#include "npu_sim/core.h"
#include <algorithm>
#include <iostream>
#include <cassert>

namespace npu_sim {

static const char* state_name(CoreState s) {
    switch (s) {
        case CoreState::IDLE:      return "IDLE";
        case CoreState::LOADING:   return "LOADING";
        case CoreState::COMPUTING: return "COMPUTING";
        case CoreState::WRITEBACK: return "WRITEBACK";
        case CoreState::DONE:      return "DONE";
    }
    return "?";
}

NPUCore::NPUCore(core_id_t id, const CoreConfig& core_cfg,
                 const SRAMConfig& sram_cfg,
                 const NetworkInterfaceConfig& ni_cfg,
                 uint32_t element_size_bits)
    : id_(id)
    , core_cfg_(core_cfg)
    , sram_cfg_(sram_cfg)
    , element_size_bits_(element_size_bits)
    , sram_(sram_cfg.size_kb * 1024,
            sram_cfg.read_bandwidth_bytes_per_cycle,
            sram_cfg.write_bandwidth_bytes_per_cycle)
    , max_outstanding_reqs_(ni_cfg.max_outstanding_reqs)
    , injection_queue_capacity_(ni_cfg.injection_queue_size)
    , ejection_queue_capacity_(ni_cfg.ejection_queue_size) {}

void NPUCore::load_workloads(std::vector<Workload>&& workloads) {
    workload_queue_ = std::move(workloads);
    current_workload_idx_ = 0;
    state_ = CoreState::IDLE;
}

void NPUCore::record_transition(CoreState old_s, CoreState new_s,
                                const std::string& detail) {
    if (!trace_enabled_) return;
    StateTraceEntry e;
    e.cycle = current_cycle_;
    e.old_state = old_s;
    e.new_state = new_s;
    e.workload_idx = static_cast<uint32_t>(current_workload_idx_);
    if (current_workload_idx_ < workload_queue_.size())
        e.layer_name = workload_queue_[current_workload_idx_].layer_name;
    e.detail = detail;
    trace_.push_back(std::move(e));
}

void NPUCore::tick(cycle_t current_cycle) {
    current_cycle_ = current_cycle;

    process_incoming_packets();
    retry_sram_allocations();
    drain_sram_writes();
    serve_pending_remote_requests();

    switch (state_) {
        case CoreState::IDLE:
            try_start_next_workload();
            if (state_ == CoreState::IDLE) {
                stats_.cycles_idle++;
            }
            break;

        case CoreState::LOADING: {
            issue_fetch_requests();
            if (all_buffers_loaded()) {
                auto old = state_;
                state_ = CoreState::COMPUTING;
                compute_remaining_ = calculate_compute_cycles();
                bool had_dram = false, had_core = false;
                if (current_workload_idx_ < workload_queue_.size()) {
                    for (const auto& buf : workload_queue_[current_workload_idx_].buffers) {
                        for (const auto& src : buf.sources) {
                            if (src.type == SourceType::DRAM) had_dram = true;
                            else had_core = true;
                        }
                    }
                }
                cycle_t loading_dram_cycles = had_dram && last_dram_ready_cycle_ > 0
                    ? (last_dram_ready_cycle_ - loading_start_cycle_) : 0;
                cycle_t loading_core_cycles = had_core && last_core_ready_cycle_ > 0
                    ? (last_core_ready_cycle_ - loading_start_cycle_) : 0;
                std::string detail = "compute_cycles=" + std::to_string(compute_remaining_);
                if (had_dram || had_core) {
                    detail += ",loading_dram_cycles=" + std::to_string(loading_dram_cycles);
                    detail += ",loading_core_cycles=" + std::to_string(loading_core_cycles);
                }
                record_transition(old, state_, detail);
            } else {
                stats_.cycles_loading++;
            }
            break;
        }

        case CoreState::COMPUTING:
            if (compute_remaining_ > 0) {
                compute_remaining_--;
                stats_.cycles_computing++;
            }
            if (compute_remaining_ == 0) {
                mark_outputs_available();
                free_consumed_buffers();

                bool needs_writeback = false;
                if (current_workload_idx_ < workload_queue_.size()) {
                    auto& wl = workload_queue_[current_workload_idx_];
                    for (auto& out : wl.outputs) {
                        for (auto& dest : out.destinations) {
                            if (dest.type == SourceType::DRAM) {
                                needs_writeback = true;
                                break;
                            }
                        }
                        if (needs_writeback) break;
                    }
                }

                if (needs_writeback) {
                    auto old = state_;
                    state_ = CoreState::WRITEBACK;
                    writeback_requests_issued_ = false;
                    writeback_pending_.clear();
                    writeback_read_remaining_ = 0;
                    auto& wl = workload_queue_[current_workload_idx_];
                    for (auto& out : wl.outputs) {
                        for (auto& dest : out.destinations) {
                            if (dest.type == SourceType::DRAM) {
                                writeback_pending_.emplace_back(out.transfer_id, out.size_bytes);
                                break;
                            }
                        }
                    }
                    if (!writeback_pending_.empty()) {
                        writeback_read_remaining_ = writeback_pending_.front().second;
                    }
                    record_transition(old, state_, "writeback_to_dram");
                } else {
                    auto old = state_;
                    stats_.workloads_completed++;
                    current_workload_idx_++;
                    state_ = CoreState::IDLE;
                    record_transition(old, state_, "workload_done");
                }
            }
            break;

        case CoreState::WRITEBACK:
            issue_writeback_requests();
            if (all_writebacks_complete()) {
                auto old = state_;
                stats_.workloads_completed++;
                current_workload_idx_++;
                state_ = CoreState::IDLE;
                record_transition(old, state_, "writeback_complete");
            } else {
                stats_.cycles_writeback++;
            }
            break;

        case CoreState::DONE:
            break;
    }

    update_sram_stats();
}

void NPUCore::receive_packet(const Packet& pkt) {
    ejection_queue_.push(pkt);
}

bool NPUCore::has_outgoing_packet() const {
    return !injection_queue_.empty();
}

Packet NPUCore::pop_outgoing_packet() {
    Packet pkt = injection_queue_.front();
    injection_queue_.pop();
    return pkt;
}

// --- private ---

void NPUCore::process_incoming_packets() {
    while (!ejection_queue_.empty()) {
        Packet pkt = ejection_queue_.front();
        ejection_queue_.pop();

        switch (pkt.type) {
            case PacketType::READ_RESPONSE: {
                uint32_t ref_count = ref_count_for_input(pkt.transfer_id);
                if (!sram_.allocate(pkt.transfer_id, pkt.data_size_bytes, DataType::IFMAP, ref_count)) {
                    sram_retry_queue_.push_back(pkt);
                    break;
                }
                stats_.total_data_loaded_bytes += pkt.data_size_bytes;
                if (sram_write_bytes_remaining_ == 0) {
                    sram_write_current_id_ = pkt.transfer_id;
                    sram_write_bytes_remaining_ = pkt.data_size_bytes;
                } else {
                    sram_write_queue_.emplace_back(pkt.transfer_id, pkt.data_size_bytes);
                }
                break;
            }
            case PacketType::READ_REQUEST: {
                if (sram_.is_ready(pkt.transfer_id)) {
                    Packet resp;
                    resp.type = PacketType::READ_RESPONSE;
                    resp.src = id_;
                    resp.dst = pkt.src;
                    resp.transfer_id = pkt.transfer_id;
                    resp.data_size_bytes = pkt.data_size_bytes;
                    resp.inject_cycle = current_cycle_;
                    injection_queue_.push(resp);
                    sram_.release_ref(pkt.transfer_id);
                } else {
                    pending_remote_requests_.push_back(pkt);
                }
                break;
            }
            case PacketType::WRITE_RESPONSE: {
                pending_writebacks_.erase(pkt.transfer_id);
                sram_.release_ref(pkt.transfer_id);
                break;
            }
            default:
                break;
        }
    }
}

void NPUCore::serve_pending_remote_requests() {
    auto it = pending_remote_requests_.begin();
    while (it != pending_remote_requests_.end()) {
        if (sram_.is_ready(it->transfer_id)) {
            if (injection_queue_.size() < injection_queue_capacity_) {
                Packet resp;
                resp.type = PacketType::READ_RESPONSE;
                resp.src = id_;
                resp.dst = it->src;
                resp.transfer_id = it->transfer_id;
                resp.data_size_bytes = it->data_size_bytes;
                resp.inject_cycle = current_cycle_;
                injection_queue_.push(resp);
                sram_.release_ref(it->transfer_id);
                it = pending_remote_requests_.erase(it);
            } else {
                stats_.cycles_stall_noc++;
                ++it;
            }
        } else {
            ++it;
        }
    }
}

void NPUCore::try_start_next_workload() {
    if (current_workload_idx_ >= workload_queue_.size()) {
        auto old = state_;
        state_ = CoreState::DONE;
        record_transition(old, state_, "all_workloads_finished");
        return;
    }

    auto& wl = workload_queue_[current_workload_idx_];
    pending_loads_.clear();
    requested_transfers_.clear();

    for (auto& buf : wl.buffers) {
        for (auto& src : buf.sources) {
            pending_loads_.insert(src.transfer_id);
        }
    }

    if (pending_loads_.empty()) {
        auto old = state_;
        state_ = CoreState::COMPUTING;
        compute_remaining_ = calculate_compute_cycles();
        record_transition(old, state_,
            "no_deps;compute_cycles=" + std::to_string(compute_remaining_));
    } else {
        auto old = state_;
        state_ = CoreState::LOADING;
        loading_start_cycle_ = current_cycle_;
        last_dram_ready_cycle_ = 0;
        last_core_ready_cycle_ = 0;
        sram_write_bytes_remaining_ = 0;
        sram_write_queue_.clear();

        if (trace_enabled_) {
            std::ostringstream oss;
            oss << "waiting_for=[";
            bool first = true;
            for (auto& buf : wl.buffers) {
                for (auto& src : buf.sources) {
                    if (!first) oss << ",";
                    first = false;
                    oss << "t" << src.transfer_id << "(";
                    oss << (src.type == SourceType::DRAM ? "DRAM" :
                            "C" + std::to_string(src.source_id));
                    oss << "," << src.size_bytes << "B)";
                }
            }
            oss << "]";
            record_transition(old, state_, oss.str());
        }
    }
}

void NPUCore::issue_fetch_requests() {
    if (current_workload_idx_ >= workload_queue_.size()) return;
    auto& wl = workload_queue_[current_workload_idx_];

    for (auto& buf : wl.buffers) {
        for (auto& src : buf.sources) {
            if (requested_transfers_.count(src.transfer_id)) continue;

            // Already in local SRAM (e.g., output from a previous local workload)
            if (sram_.is_ready(src.transfer_id)) {
                pending_loads_.erase(src.transfer_id);
                requested_transfers_.insert(src.transfer_id);
                continue;
            }

            if (injection_queue_.size() >= injection_queue_capacity_) {
                stats_.cycles_stall_noc++;
                return;
            }

            Packet req;
            req.type = PacketType::READ_REQUEST;
            req.src = id_;
            req.dst = (src.type == SourceType::CORE) ? src.source_id : DRAM_ID;
            req.transfer_id = src.transfer_id;
            req.data_size_bytes = src.size_bytes;
            req.inject_cycle = current_cycle_;
            injection_queue_.push(req);
            requested_transfers_.insert(src.transfer_id);
        }
    }
}

bool NPUCore::all_buffers_loaded() const {
    return pending_loads_.empty();
}

cycle_t NPUCore::calculate_compute_cycles() const {
    if (current_workload_idx_ >= workload_queue_.size()) return 0;
    auto& wl = workload_queue_[current_workload_idx_];
    return wl.compute_cycles(core_cfg_.mac_units, core_cfg_.vector_units,
                             element_size_bits_,
                             core_cfg_.use_analytical_time);
}

void NPUCore::mark_outputs_available() {
    if (current_workload_idx_ >= workload_queue_.size()) return;
    auto& wl = workload_queue_[current_workload_idx_];

    for (auto& out : wl.outputs) {
        uint32_t ref_count = 0;
        bool has_dram_dest = false;
        for (auto& dest : out.destinations) {
            if (dest.type == SourceType::CORE) {
                ref_count++;
            } else if (dest.type == SourceType::DRAM) {
                has_dram_dest = true;
            }
        }
        ref_count += count_local_future_uses(out.transfer_id);
        if (has_dram_dest) ref_count++;

        sram_.allocate(out.transfer_id, out.size_bytes, DataType::OFMAP, ref_count);
        sram_.mark_ready(out.transfer_id);
    }
}

void NPUCore::free_consumed_buffers() {
    if (current_workload_idx_ >= workload_queue_.size()) return;
    auto& wl = workload_queue_[current_workload_idx_];

    for (auto& buf : wl.buffers) {
        for (auto& src : buf.sources) {
            sram_.release_ref(src.transfer_id);
        }
    }
}

void NPUCore::issue_writeback_requests() {
    if (current_workload_idx_ >= workload_queue_.size()) return;
    if (writeback_pending_.empty()) {
        writeback_requests_issued_ = true;
        return;
    }

    uint64_t read_bw = effective_read_bw();
    if (writeback_read_remaining_ > 0) {
        if (read_bw > 0) {
            uint64_t to_read = (writeback_read_remaining_ > read_bw) ? read_bw : writeback_read_remaining_;
            writeback_read_remaining_ -= to_read;
        } else {
            writeback_read_remaining_ = 0;
        }
    }

    if (writeback_read_remaining_ == 0) {
        if (injection_queue_.size() >= injection_queue_capacity_) {
            stats_.cycles_stall_noc++;
            return;
        }
        transfer_id_t tid = writeback_pending_.front().first;
        uint64_t sz = writeback_pending_.front().second;
        writeback_pending_.pop_front();
        Packet pkt;
        pkt.type = PacketType::WRITE_REQUEST;
        pkt.src = id_;
        pkt.dst = DRAM_ID;
        pkt.transfer_id = tid;
        pkt.data_size_bytes = sz;
        pkt.inject_cycle = current_cycle_;
        injection_queue_.push(pkt);
        pending_writebacks_.insert(tid);
        stats_.total_data_stored_bytes += sz;

        if (!writeback_pending_.empty()) {
            writeback_read_remaining_ = writeback_pending_.front().second;
        } else {
            writeback_requests_issued_ = true;
        }
    }
}

bool NPUCore::all_writebacks_complete() const {
    return writeback_requests_issued_ && pending_writebacks_.empty();
}

void NPUCore::update_sram_stats() {
    if (sram_.used() > stats_.peak_sram_usage_bytes) {
        stats_.peak_sram_usage_bytes = sram_.used();
    }
}

bool NPUCore::get_source_type_for_transfer(transfer_id_t id, SourceType& out) const {
    if (current_workload_idx_ >= workload_queue_.size()) return false;
    for (const auto& buf : workload_queue_[current_workload_idx_].buffers) {
        for (const auto& src : buf.sources) {
            if (src.transfer_id == id) {
                out = src.type;
                return true;
            }
        }
    }
    return false;
}

void NPUCore::on_pending_load_ready(transfer_id_t id) {
    SourceType t;
    if (get_source_type_for_transfer(id, t)) {
        if (t == SourceType::DRAM) {
            if (current_cycle_ > last_dram_ready_cycle_) last_dram_ready_cycle_ = current_cycle_;
        } else {
            if (current_cycle_ > last_core_ready_cycle_) last_core_ready_cycle_ = current_cycle_;
        }
    }
}

void NPUCore::drain_sram_writes() {
    uint64_t write_bw = effective_write_bw();
    if (write_bw == 0) {
        while (sram_write_bytes_remaining_ > 0 || !sram_write_queue_.empty()) {
            if (sram_write_bytes_remaining_ > 0) {
                on_pending_load_ready(sram_write_current_id_);
                pending_loads_.erase(sram_write_current_id_);
                sram_.mark_ready(sram_write_current_id_);
                sram_write_bytes_remaining_ = 0;
            }
            if (!sram_write_queue_.empty()) {
                sram_write_current_id_ = sram_write_queue_.front().first;
                sram_write_bytes_remaining_ = sram_write_queue_.front().second;
                sram_write_queue_.pop_front();
            }
        }
        return;
    }
    while (write_bw > 0 && (sram_write_bytes_remaining_ > 0 || !sram_write_queue_.empty())) {
        if (sram_write_bytes_remaining_ == 0) {
            if (sram_write_queue_.empty()) break;
            sram_write_current_id_ = sram_write_queue_.front().first;
            sram_write_bytes_remaining_ = sram_write_queue_.front().second;
            sram_write_queue_.pop_front();
        }
        uint64_t to_write = (sram_write_bytes_remaining_ > write_bw) ? write_bw : sram_write_bytes_remaining_;
        sram_write_bytes_remaining_ -= to_write;
        write_bw -= to_write;
        if (sram_write_bytes_remaining_ == 0) {
            on_pending_load_ready(sram_write_current_id_);
            pending_loads_.erase(sram_write_current_id_);
            sram_.mark_ready(sram_write_current_id_);
        }
    }
}

uint64_t NPUCore::effective_write_bw() const {
    if (current_workload_is_pe()) {
        if (sram_cfg_.pe_write_bandwidth_bytes_per_cycle > 0)
            return sram_cfg_.pe_write_bandwidth_bytes_per_cycle;
    } else {
        if (sram_cfg_.vp_write_bandwidth_bytes_per_cycle > 0)
            return sram_cfg_.vp_write_bandwidth_bytes_per_cycle;
    }
    return sram_cfg_.write_bandwidth_bytes_per_cycle;
}

uint64_t NPUCore::effective_read_bw() const {
    if (current_workload_is_pe()) {
        if (sram_cfg_.pe_read_bandwidth_bytes_per_cycle > 0)
            return sram_cfg_.pe_read_bandwidth_bytes_per_cycle;
    } else {
        if (sram_cfg_.vp_read_bandwidth_bytes_per_cycle > 0)
            return sram_cfg_.vp_read_bandwidth_bytes_per_cycle;
    }
    return sram_cfg_.read_bandwidth_bytes_per_cycle;
}

uint32_t NPUCore::ref_count_for_input(transfer_id_t id) const {
    if (current_workload_idx_ >= workload_queue_.size()) return 0;
    uint32_t count = 0;
    for (const auto& buf : workload_queue_[current_workload_idx_].buffers) {
        for (const auto& src : buf.sources) {
            if (src.transfer_id == id) count++;
        }
    }
    return count > 0 ? count : 1;
}

bool NPUCore::current_workload_is_pe() const {
    if (current_workload_idx_ >= workload_queue_.size()) return false;
    OperatorType t = workload_queue_[current_workload_idx_].op_type;
    return t == OperatorType::CONV2D || t == OperatorType::FC;
}

std::vector<transfer_id_t> NPUCore::pending_load_ids() const {
    return std::vector<transfer_id_t>(pending_loads_.begin(), pending_loads_.end());
}

void NPUCore::retry_sram_allocations() {
    auto it = sram_retry_queue_.begin();
    while (it != sram_retry_queue_.end()) {
        uint32_t ref_count = ref_count_for_input(it->transfer_id);
        if (sram_.allocate(it->transfer_id, it->data_size_bytes, DataType::IFMAP, ref_count)) {
            stats_.total_data_loaded_bytes += it->data_size_bytes;
            if (sram_write_bytes_remaining_ == 0) {
                sram_write_current_id_ = it->transfer_id;
                sram_write_bytes_remaining_ = it->data_size_bytes;
            } else {
                sram_write_queue_.emplace_back(it->transfer_id, it->data_size_bytes);
            }
            it = sram_retry_queue_.erase(it);
        } else {
            ++it;
        }
    }
}

uint32_t NPUCore::count_local_future_uses(transfer_id_t tid) const {
    uint32_t count = 0;
    for (size_t i = current_workload_idx_ + 1; i < workload_queue_.size(); i++) {
        for (auto& buf : workload_queue_[i].buffers) {
            for (auto& src : buf.sources) {
                if (src.transfer_id == tid &&
                    src.type == SourceType::CORE && src.source_id == id_) {
                    count++;
                }
            }
        }
    }
    return count;
}

std::vector<NPUCore::WaitInfo> NPUCore::get_pending_wait_info() const {
    std::vector<WaitInfo> result;
    if (state_ != CoreState::LOADING || current_workload_idx_ >= workload_queue_.size())
        return result;
    auto& wl = workload_queue_[current_workload_idx_];
    for (auto& buf : wl.buffers) {
        for (auto& src : buf.sources) {
            if (pending_loads_.count(src.transfer_id)) {
                WaitInfo w;
                w.source_core = (src.type == SourceType::DRAM) ? DRAM_ID : src.source_id;
                w.transfer_id = src.transfer_id;
                result.push_back(w);
            }
        }
    }
    return result;
}

void NPUCore::force_advance_loading() {
    if (state_ != CoreState::LOADING) return;
    auto old = state_;
    pending_loads_.clear();
    sram_retry_queue_.clear();
    sram_write_bytes_remaining_ = 0;
    sram_write_queue_.clear();
    state_ = CoreState::COMPUTING;
    compute_remaining_ = calculate_compute_cycles();
    record_transition(old, state_,
        "FORCE_ADVANCE_DEADLOCK;compute_cycles=" + std::to_string(compute_remaining_));
}

void NPUCore::force_complete_writeback() {
    if (state_ != CoreState::WRITEBACK) return;
    auto old = state_;
    pending_writebacks_.clear();
    writeback_pending_.clear();
    writeback_requests_issued_ = true;
    writeback_read_remaining_ = 0;
    stats_.workloads_completed++;
    current_workload_idx_++;
    state_ = CoreState::IDLE;
    record_transition(old, state_, "FORCE_COMPLETE_WRITEBACK_DEADLOCK");
}

}  // namespace npu_sim

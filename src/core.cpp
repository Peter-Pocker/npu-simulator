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
                record_transition(old, state_,
                    "compute_cycles=" + std::to_string(compute_remaining_));
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
                // Data arrived — store in SRAM and remove from pending loads
                sram_.allocate(pkt.transfer_id, pkt.data_size_bytes, DataType::IFMAP);
                sram_.mark_ready(pkt.transfer_id);
                pending_loads_.erase(pkt.transfer_id);
                stats_.total_data_loaded_bytes += pkt.data_size_bytes;
                break;
            }
            case PacketType::READ_REQUEST: {
                // Another core is requesting data from our SRAM
                if (sram_.is_ready(pkt.transfer_id)) {
                    Packet resp;
                    resp.type = PacketType::READ_RESPONSE;
                    resp.src = id_;
                    resp.dst = pkt.src;
                    resp.transfer_id = pkt.transfer_id;
                    resp.data_size_bytes = pkt.data_size_bytes;
                    resp.inject_cycle = current_cycle_;
                    injection_queue_.push(resp);
                } else {
                    pending_remote_requests_.push_back(pkt);
                }
                break;
            }
            case PacketType::WRITE_RESPONSE: {
                pending_writebacks_.erase(pkt.transfer_id);
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
                             element_size_bits_);
}

void NPUCore::mark_outputs_available() {
    if (current_workload_idx_ >= workload_queue_.size()) return;
    auto& wl = workload_queue_[current_workload_idx_];

    for (auto& out : wl.outputs) {
        uint32_t ref_count = 0;
        for (auto& dest : out.destinations) {
            if (dest.type == SourceType::CORE) {
                ref_count++;
            }
        }

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
    if (writeback_requests_issued_) return;
    if (current_workload_idx_ >= workload_queue_.size()) return;

    auto& wl = workload_queue_[current_workload_idx_];
    for (auto& out : wl.outputs) {
        for (auto& dest : out.destinations) {
            if (dest.type == SourceType::DRAM) {
                if (injection_queue_.size() >= injection_queue_capacity_) {
                    stats_.cycles_stall_noc++;
                    return;
                }
                Packet pkt;
                pkt.type = PacketType::WRITE_REQUEST;
                pkt.src = id_;
                pkt.dst = DRAM_ID;
                pkt.transfer_id = out.transfer_id;
                pkt.data_size_bytes = out.size_bytes;
                pkt.inject_cycle = current_cycle_;
                injection_queue_.push(pkt);
                pending_writebacks_.insert(out.transfer_id);
                stats_.total_data_stored_bytes += out.size_bytes;
            }
        }
    }
    writeback_requests_issued_ = true;
}

bool NPUCore::all_writebacks_complete() const {
    return writeback_requests_issued_ && pending_writebacks_.empty();
}

void NPUCore::update_sram_stats() {
    if (sram_.used() > stats_.peak_sram_usage_bytes) {
        stats_.peak_sram_usage_bytes = sram_.used();
    }
}

}  // namespace npu_sim

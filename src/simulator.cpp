#include "npu_sim/simulator.h"
#include "npu_sim/ir_parser.h"
#include "npu_sim/gemini_parser.h"
#include "npu_sim/booksim2_noc.h"
#include "npu_sim/ramulator2_dram.h"

#include <iostream>
#include <iomanip>
#include <fstream>
#include <algorithm>
#include <cassert>
#include <filesystem>

namespace npu_sim {

// ── SimpleNoC (stub) ──

void SimpleNoC::init(uint32_t num_nodes, const NoCConfig& config) {
    num_nodes_ = num_nodes;
    config_ = config;
    mesh_width_ = static_cast<uint32_t>(std::ceil(std::sqrt(num_nodes)));
}

bool SimpleNoC::inject(const Packet& pkt) {
    Packet p = pkt;
    p.inject_cycle = cycle_;
    p.deliver_cycle = cycle_ + calc_latency(pkt.src, pkt.dst);
    in_flight_.push_back(p);
    return true;
}

void SimpleNoC::tick() {
    cycle_++;
}

std::vector<Packet> SimpleNoC::get_delivered_packets(core_id_t node_id) {
    std::vector<Packet> delivered;
    auto it = in_flight_.begin();
    while (it != in_flight_.end()) {
        if (it->dst == node_id && it->deliver_cycle <= cycle_) {
            delivered.push_back(*it);
            it = in_flight_.erase(it);
        } else {
            ++it;
        }
    }
    return delivered;
}

bool SimpleNoC::can_inject(core_id_t /*node_id*/) const {
    return true;
}

uint32_t SimpleNoC::calc_latency(core_id_t src, core_id_t dst) const {
    if (mesh_width_ == 0) return config_.router_latency_cycles;
    uint32_t sx = static_cast<uint32_t>(src) % mesh_width_;
    uint32_t sy = static_cast<uint32_t>(src) / mesh_width_;
    uint32_t dx = static_cast<uint32_t>(dst) % mesh_width_;
    uint32_t dy = static_cast<uint32_t>(dst) / mesh_width_;
    uint32_t hops = (sx > dx ? sx - dx : dx - sx) + (sy > dy ? sy - dy : dy - sy);
    return hops * config_.hop_latency_cycles + config_.router_latency_cycles;
}

std::unique_ptr<NetworkInterface> NetworkInterface::create_simple(
        uint32_t num_nodes, const NoCConfig& config) {
    auto noc = std::make_unique<SimpleNoC>();
    noc->init(num_nodes, config);
    return noc;
}

std::unique_ptr<NetworkInterface> NetworkInterface::create(
        uint32_t num_nodes, const NoCConfig& config) {
    if (config.backend == "booksim2") {
        auto noc = std::make_unique<BookSim2NoC>();
        noc->init(num_nodes, config);
        return noc;
    }
    return create_simple(num_nodes, config);
}

// ── SimpleDRAM (stub) ──

void SimpleDRAM::init(const DRAMConfig& config) {
    config_ = config;
}

bool SimpleDRAM::send_request(const Packet& pkt) {
    Packet p = pkt;
    p.inject_cycle = cycle_;
    cycle_t latency = (pkt.type == PacketType::READ_REQUEST)
                        ? DEFAULT_READ_LATENCY : DEFAULT_WRITE_LATENCY;
    p.deliver_cycle = cycle_ + latency;

    if (pkt.type == PacketType::READ_REQUEST) {
        p.type = PacketType::READ_RESPONSE;
    } else {
        p.type = PacketType::WRITE_RESPONSE;
    }
    std::swap(p.src, p.dst);

    in_flight_.push_back(p);
    return true;
}

void SimpleDRAM::tick() {
    cycle_++;
}

std::vector<Packet> SimpleDRAM::get_responses() {
    std::vector<Packet> responses;
    auto it = in_flight_.begin();
    while (it != in_flight_.end()) {
        if (it->deliver_cycle <= cycle_) {
            responses.push_back(*it);
            it = in_flight_.erase(it);
        } else {
            ++it;
        }
    }
    return responses;
}

std::unique_ptr<MemoryInterface> MemoryInterface::create_simple(const DRAMConfig& config) {
    auto dram = std::make_unique<SimpleDRAM>();
    dram->init(config);
    return dram;
}

std::unique_ptr<MemoryInterface> MemoryInterface::create(const DRAMConfig& config) {
    if (config.backend == "ramulator2") {
        auto dram = std::make_unique<Ramulator2DRAM>();
        dram->init(config);
        return dram;
    }
    return create_simple(config);
}

// ── Simulator ──

SimConfig SimConfig::load_from_json(const std::string& path) {
    std::ifstream ifs(path);
    if (!ifs.is_open()) {
        throw std::runtime_error("Cannot open config file: " + path);
    }
    nlohmann::json j;
    ifs >> j;
    return j.get<SimConfig>();
}

Simulator::Simulator(const SimConfig& config)
    : config_(config) {}

void Simulator::load_ir(const std::string& path) {
    auto parser = IRParser::create("gemini");
    ir_data_ = parser->parse(path);

    if (config_.num_cores_x == 0) config_.num_cores_x = ir_data_.num_cores_x;
    if (config_.num_cores_y == 0) config_.num_cores_y = ir_data_.num_cores_y;

    if (ir_data_.required_sram_bytes > 0) {
        uint32_t required_kb = static_cast<uint32_t>(
            (ir_data_.required_sram_bytes + 1023) / 1024);
        if (required_kb > config_.sram.size_kb) {
            std::cout << "[Simulator] SRAM auto-adjusted: "
                      << config_.sram.size_kb << " KB -> "
                      << required_kb << " KB (from IR buffersize)\n";
            config_.sram.size_kb = required_kb;
        }
    }

    std::cout << "[Simulator] Loaded IR: "
              << config_.num_cores_x << "x" << config_.num_cores_y
              << " cores, top_batch_cut=" << ir_data_.top_batch_cut << "\n";

    uint32_t total_workloads = 0;
    for (auto& cw : ir_data_.core_workloads) {
        total_workloads += static_cast<uint32_t>(cw.size());
    }
    std::cout << "[Simulator] Total workloads: " << total_workloads
              << ", DRAM reads: " << ir_data_.dram_reads.size()
              << ", DRAM writes: " << ir_data_.dram_writes.size() << "\n";

    init_cores();
}

void Simulator::init_cores() {
    uint32_t num_cores = config_.num_cores_y * (config_.num_cores_x + 2);
    cores_.reserve(num_cores);

    for (uint32_t i = 0; i < num_cores; ++i) {
        cores_.emplace_back(static_cast<core_id_t>(i),
                            config_.core, config_.sram, config_.ni,
                            config_.element_size_bits);
        if (trace_enabled_) {
            cores_.back().set_trace_enabled(true);
        }
    }

    for (uint32_t i = 0; i < num_cores && i < ir_data_.core_workloads.size(); ++i) {
        if (!ir_data_.core_workloads[i].empty()) {
            cores_[i].load_workloads(std::move(ir_data_.core_workloads[i]));
        }
    }

    noc_ = NetworkInterface::create(num_cores, config_.noc);
    dram_ = MemoryInterface::create(config_.dram);
}

void Simulator::run() {
    std::cout << "[Simulator] Starting simulation...\n";
    current_cycle_ = 0;

    while (!all_cores_done()) {
        tick();
        current_cycle_++;

        if (current_cycle_ % 100000 == 0) {
            uint32_t done_count = 0;
            for (auto& c : cores_) {
                if (c.is_done()) done_count++;
            }
            std::cout << "[Simulator] Cycle " << current_cycle_
                      << ", cores done: " << done_count << "/" << cores_.size() << "\n";
        }
    }

    sim_stats_.total_cycles = current_cycle_;
    std::cout << "[Simulator] Simulation complete at cycle " << current_cycle_ << "\n";
}

void Simulator::tick() {
    // 1. Each core ticks
    for (auto& core : cores_) {
        core.tick(current_cycle_);
    }

    // 2. Collect outgoing packets from cores -> inject into NoC or DRAM
    collect_core_packets();

    // 3. Advance NoC and DRAM
    noc_->tick();
    dram_->tick();

    // 4. Deliver NoC packets to cores
    deliver_noc_packets();

    // 5. Deliver DRAM responses to cores
    deliver_dram_responses();
}

bool Simulator::all_cores_done() const {
    for (auto& core : cores_) {
        if (!core.is_done()) return false;
    }
    return true;
}

void Simulator::collect_core_packets() {
    for (auto& core : cores_) {
        while (core.has_outgoing_packet()) {
            Packet pkt = core.pop_outgoing_packet();

            if (pkt.dst == DRAM_ID) {
                dram_->send_request(pkt);
                if (pkt.type == PacketType::READ_REQUEST) {
                    sim_stats_.total_dram_reads++;
                } else {
                    sim_stats_.total_dram_writes++;
                }
            } else {
                noc_->inject(pkt);
                sim_stats_.total_noc_packets++;
            }
        }
    }
}

void Simulator::deliver_noc_packets() {
    for (auto& core : cores_) {
        auto packets = noc_->get_delivered_packets(core.id());
        for (auto& pkt : packets) {
            core.receive_packet(pkt);
        }
    }
}

void Simulator::deliver_dram_responses() {
    auto responses = dram_->get_responses();
    for (auto& pkt : responses) {
        if (pkt.dst >= 0 && pkt.dst < static_cast<core_id_t>(cores_.size())) {
            cores_[pkt.dst].receive_packet(pkt);
        }
    }
}

void Simulator::print_stats() const {
    std::cout << "\n========== Simulation Statistics ==========\n";
    std::cout << "Total cycles:       " << sim_stats_.total_cycles << "\n";
    std::cout << "NoC packets:        " << sim_stats_.total_noc_packets << "\n";
    std::cout << "DRAM reads:         " << sim_stats_.total_dram_reads << "\n";
    std::cout << "DRAM writes:        " << sim_stats_.total_dram_writes << "\n";

    std::cout << "\n--- Per-Core Statistics ---\n";
    std::cout << std::setw(6) << "Core"
              << std::setw(12) << "Idle"
              << std::setw(12) << "Loading"
              << std::setw(12) << "Compute"
              << std::setw(12) << "Writeback"
              << std::setw(12) << "StallNoC"
              << std::setw(10) << "Workloads"
              << std::setw(14) << "PeakSRAM(KB)"
              << "\n";

    for (auto& core : cores_) {
        auto& s = core.stats();
        if (s.workloads_completed == 0 && s.cycles_idle == 0) continue;
        std::cout << std::setw(6) << core.id()
                  << std::setw(12) << s.cycles_idle
                  << std::setw(12) << s.cycles_loading
                  << std::setw(12) << s.cycles_computing
                  << std::setw(12) << s.cycles_writeback
                  << std::setw(12) << s.cycles_stall_noc
                  << std::setw(10) << s.workloads_completed
                  << std::setw(14) << std::fixed << std::setprecision(1)
                  << (s.peak_sram_usage_bytes / 1024.0)
                  << "\n";
    }
    std::cout << "=============================================\n";
}

void Simulator::export_trace() const {
    if (!trace_enabled_) return;
    std::filesystem::create_directories(trace_output_path_);
    export_trace_csv();
    export_workload_summary();
    std::cout << "\n[Trace] Files written to " << trace_output_path_ << "/\n";
}

static const char* state_str(CoreState s) {
    switch (s) {
        case CoreState::IDLE:      return "IDLE";
        case CoreState::LOADING:   return "LOADING";
        case CoreState::COMPUTING: return "COMPUTING";
        case CoreState::WRITEBACK: return "WRITEBACK";
        case CoreState::DONE:      return "DONE";
    }
    return "?";
}

void Simulator::export_trace_csv() const {
    std::string path = trace_output_path_ + "/state_trace.csv";
    std::ofstream ofs(path);
    ofs << "cycle,core_id,old_state,new_state,workload_idx,layer_name,detail\n";

    struct MergedEntry {
        cycle_t cycle;
        core_id_t core_id;
        const StateTraceEntry* entry;
    };
    std::vector<MergedEntry> all;

    for (auto& core : cores_) {
        for (auto& e : core.trace()) {
            all.push_back({e.cycle, core.id(), &e});
        }
    }
    std::sort(all.begin(), all.end(),
              [](const MergedEntry& a, const MergedEntry& b) {
                  return a.cycle < b.cycle ||
                         (a.cycle == b.cycle && a.core_id < b.core_id);
              });

    for (auto& m : all) {
        auto& e = *m.entry;
        ofs << e.cycle << ","
            << m.core_id << ","
            << state_str(e.old_state) << ","
            << state_str(e.new_state) << ","
            << e.workload_idx << ","
            << e.layer_name << ","
            << "\"" << e.detail << "\"\n";
    }
    std::cout << "[Trace] State transitions: " << path
              << " (" << all.size() << " events)\n";
}

void Simulator::export_workload_summary() const {
    std::string path = trace_output_path_ + "/workload_summary.csv";
    std::ofstream ofs(path);
    ofs << "core_id,workload_idx,layer_name,op_type,"
        << "start_cycle,loading_done_cycle,compute_done_cycle,end_cycle,"
        << "loading_cycles,compute_cycles,writeback_cycles,"
        << "data_sources\n";

    for (auto& core : cores_) {
        auto& trace = core.trace();
        if (trace.empty()) continue;

        struct WlPhase {
            uint32_t wl_idx = 0;
            std::string layer_name;
            std::string op_type;
            cycle_t start = 0;
            cycle_t loading_done = 0;
            cycle_t compute_done = 0;
            cycle_t end = 0;
            std::string data_sources;
        };

        std::vector<WlPhase> phases;
        WlPhase cur;
        bool in_wl = false;

        for (auto& e : trace) {
            if (e.new_state == CoreState::LOADING) {
                if (in_wl && cur.wl_idx != e.workload_idx) {
                    phases.push_back(cur);
                    cur = WlPhase{};
                }
                cur.wl_idx = e.workload_idx;
                cur.layer_name = e.layer_name;
                cur.start = e.cycle;
                cur.data_sources = e.detail;
                in_wl = true;
            } else if (e.new_state == CoreState::COMPUTING) {
                if (!in_wl) {
                    cur.wl_idx = e.workload_idx;
                    cur.layer_name = e.layer_name;
                    cur.start = e.cycle;
                    cur.data_sources = e.detail;
                    in_wl = true;
                }
                cur.loading_done = e.cycle;
            } else if (e.new_state == CoreState::WRITEBACK) {
                cur.compute_done = e.cycle;
            } else if ((e.new_state == CoreState::IDLE ||
                        e.new_state == CoreState::DONE) && in_wl) {
                cur.end = e.cycle;
                if (cur.compute_done == 0) cur.compute_done = e.cycle;
                if (cur.loading_done == 0) cur.loading_done = cur.start;
                phases.push_back(cur);
                cur = WlPhase{};
                in_wl = false;
            }
        }
        if (in_wl) {
            if (cur.end == 0) cur.end = sim_stats_.total_cycles;
            phases.push_back(cur);
        }

        for (auto& p : phases) {
            cycle_t load_t = (p.loading_done > p.start) ? p.loading_done - p.start : 0;
            cycle_t comp_t = (p.compute_done > p.loading_done) ? p.compute_done - p.loading_done : 0;
            cycle_t wb_t = (p.end > p.compute_done) ? p.end - p.compute_done : 0;

            ofs << core.id() << ","
                << p.wl_idx << ","
                << p.layer_name << ","
                << p.op_type << ","
                << p.start << ","
                << p.loading_done << ","
                << p.compute_done << ","
                << p.end << ","
                << load_t << ","
                << comp_t << ","
                << wb_t << ","
                << "\"" << p.data_sources << "\"\n";
        }
    }
    std::cout << "[Trace] Workload summary: " << path << "\n";
}

}  // namespace npu_sim

#include "npu_sim/simulator.h"
#include "npu_sim/ir_parser.h"
#include "npu_sim/gemini_parser.h"
#include "npu_sim/booksim2_noc.h"
#include "npu_sim/ramulator2_dram.h"
#include "npu_sim/address_allocator.h"

#include <iostream>
#include <iomanip>
#include <fstream>
#include <algorithm>
#include <cassert>
#include <filesystem>

namespace npu_sim {

// ── SimpleNoC (stub) ──

void SimpleNoC::init(uint32_t num_nodes, const NoCConfig& config,
                     uint32_t mesh_width, uint32_t mesh_height) {
    num_nodes_ = num_nodes;
    config_ = config;
    if (mesh_width > 0) {
        mesh_width_ = mesh_width;
    } else {
        mesh_width_ = static_cast<uint32_t>(std::ceil(std::sqrt(num_nodes)));
    }
}

bool SimpleNoC::inject(const Packet& pkt) {
    Packet p = pkt;
    p.inject_cycle = cycle_;
    double ratio = config_.clock_ratio > 0.0 ? config_.clock_ratio : 1.0;
    uint64_t latency_global = static_cast<uint64_t>(
        calc_latency(pkt.src, pkt.dst) * ratio + 0.5);
    if (latency_global < 1) latency_global = 1;
    p.deliver_cycle = cycle_ + latency_global;
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
        uint32_t num_nodes, const NoCConfig& config,
        uint32_t mesh_width, uint32_t mesh_height) {
    auto noc = std::make_unique<SimpleNoC>();
    noc->init(num_nodes, config, mesh_width, mesh_height);
    return noc;
}

std::unique_ptr<NetworkInterface> NetworkInterface::create(
        uint32_t num_nodes, const NoCConfig& config,
        uint32_t mesh_width, uint32_t mesh_height) {
    if (config.backend == "booksim2") {
        auto noc = std::make_unique<BookSim2NoC>();
        noc->init(num_nodes, config, mesh_width, mesh_height);
        return noc;
    }
    return create_simple(num_nodes, config, mesh_width, mesh_height);
}

// ── SimpleDRAM (stub) ──

void SimpleDRAM::init(const DRAMConfig& config) {
    config_ = config;
}

bool SimpleDRAM::send_request(const Packet& pkt) {
    Packet p = pkt;
    p.inject_cycle = cycle_;
    double ratio = config_.clock_ratio > 0.0 ? config_.clock_ratio : 1.0;
    cycle_t base_latency = (pkt.type == PacketType::READ_REQUEST)
                        ? DEFAULT_READ_LATENCY : DEFAULT_WRITE_LATENCY;
    uint64_t latency_global = static_cast<uint64_t>(base_latency * ratio + 0.5);
    if (latency_global < 1) latency_global = 1;
    p.deliver_cycle = cycle_ + latency_global;

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
    : config_(config), log_dram_(config.log_dram) {}

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

    topology_.build(config_.num_cores_x, config_.num_cores_y, config_.topology);
    topology_.print_summary();

    addr_allocator_ = DRAMAddressAllocator(config_.dram.resolved_cache_line_size());
    addr_allocator_.allocate(ir_data_);
    addr_allocator_.print_summary();

    std::cout << "[Simulator] Loaded IR: "
              << config_.num_cores_x << "x" << config_.num_cores_y
              << " cores, top_batch_cut=" << ir_data_.top_batch_cut << "\n";

    uint32_t total_workloads = 0;
    for (auto& cw : ir_data_.core_workloads) {
        total_workloads += static_cast<uint32_t>(cw.size());
    }
    sim_stats_.total_workloads = total_workloads;
    std::cout << "[Simulator] Total workloads: " << total_workloads
              << ", DRAM reads: " << ir_data_.dram_reads.size()
              << ", DRAM writes: " << ir_data_.dram_writes.size() << "\n";

    init_cores();
}

void Simulator::init_cores() {
    uint32_t num_cores = topology_.num_compute_cores();
    if (num_cores == 0) num_cores = 1;
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

    uint32_t noc_nodes = topology_.total_noc_nodes();
    noc_ = NetworkInterface::create(noc_nodes, config_.noc,
                                    topology_.mesh_width(), topology_.mesh_height());
    if (config_.log_dram) {
        config_.dram.log_dram = true;
    }
    dram_ = MemoryInterface::create(config_.dram);
}

bool Simulator::run() {
    std::cout << "[Simulator] Starting simulation...\n";
    current_cycle_ = 0;

    while (!all_cores_done()) {
        tick();
        current_cycle_++;

        if (current_cycle_ % 100000 == 0) {
            uint32_t done_count = 0;
            uint32_t total_completed = 0;
            for (auto& c : cores_) {
                if (c.is_done()) done_count++;
                total_completed += c.stats().workloads_completed;
            }
            if (total_completed > last_completed_count_) {
                last_completed_count_ = total_completed;
                last_progress_cycle_ = current_cycle_;
            }
            std::cout << "[Simulator] Cycle " << current_cycle_
                      << ", cores done: " << done_count << "/" << cores_.size()
                      << ", workloads completed: " << total_completed << "/" << sim_stats_.total_workloads;
            if (sim_stats_.total_workloads > 0) {
                double pct = 100.0 * static_cast<double>(total_completed) / sim_stats_.total_workloads;
                std::cout << " (" << std::fixed << std::setprecision(1) << pct << "%)";
            }
            std::cout << "\n";
            if (trace_enabled_) {
                export_trace(false);
            }
            if (check_progress_and_handle_deadlock()) {
                std::cerr << "[Simulator] Aborting due to unresolvable deadlock.\n";
                export_trace();
                return false;
            }
        }
    }

    sim_stats_.total_cycles = current_cycle_;
    sim_stats_.total_dram_subrequests = dram_->total_subrequests();
    std::cout << "[Simulator] Simulation complete at cycle " << current_cycle_;
    if (deadlock_resolutions_ > 0) {
        std::cout << " (deadlock force-advances: " << deadlock_resolutions_ << ")";
    }
    std::cout << "\n";
    return true;
}

void Simulator::tick() {
    // 1. Each core ticks
    for (auto& core : cores_) {
        core.tick(current_cycle_);
    }

    // 2. Collect outgoing packets from cores -> inject into NoC
    collect_core_packets();

    // 3. Inject buffered DRAM responses into NoC (from previous cycles)
    if (topology_.has_dram_on_noc()) {
        inject_dram_responses();
    }

    // 4. Advance NoC and DRAM
    noc_->tick();
    dram_->tick();

    // 5. Deliver NoC packets to cores (and to DRAM controller nodes)
    deliver_noc_packets();

    // 6. Collect DRAM responses
    deliver_dram_responses();
}

bool Simulator::all_cores_done() const {
    for (auto& core : cores_) {
        if (!core.is_done()) return false;
    }
    return true;
}

bool Simulator::check_progress_and_handle_deadlock() {
    const cycle_t no_progress = current_cycle_ - last_progress_cycle_;
    if (no_progress < DEADLOCK_CHECK_THRESHOLD) return false;

    if (detect_and_resolve_deadlock()) {
        last_progress_cycle_ = current_cycle_;
        return false;  // resolved, continue simulation
    }

    if (no_progress >= DEADLOCK_ABORT_THRESHOLD) {
        std::cerr << "\n[Deadlock] No progress for " << no_progress
                  << " cycles and no resolvable cycle found. Aborting.\n";
        print_deadlock_diagnostic();
        return true;  // abort
    }

    return false;
}

std::vector<std::vector<uint32_t>> Simulator::build_wait_for_graph() const {
    uint32_t n = static_cast<uint32_t>(cores_.size());
    std::vector<std::vector<uint32_t>> adj(n);

    for (uint32_t i = 0; i < n; i++) {
        if (cores_[i].is_done()) continue;
        auto waits = cores_[i].get_pending_wait_info();
        std::unordered_set<uint32_t> targets;
        for (auto& w : waits) {
            if (w.source_core != DRAM_ID &&
                w.source_core >= 0 &&
                static_cast<uint32_t>(w.source_core) < n &&
                !cores_[static_cast<uint32_t>(w.source_core)].is_done()) {
                targets.insert(static_cast<uint32_t>(w.source_core));
            }
        }
        for (uint32_t t : targets) {
            adj[i].push_back(t);
        }
    }
    return adj;
}

bool Simulator::find_cycle_dfs(
        const std::vector<std::vector<uint32_t>>& adj,
        std::vector<uint32_t>& cycle) const {
    uint32_t n = static_cast<uint32_t>(adj.size());
    std::vector<int> color(n, 0);  // 0=white 1=gray 2=black
    std::vector<uint32_t> path;

    std::function<bool(uint32_t)> dfs = [&](uint32_t u) -> bool {
        color[u] = 1;
        path.push_back(u);
        for (uint32_t v : adj[u]) {
            if (color[v] == 1) {
                auto it = std::find(path.begin(), path.end(), v);
                cycle.assign(it, path.end());
                return true;
            }
            if (color[v] == 0) {
                if (dfs(v)) return true;
            }
        }
        path.pop_back();
        color[u] = 2;
        return false;
    };

    for (uint32_t i = 0; i < n; i++) {
        if (color[i] == 0 && !adj[i].empty()) {
            if (dfs(i)) return true;
        }
    }
    return false;
}

bool Simulator::detect_and_resolve_deadlock() {
    auto adj = build_wait_for_graph();
    std::vector<uint32_t> cycle;

    if (!find_cycle_dfs(adj, cycle)) {
        // No inter-core cycle. Check for SRAM-pressure stalls:
        // a core is LOADING but all pending data is from DRAM (not another core),
        // yet it has items stuck in sram_retry_queue. No cycle exists, but it's stuck
        // because SRAM is full of stale data. Force-advance it.
        for (auto& core : cores_) {
            if (core.state() == CoreState::LOADING && core.has_sram_retry_pending()) {
                std::cerr << "[Deadlock] Core " << core.id()
                          << " stuck on SRAM pressure (retry queue="
                          << core.has_sram_retry_pending()
                          << "). Force-advancing.\n";
                core.force_advance_loading();
                deadlock_resolutions_++;
                return true;
            }
        }
        // Check for WRITEBACK stalls (no WRITE_RESPONSE for a long time)
        for (auto& core : cores_) {
            if (core.state() == CoreState::WRITEBACK) {
                std::cerr << "[Deadlock] Core " << core.id()
                          << " stuck in WRITEBACK. Force-completing.\n";
                core.force_complete_writeback();
                deadlock_resolutions_++;
                return true;
            }
        }
        return false;
    }

    if (deadlock_resolutions_ >= MAX_DEADLOCK_RESOLUTIONS) {
        std::cerr << "[Deadlock] Resolution limit (" << MAX_DEADLOCK_RESOLUTIONS
                  << ") reached. Aborting.\n";
        print_deadlock_diagnostic();
        return false;
    }

    // Print the cycle
    std::cerr << "[Deadlock] Cycle detected at cycle " << current_cycle_ << ": ";
    for (size_t i = 0; i < cycle.size(); i++) {
        if (i > 0) std::cerr << " -> ";
        std::cerr << "C" << cycle[i];
    }
    std::cerr << " -> C" << cycle[0] << "\n";

    // Pick the core in the cycle with the fewest remaining workloads to force-advance
    uint32_t best = cycle[0];
    size_t fewest_remaining = SIZE_MAX;
    for (uint32_t c : cycle) {
        size_t remaining = 0;
        if (cores_[c].current_workload_index() < SIZE_MAX)
            remaining = 1;  // at least the current one
        if (remaining < fewest_remaining || (remaining == fewest_remaining && c < best)) {
            fewest_remaining = remaining;
            best = c;
        }
    }

    std::cerr << "[Deadlock] Force-advancing core " << best
              << " (workload " << cores_[best].current_workload_index()
              << ", completed " << cores_[best].stats().workloads_completed << ")\n";
    cores_[best].force_advance_loading();
    deadlock_resolutions_++;
    return true;
}

void Simulator::print_deadlock_diagnostic() const {
    auto sname = [](CoreState s) {
        switch (s) {
            case CoreState::IDLE:      return "IDLE";
            case CoreState::LOADING:   return "LOADING";
            case CoreState::COMPUTING: return "COMPUTING";
            case CoreState::WRITEBACK: return "WRITEBACK";
            case CoreState::DONE:      return "DONE";
        }
        return "?";
    };
    std::cerr << "  Per-core state:\n";
    for (size_t i = 0; i < cores_.size(); ++i) {
        auto& c = cores_[i];
        if (c.is_done()) continue;
        std::cerr << "    core " << std::setw(2) << c.id()
                  << " " << std::setw(10) << sname(c.state())
                  << " wl=" << c.current_workload_index()
                  << " done=" << c.stats().workloads_completed
                  << " pending=" << c.pending_loads_count()
                  << " sram_retry=" << (c.has_sram_retry_pending() ? "yes" : "no");
        auto ids = c.pending_load_ids();
        if (!ids.empty()) {
            std::cerr << " wait=[";
            for (size_t j = 0; j < ids.size() && j < 8; ++j) {
                if (j > 0) std::cerr << ",";
                std::cerr << ids[j];
            }
            if (ids.size() > 8) std::cerr << "...+" << (ids.size() - 8);
            std::cerr << "]";
        }
        std::cerr << "\n";
    }
}

void Simulator::collect_core_packets() {
    for (auto& core : cores_) {
        while (core.has_outgoing_packet()) {
            Packet pkt = core.pop_outgoing_packet();

            if (pkt.dst == DRAM_ID) {
                pkt.address = addr_allocator_.get_address(pkt.transfer_id);

                if (pkt.type == PacketType::READ_REQUEST) {
                    sim_stats_.total_dram_reads++;
                } else {
                    sim_stats_.total_dram_writes++;
                }

                if (topology_.has_dram_on_noc()) {
                    core_id_t core_noc = topology_.core_to_noc(
                        static_cast<uint32_t>(pkt.src));
                    core_id_t dram_noc = topology_.select_dram_controller(
                        core_noc, pkt.transfer_id);
                    pkt.src = core_noc;
                    pkt.dst = dram_noc;
                    if (log_dram_) {
                        std::cout << "[DRAM] cycle " << current_cycle_
                                  << " core " << pkt.src << " -> DRAM "
                                  << (pkt.type == PacketType::READ_REQUEST ? "READ_REQ" : "WRITE_REQ")
                                  << " tid=" << pkt.transfer_id << " addr=0x" << std::hex << pkt.address << std::dec
                                  << " size=" << pkt.data_size_bytes << " (via NoC)\n";
                    }
                    noc_->inject(pkt);
                    sim_stats_.total_noc_packets++;
                } else {
                    if (log_dram_) {
                        std::cout << "[DRAM] cycle " << current_cycle_
                                  << " core " << pkt.src << " -> DRAM "
                                  << (pkt.type == PacketType::READ_REQUEST ? "READ_REQ" : "WRITE_REQ")
                                  << " tid=" << pkt.transfer_id << " addr=0x" << std::hex << pkt.address << std::dec
                                  << " size=" << pkt.data_size_bytes << "\n";
                    }
                    dram_->send_request(pkt);
                }
            } else {
                if (topology_.has_dram_on_noc()) {
                    pkt.src = topology_.core_to_noc(
                        static_cast<uint32_t>(pkt.src));
                    pkt.dst = topology_.core_to_noc(
                        static_cast<uint32_t>(pkt.dst));
                }
                noc_->inject(pkt);
                sim_stats_.total_noc_packets++;
            }
        }
    }
}

void Simulator::inject_dram_responses() {
    std::vector<Packet> retry;
    for (auto& pkt : pending_dram_responses_) {
        if (noc_->can_inject(pkt.src)) {
            noc_->inject(pkt);
            sim_stats_.total_noc_packets++;
        } else {
            retry.push_back(pkt);
        }
    }
    pending_dram_responses_ = std::move(retry);
}

void Simulator::deliver_noc_packets() {
    if (topology_.has_dram_on_noc()) {
        // Deliver to compute core nodes
        for (uint32_t c = 0; c < cores_.size(); ++c) {
            core_id_t noc_id = topology_.core_to_noc(c);
            auto packets = noc_->get_delivered_packets(noc_id);
            for (auto& pkt : packets) {
                cores_[c].receive_packet(pkt);
            }
        }
        // Deliver to DRAM controller nodes -> forward to DRAM backend
        for (uint32_t d = 0; d < topology_.num_dram_controllers(); ++d) {
            core_id_t noc_id = topology_.dram_ctrl_to_noc(d);
            auto packets = noc_->get_delivered_packets(noc_id);
            for (auto& pkt : packets) {
                if (log_dram_) {
                    std::cout << "[DRAM] cycle " << current_cycle_
                              << " (NoC delivered) core_noc " << pkt.src << " -> DRAM "
                              << (pkt.type == PacketType::READ_REQUEST ? "READ_REQ" : "WRITE_REQ")
                              << " tid=" << pkt.transfer_id << " addr=0x" << std::hex << pkt.address << std::dec
                              << " size=" << pkt.data_size_bytes << "\n";
                }
                dram_->send_request(pkt);
            }
        }
    } else {
        for (auto& core : cores_) {
            auto packets = noc_->get_delivered_packets(core.id());
            for (auto& pkt : packets) {
                core.receive_packet(pkt);
            }
        }
    }
}

void Simulator::deliver_dram_responses() {
    auto responses = dram_->get_responses();

    if (log_dram_) {
        for (const auto& pkt : responses) {
            std::cout << "[DRAM] cycle " << current_cycle_
                      << " DRAM -> core " << pkt.dst << " "
                      << (pkt.type == PacketType::READ_RESPONSE ? "READ_RESP" : "WRITE_RESP")
                      << " tid=" << pkt.transfer_id << "\n";
        }
    }

    if (topology_.has_dram_on_noc()) {
        // Buffer responses for NoC injection next cycle
        for (auto& pkt : responses) {
            pending_dram_responses_.push_back(pkt);
        }
    } else {
        for (auto& pkt : responses) {
            if (pkt.dst >= 0 && pkt.dst < static_cast<core_id_t>(cores_.size())) {
                cores_[pkt.dst].receive_packet(pkt);
            }
        }
    }
}

void Simulator::print_stats() const {
    std::cout << "\n========== Simulation Statistics ==========\n";
    std::cout << "Total cycles:       " << sim_stats_.total_cycles << "\n";
    std::cout << "NoC packets:        " << sim_stats_.total_noc_packets << "\n";
    std::cout << "DRAM reads:         " << sim_stats_.total_dram_reads << "\n";
    std::cout << "DRAM writes:        " << sim_stats_.total_dram_writes << "\n";
    std::cout << "DRAM sub-requests:  " << sim_stats_.total_dram_subrequests << "\n";
    std::cout << "DRAM addr allocated:" << std::fixed << std::setprecision(2)
              << (addr_allocator_.total_allocated() / (1024.0 * 1024.0)) << " MB\n";
    if (deadlock_resolutions_ > 0) {
        std::cout << "Deadlock resolutions:" << deadlock_resolutions_ << "\n";
    }

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

void Simulator::export_trace(bool verbose) const {
    if (!trace_enabled_) return;
    std::filesystem::create_directories(trace_output_path_);
    const bool quiet = !verbose;
    export_trace_csv(quiet);
    export_workload_summary(quiet);
    if (verbose) {
        std::cout << "\n[Trace] Files written to " << trace_output_path_ << "/\n";
    }
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

void Simulator::export_trace_csv(bool quiet) const {
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
    if (!quiet) {
        std::cout << "[Trace] State transitions: " << path
                  << " (" << all.size() << " events)\n";
    }
}

void Simulator::export_workload_summary(bool quiet) const {
    std::string path = trace_output_path_ + "/workload_summary.csv";
    std::ofstream ofs(path);
    ofs << "core_id,workload_idx,layer_name,op_type,"
        << "start_cycle,loading_done_cycle,compute_done_cycle,end_cycle,"
        << "loading_cycles,loading_dram_cycles,loading_core_cycles,compute_cycles,writeback_cycles,idle_before_cycles,"
        << "data_sources\n";

    auto parse_loading_breakdown = [](const std::string& detail, cycle_t& out_dram, cycle_t& out_core) {
        out_dram = 0;
        out_core = 0;
        auto pos = detail.find("loading_dram_cycles=");
        if (pos != std::string::npos) {
            pos += 19;
            size_t end = detail.find(',', pos);
            if (end == std::string::npos) end = detail.size();
            try { out_dram = static_cast<cycle_t>(std::stoull(detail.substr(pos, end - pos))); } catch (...) {}
        }
        pos = detail.find("loading_core_cycles=");
        if (pos != std::string::npos) {
            pos += 19;
            size_t end = detail.find(',', pos);
            if (end == std::string::npos) end = detail.size();
            try { out_core = static_cast<cycle_t>(std::stoull(detail.substr(pos, end - pos))); } catch (...) {}
        }
    };

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
            cycle_t loading_dram_cycles = 0;
            cycle_t loading_core_cycles = 0;
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
                parse_loading_breakdown(e.detail, cur.loading_dram_cycles, cur.loading_core_cycles);
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
            if (cur.end == 0) {
                cur.end = (sim_stats_.total_cycles > 0) ? sim_stats_.total_cycles : current_cycle_;
            }
            phases.push_back(cur);
        }

        cycle_t prev_end = 0;
        for (auto& p : phases) {
            cycle_t load_t = (p.loading_done > p.start) ? p.loading_done - p.start : 0;
            cycle_t comp_t = (p.compute_done > p.loading_done) ? p.compute_done - p.loading_done : 0;
            cycle_t wb_t = (p.end > p.compute_done) ? p.end - p.compute_done : 0;
            cycle_t idle_before = (p.start <= prev_end) ? 0 : (p.start - prev_end - 1);
            prev_end = p.end;

            ofs << core.id() << ","
                << p.wl_idx << ","
                << p.layer_name << ","
                << p.op_type << ","
                << p.start << ","
                << p.loading_done << ","
                << p.compute_done << ","
                << p.end << ","
                << load_t << ","
                << p.loading_dram_cycles << ","
                << p.loading_core_cycles << ","
                << comp_t << ","
                << wb_t << ","
                << idle_before << ","
                << "\"" << p.data_sources << "\"\n";
        }
    }
    if (!quiet) {
        std::cout << "[Trace] Workload summary: " << path << "\n";
    }
}

}  // namespace npu_sim

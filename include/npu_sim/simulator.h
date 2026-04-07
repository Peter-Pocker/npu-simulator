#pragma once

#include "npu_sim/config.h"
#include "npu_sim/core.h"
#include "npu_sim/task.h"
#include "npu_sim/topology.h"
#include "npu_sim/network_interface.h"
#include "npu_sim/memory_interface.h"
#include "npu_sim/address_allocator.h"

#include <vector>
#include <memory>
#include <string>
#include <functional>

namespace npu_sim {

struct SimStats {
    cycle_t total_cycles = 0;
    uint32_t total_workloads = 0;
    uint64_t total_noc_packets = 0;
    uint64_t total_dram_reads = 0;
    uint64_t total_dram_writes = 0;
    uint64_t total_dram_subrequests = 0;
};

class Simulator {
public:
    explicit Simulator(const SimConfig& config);

    void set_trace_enabled(bool enabled) { trace_enabled_ = enabled; }
    void set_trace_output(const std::string& path) { trace_output_path_ = path; }
    void set_log_dram(bool enabled) { log_dram_ = enabled; }

    void load_ir(const std::string& path);
    /** Run simulation. Returns false if deadlock detected (trace exported if enabled). */
    bool run();
    void print_stats() const;
    /** Export trace files. When verbose is false, skip console output (used for incremental flush). */
    void export_trace(bool verbose = true) const;

private:
    void init_cores();
    void tick();
    bool all_cores_done() const;

    void collect_core_packets();
    void inject_dram_responses();
    void deliver_noc_packets();
    void deliver_dram_responses();

    void export_trace_csv(bool quiet = false) const;
    void export_workload_summary(bool quiet = false) const;

    SimConfig config_;
    Topology topology_;
    IRData ir_data_;
    std::vector<NPUCore> cores_;
    std::unique_ptr<NetworkInterface> noc_;
    std::unique_ptr<MemoryInterface> dram_;
    cycle_t current_cycle_ = 0;
    SimStats sim_stats_;
    bool trace_enabled_ = false;
    std::string trace_output_path_ = "trace";
    bool log_dram_ = false;

    DRAMAddressAllocator addr_allocator_;
    std::vector<Packet> pending_dram_responses_;

    uint32_t last_completed_count_ = 0;
    cycle_t last_progress_cycle_ = 0;
    uint32_t deadlock_resolutions_ = 0;

    static constexpr cycle_t DEADLOCK_CHECK_THRESHOLD  =   500'000;
    static constexpr cycle_t DEADLOCK_ABORT_THRESHOLD   = 20'000'000;
    static constexpr uint32_t MAX_DEADLOCK_RESOLUTIONS  = 1000;

    /** High-level progress check + deadlock handling. Returns true to abort. */
    bool check_progress_and_handle_deadlock();
    /** Build directed wait-for graph: adj[i] = set of cores that core i waits for. */
    std::vector<std::vector<uint32_t>> build_wait_for_graph() const;
    /** DFS cycle detection on the wait-for graph. Returns true if a cycle is found. */
    bool find_cycle_dfs(const std::vector<std::vector<uint32_t>>& adj,
                        std::vector<uint32_t>& cycle) const;
    /** Try to detect and resolve a deadlock. Returns true if resolved. */
    bool detect_and_resolve_deadlock();
    /** Print per-core deadlock diagnostic to stderr. */
    void print_deadlock_diagnostic() const;
};

}  // namespace npu_sim

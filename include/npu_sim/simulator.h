#pragma once

#include "npu_sim/config.h"
#include "npu_sim/core.h"
#include "npu_sim/task.h"
#include "npu_sim/network_interface.h"
#include "npu_sim/memory_interface.h"

#include <vector>
#include <memory>
#include <string>

namespace npu_sim {

struct SimStats {
    cycle_t total_cycles = 0;
    uint32_t total_workloads = 0;
    uint64_t total_noc_packets = 0;
    uint64_t total_dram_reads = 0;
    uint64_t total_dram_writes = 0;
};

class Simulator {
public:
    explicit Simulator(const SimConfig& config);

    void set_trace_enabled(bool enabled) { trace_enabled_ = enabled; }
    void set_trace_output(const std::string& path) { trace_output_path_ = path; }

    void load_ir(const std::string& path);
    void run();
    void print_stats() const;
    void export_trace() const;

private:
    void init_cores();
    void tick();
    bool all_cores_done() const;

    void collect_core_packets();
    void deliver_noc_packets();
    void deliver_dram_responses();

    void export_trace_csv() const;
    void export_workload_summary() const;

    SimConfig config_;
    IRData ir_data_;
    std::vector<NPUCore> cores_;
    std::unique_ptr<NetworkInterface> noc_;
    std::unique_ptr<MemoryInterface> dram_;
    cycle_t current_cycle_ = 0;
    SimStats sim_stats_;
    bool trace_enabled_ = false;
    std::string trace_output_path_ = "trace";
};

}  // namespace npu_sim

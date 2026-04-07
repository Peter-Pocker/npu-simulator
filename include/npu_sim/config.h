#pragma once

#include <string>
#include <cstdint>
#include <vector>
#include <nlohmann/json.hpp>

namespace npu_sim {

struct DRAMControllerPos {
    uint32_t x = 0;
    uint32_t y = 0;
};

struct TopologyConfig {
    uint32_t core_origin_x = 0;
    uint32_t core_origin_y = 0;
    std::vector<DRAMControllerPos> dram_controllers;
    std::string dram_routing_policy = "nearest";
};

struct CoreConfig {
    uint32_t mac_units = 256;
    uint32_t vector_units = 64;
    double clock_freq_mhz = 1000.0;
    bool use_analytical_time = true;
};

struct SRAMConfig {
    uint64_t size_kb = 512;
    uint64_t read_bandwidth_bytes_per_cycle = 64;
    uint64_t write_bandwidth_bytes_per_cycle = 64;
    /// PE (Conv/FC) buffer bandwidth; 0 = use unified above
    uint64_t pe_read_bandwidth_bytes_per_cycle = 0;
    uint64_t pe_write_bandwidth_bytes_per_cycle = 0;
    /// VP (Pool/ElementWise) buffer bandwidth; 0 = use unified above
    uint64_t vp_read_bandwidth_bytes_per_cycle = 0;
    uint64_t vp_write_bandwidth_bytes_per_cycle = 0;
};

struct NetworkInterfaceConfig {
    uint32_t max_outstanding_reqs = 16;
    uint32_t injection_queue_size = 8;
    uint32_t ejection_queue_size = 8;
};

struct NoCConfig {
    uint32_t flit_size_bytes = 16;
    uint32_t hop_latency_cycles = 1;
    uint32_t router_latency_cycles = 1;
    std::string backend = "simple";  // "simple" or "booksim2"
    uint32_t injection_queue_depth = 16;
    // clock_ratio = core_freq / noc_freq.
    //   1.0 = same freq as core (default)
    //   2.0 = NoC runs at half core freq
    //   0.5 = NoC runs at double core freq
    double clock_ratio = 1.0;

    // BookSim2 router parameters (used when backend="booksim2" and no external cfg)
    uint32_t num_vcs = 8;
    uint32_t vc_buf_size = 8;
    uint32_t routing_delay = 1;
    uint32_t vc_alloc_delay = 1;
    uint32_t sw_alloc_delay = 1;
    uint32_t st_final_delay = 1;
    uint32_t deadlock_warn_timeout = 1024;

    // External BookSim2 cfg file (empty = auto-generate from above params)
    std::string booksim2_config_path;
};

struct DRAMConfig {
    uint32_t num_channels = 4;
    // clock_ratio = core_freq / dram_controller_freq.
    //   1.0 = same freq as core (default)
    //   0.5 = DRAM controller runs at 2x core freq
    //   1.5 = DRAM controller runs at 2/3 core freq
    double clock_ratio = 1.0;
    std::string backend = "simple";  // "simple" or "ramulator2"

    // DRAM access granularity (bytes). 0 = auto-detect from standard (burst size).
    // DDR4/DDR5/HBM2/HBM3 → 64B, LPDDR4/LPDDR5 → 32B.
    uint32_t cache_line_size = 0;

    // Ramulator2 parameters (used when backend="ramulator2" and no external yaml)
    std::string standard = "DDR4";          // DDR4 / DDR5 / HBM2 / LPDDR4 ...
    std::string org = "DDR4_8Gb_x8";        // organization preset
    std::string timing = "DDR4_2400R";      // timing preset
    uint32_t num_ranks = 2;
    std::string scheduler = "FRFCFS";       // controller scheduler
    std::string addr_mapper = "RoBaRaCoCh"; // RoBaRaCoCh / ChRaBaRoCo / MOP4CLXOR / CustomizedMapper
    std::string addr_mapping;               // required when addr_mapper="CustomizedMapper", e.g. "2BG-2B-1RA-16R-7C"
    uint32_t frontend_clock_ratio = 4;      // Ramulator2 frontend clock_ratio

    // Controller plugins (empty path = disabled)
    std::string trace_recorder_path;        // TraceRecorder output path, e.g. "./trace/issue_log"
    std::string cmd_counter_path;           // CommandCounter output path, e.g. "./trace/cmd_cnt.log"
    std::vector<std::string> cmd_counter_commands; // commands to count, e.g. ["ACT","PRE","RD","WR","REFab"]

    // External Ramulator2 yaml file (empty = auto-generate from above params)
    std::string config_path;

    /// When true, Ramulator2 backend prints accept/complete logs (for debugging).
    bool log_dram = false;

    uint32_t resolved_cache_line_size() const {
        if (cache_line_size > 0) return cache_line_size;
        if (standard.find("LPDDR") == 0) return 32;
        return 64;
    }
};

struct SimConfig {
    uint32_t num_cores_x = 4;
    uint32_t num_cores_y = 4;
    uint32_t element_size_bits = 8;

    CoreConfig core;
    SRAMConfig sram;
    NetworkInterfaceConfig ni;
    NoCConfig noc;
    DRAMConfig dram;
    TopologyConfig topology;

    std::string ir_path;
    std::string output_path = "sim_output";

    /// When true, print DRAM request/response logs (for debugging Ramulator2 interaction).
    bool log_dram = false;

    static SimConfig load_from_json(const std::string& path);
};

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(CoreConfig,
    mac_units, vector_units, clock_freq_mhz, use_analytical_time)

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(SRAMConfig,
    size_kb, read_bandwidth_bytes_per_cycle, write_bandwidth_bytes_per_cycle,
    pe_read_bandwidth_bytes_per_cycle, pe_write_bandwidth_bytes_per_cycle,
    vp_read_bandwidth_bytes_per_cycle, vp_write_bandwidth_bytes_per_cycle)

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(NetworkInterfaceConfig,
    max_outstanding_reqs, injection_queue_size, ejection_queue_size)

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(NoCConfig,
    flit_size_bytes, hop_latency_cycles, router_latency_cycles,
    backend, injection_queue_depth, clock_ratio,
    num_vcs, vc_buf_size, routing_delay, vc_alloc_delay,
    sw_alloc_delay, st_final_delay, deadlock_warn_timeout,
    booksim2_config_path)

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(DRAMConfig,
    num_channels, clock_ratio, backend, cache_line_size,
    standard, org, timing, num_ranks, scheduler, addr_mapper,
    addr_mapping, frontend_clock_ratio,
    trace_recorder_path, cmd_counter_path, cmd_counter_commands,
    config_path, log_dram)

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(DRAMControllerPos, x, y)

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(TopologyConfig,
    core_origin_x, core_origin_y, dram_controllers, dram_routing_policy)

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(SimConfig,
    num_cores_x, num_cores_y, element_size_bits,
    core, sram, ni, noc, dram, topology,
    ir_path, output_path, log_dram)

}  // namespace npu_sim

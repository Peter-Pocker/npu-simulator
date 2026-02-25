#pragma once

#include <string>
#include <cstdint>
#include <nlohmann/json.hpp>

namespace npu_sim {

struct CoreConfig {
    uint32_t mac_units = 256;
    uint32_t vector_units = 64;
    double clock_freq_mhz = 1000.0;
};

struct SRAMConfig {
    uint64_t size_kb = 512;
    uint64_t read_bandwidth_bytes_per_cycle = 64;
    uint64_t write_bandwidth_bytes_per_cycle = 64;
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
    std::string booksim2_config_path = "configs/booksim2_mesh.cfg";
    uint32_t injection_queue_depth = 16;
};

struct DRAMConfig {
    std::string config_path = "configs/ramulator2_ddr4.yaml";
    uint32_t num_channels = 4;
    std::string backend = "simple";  // "simple" or "ramulator2"
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

    std::string ir_path;
    std::string output_path = "sim_output";

    static SimConfig load_from_json(const std::string& path);
};

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(CoreConfig,
    mac_units, vector_units, clock_freq_mhz)

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(SRAMConfig,
    size_kb, read_bandwidth_bytes_per_cycle, write_bandwidth_bytes_per_cycle)

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(NetworkInterfaceConfig,
    max_outstanding_reqs, injection_queue_size, ejection_queue_size)

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(NoCConfig,
    flit_size_bytes, hop_latency_cycles, router_latency_cycles,
    backend, booksim2_config_path, injection_queue_depth)

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(DRAMConfig,
    config_path, num_channels, backend)

NLOHMANN_DEFINE_TYPE_NON_INTRUSIVE_WITH_DEFAULT(SimConfig,
    num_cores_x, num_cores_y, element_size_bits,
    core, sram, ni, noc, dram,
    ir_path, output_path)

}  // namespace npu_sim

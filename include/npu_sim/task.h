#pragma once

#include "npu_sim/types.h"
#include <vector>
#include <array>
#include <string>

namespace npu_sim {

struct TensorRange {
    std::array<uint32_t, 4> lower = {0, 0, 0, 0};  // [B, C, H, W]
    std::array<uint32_t, 4> upper = {0, 0, 0, 0};

    uint64_t volume() const {
        uint64_t vol = 1;
        for (int i = 0; i < 4; ++i) {
            vol *= (upper[i] - lower[i] + 1);
        }
        return vol;
    }

    uint32_t dim_size(int d) const { return upper[d] - lower[d] + 1; }
};

struct DataSource {
    SourceType type;
    core_id_t source_id;
    uint64_t size_bytes;
    transfer_id_t transfer_id;
    std::string layer_name;
};

struct DataDestination {
    SourceType type;
    core_id_t dest_id;
    workload_id_t dest_workload_id;
    std::string layer_name;
};

struct BufferRequirement {
    DataType data_type;
    std::string source_layer;
    workload_id_t source_workload_id = 0;
    uint64_t size_bytes = 0;
    uint64_t block_kb = 0;
    std::vector<DataSource> sources;
    std::vector<transfer_id_t> transfer_ids;
};

struct OutputTransfer {
    TensorRange range;
    uint64_t size_bytes;
    transfer_id_t transfer_id;
    std::vector<DataDestination> destinations;
};

struct Workload {
    workload_id_t id = 0;
    std::string layer_name;
    OperatorType op_type = OperatorType::CUSTOM;

    TensorRange ofmap_range;
    uint64_t ofmap_size_bytes = 0;

    uint64_t weight_size_bytes = 0;

    cycle_t analytical_time = 0;

    std::vector<BufferRequirement> buffers;
    std::vector<OutputTransfer> outputs;

    cycle_t compute_cycles(uint32_t mac_units, uint32_t vector_units,
                           uint32_t element_size_bits) const {
        if (analytical_time > 0) return analytical_time;

        uint64_t ofmap_B = ofmap_range.dim_size(0);
        uint64_t ofmap_H = ofmap_range.dim_size(2);
        uint64_t ofmap_W = ofmap_range.dim_size(3);

        switch (op_type) {
            case OperatorType::CONV2D:
            case OperatorType::FC: {
                uint64_t weight_elements = weight_size_bytes * 8 / element_size_bits;
                uint64_t total_macs = ofmap_B * ofmap_H * ofmap_W * weight_elements;
                return (total_macs + mac_units - 1) / mac_units;
            }
            case OperatorType::POOLING:
            case OperatorType::ELEMENT_WISE:
            case OperatorType::POINT_TO_POINT: {
                uint64_t ofmap_elements = ofmap_range.volume();
                return (ofmap_elements + vector_units - 1) / vector_units;
            }
            default:
                return 1;
        }
    }
};

struct DRAMTransfer {
    DataType data_type;
    std::string layer_name;
    uint64_t size_bytes;
    transfer_id_t transfer_id;
    core_id_t core_id;
    workload_id_t workload_id;
    bool is_read;  // true = DRAM->Core, false = Core->DRAM
};

struct IRData {
    uint32_t num_cores_x = 0;
    uint32_t num_cores_y = 0;
    uint32_t top_batch_cut = 1;
    uint64_t required_sram_bytes = 0;
    std::vector<std::vector<Workload>> core_workloads;
    std::vector<DRAMTransfer> dram_reads;
    std::vector<DRAMTransfer> dram_writes;
};

}  // namespace npu_sim

#pragma once

#include "npu_sim/types.h"

namespace npu_sim {

struct Packet {
    PacketType type;
    core_id_t src;
    core_id_t dst;
    transfer_id_t transfer_id;
    uint64_t data_size_bytes;
    uint64_t address = 0;
    cycle_t inject_cycle = 0;
    cycle_t deliver_cycle = 0;

    uint32_t num_flits(uint32_t flit_size_bytes) const {
        if (type == PacketType::READ_REQUEST) {
            return 1;
        }
        uint32_t payload_flits = static_cast<uint32_t>(
            (data_size_bytes + flit_size_bytes - 1) / flit_size_bytes);
        return 1 + payload_flits;  // header + payload
    }
};

}  // namespace npu_sim

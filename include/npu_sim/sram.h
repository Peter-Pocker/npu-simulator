#pragma once

#include "npu_sim/types.h"
#include <unordered_map>
#include <cstdint>

namespace npu_sim {

struct SRAMEntry {
    transfer_id_t transfer_id;
    uint64_t size_bytes;
    DataType data_type;
    bool ready;              // data is fully loaded and available
    uint32_t ref_count;      // number of consumers still needing this data
};

class SRAM {
public:
    explicit SRAM(uint64_t capacity_bytes,
                  uint64_t read_bw_bytes_per_cycle,
                  uint64_t write_bw_bytes_per_cycle);

    bool allocate(transfer_id_t id, uint64_t size_bytes,
                  DataType type, uint32_t ref_count = 0);
    bool mark_ready(transfer_id_t id);
    bool has_data(transfer_id_t id) const;
    bool is_ready(transfer_id_t id) const;
    bool release_ref(transfer_id_t id);
    bool deallocate(transfer_id_t id);

    uint64_t capacity() const { return capacity_bytes_; }
    uint64_t used() const { return used_bytes_; }
    uint64_t available() const { return capacity_bytes_ - used_bytes_; }
    bool can_fit(uint64_t size_bytes) const { return available() >= size_bytes; }

    uint64_t read_bandwidth() const { return read_bw_; }
    uint64_t write_bandwidth() const { return write_bw_; }

    void clear();

    const std::unordered_map<transfer_id_t, SRAMEntry>& entries() const {
        return entries_;
    }

private:
    uint64_t capacity_bytes_;
    uint64_t used_bytes_ = 0;
    uint64_t read_bw_;
    uint64_t write_bw_;
    std::unordered_map<transfer_id_t, SRAMEntry> entries_;
};

}  // namespace npu_sim

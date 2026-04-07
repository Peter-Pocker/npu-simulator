#pragma once

#include "npu_sim/types.h"
#include "npu_sim/task.h"
#include <unordered_map>
#include <cstdint>
#include <string>

namespace npu_sim {

enum class AddressAllocStrategy {
    INDEPENDENT,
    TENSOR_LAYOUT   // reserved for future: allocate by full tensor, slices use strided access
};

class DRAMAddressAllocator {
public:
    explicit DRAMAddressAllocator(uint32_t alignment = 64);

    void allocate(const IRData& ir_data);

    uint64_t get_address(transfer_id_t id) const;

    bool has_address(transfer_id_t id) const;

    uint64_t total_allocated() const { return next_addr_; }
    size_t num_entries() const { return addr_map_.size(); }

    void print_summary() const;

private:
    uint32_t alignment_;
    uint64_t next_addr_ = 0;
    std::unordered_map<transfer_id_t, uint64_t> addr_map_;
    std::unordered_map<transfer_id_t, uint64_t> size_map_;

    void collect_transfer(transfer_id_t id, uint64_t size_bytes);
    uint64_t align_up(uint64_t addr) const;
};

}  // namespace npu_sim

#include "npu_sim/address_allocator.h"

#include <algorithm>
#include <iostream>
#include <iomanip>
#include <vector>

namespace npu_sim {

DRAMAddressAllocator::DRAMAddressAllocator(uint32_t alignment)
    : alignment_(alignment > 0 ? alignment : 64) {}

void DRAMAddressAllocator::collect_transfer(transfer_id_t id, uint64_t size_bytes) {
    if (size_bytes == 0) return;
    auto it = size_map_.find(id);
    if (it == size_map_.end()) {
        size_map_[id] = size_bytes;
    } else {
        it->second = std::max(it->second, size_bytes);
    }
}

uint64_t DRAMAddressAllocator::align_up(uint64_t addr) const {
    uint64_t mask = alignment_ - 1;
    return (addr + mask) & ~mask;
}

void DRAMAddressAllocator::allocate(const IRData& ir_data) {
    size_map_.clear();
    addr_map_.clear();
    next_addr_ = 0;

    for (auto& dt : ir_data.dram_reads) {
        collect_transfer(dt.transfer_id, dt.size_bytes);
    }
    for (auto& dt : ir_data.dram_writes) {
        collect_transfer(dt.transfer_id, dt.size_bytes);
    }

    for (auto& core_wls : ir_data.core_workloads) {
        for (auto& wl : core_wls) {
            for (auto& buf : wl.buffers) {
                for (auto& src : buf.sources) {
                    collect_transfer(src.transfer_id, src.size_bytes);
                }
            }
            for (auto& out : wl.outputs) {
                collect_transfer(out.transfer_id, out.size_bytes);
            }
        }
    }

    std::vector<transfer_id_t> sorted_ids;
    sorted_ids.reserve(size_map_.size());
    for (auto& [id, _] : size_map_) {
        sorted_ids.push_back(id);
    }
    std::sort(sorted_ids.begin(), sorted_ids.end());

    for (auto id : sorted_ids) {
        uint64_t base = align_up(next_addr_);
        addr_map_[id] = base;
        next_addr_ = base + align_up(size_map_[id]);
    }
}

uint64_t DRAMAddressAllocator::get_address(transfer_id_t id) const {
    auto it = addr_map_.find(id);
    if (it != addr_map_.end()) return it->second;
    return static_cast<uint64_t>(id) * alignment_;
}

bool DRAMAddressAllocator::has_address(transfer_id_t id) const {
    return addr_map_.count(id) > 0;
}

void DRAMAddressAllocator::print_summary() const {
    double total_mb = next_addr_ / (1024.0 * 1024.0);
    std::cout << "[AddressAllocator] Allocated " << addr_map_.size()
              << " transfer blocks, total " << std::fixed
              << std::setprecision(2) << total_mb << " MB"
              << " (alignment=" << alignment_ << "B)\n";
}

}  // namespace npu_sim

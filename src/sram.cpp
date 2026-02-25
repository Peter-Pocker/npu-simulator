#include "npu_sim/sram.h"
#include <stdexcept>

namespace npu_sim {

SRAM::SRAM(uint64_t capacity_bytes,
           uint64_t read_bw_bytes_per_cycle,
           uint64_t write_bw_bytes_per_cycle)
    : capacity_bytes_(capacity_bytes)
    , read_bw_(read_bw_bytes_per_cycle)
    , write_bw_(write_bw_bytes_per_cycle) {}

bool SRAM::allocate(transfer_id_t id, uint64_t size_bytes,
                    DataType type, uint32_t ref_count) {
    if (entries_.count(id)) {
        return true;  // already allocated
    }
    if (used_bytes_ + size_bytes > capacity_bytes_) {
        return false;
    }
    entries_[id] = SRAMEntry{id, size_bytes, type, false, ref_count};
    used_bytes_ += size_bytes;
    return true;
}

bool SRAM::mark_ready(transfer_id_t id) {
    auto it = entries_.find(id);
    if (it == entries_.end()) return false;
    it->second.ready = true;
    return true;
}

bool SRAM::has_data(transfer_id_t id) const {
    return entries_.count(id) > 0;
}

bool SRAM::is_ready(transfer_id_t id) const {
    auto it = entries_.find(id);
    if (it == entries_.end()) return false;
    return it->second.ready;
}

bool SRAM::release_ref(transfer_id_t id) {
    auto it = entries_.find(id);
    if (it == entries_.end()) return false;
    if (it->second.ref_count > 0) {
        it->second.ref_count--;
    }
    if (it->second.ref_count == 0) {
        used_bytes_ -= it->second.size_bytes;
        entries_.erase(it);
        return true;
    }
    return false;
}

bool SRAM::deallocate(transfer_id_t id) {
    auto it = entries_.find(id);
    if (it == entries_.end()) return false;
    used_bytes_ -= it->second.size_bytes;
    entries_.erase(it);
    return true;
}

void SRAM::clear() {
    entries_.clear();
    used_bytes_ = 0;
}

}  // namespace npu_sim

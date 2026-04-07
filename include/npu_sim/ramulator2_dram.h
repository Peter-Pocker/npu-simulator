#pragma once

#include "npu_sim/memory_interface.h"
#include <string>
#include <vector>
#include <deque>
#include <unordered_map>
#include <functional>

namespace Ramulator {
    class IFrontEnd;
    class IMemorySystem;
}

namespace npu_sim {

class Ramulator2DRAM : public MemoryInterface {
public:
    Ramulator2DRAM();
    ~Ramulator2DRAM() override;

    void init(const DRAMConfig& config) override;
    bool send_request(const Packet& pkt) override;
    void tick() override;
    std::vector<Packet> get_responses() override;
    cycle_t current_cycle() const override { return cycle_; }

    uint64_t total_subrequests() const override { return total_subreqs_; }

private:
    struct BurstGroup {
        uint64_t burst_id;
        Packet original_pkt;
        uint64_t base_addr;
        uint32_t total_lines;
        uint32_t injected = 0;
        uint32_t completed = 0;
        bool is_read;
    };

    void inject_pending_subrequests();
    void on_subrequest_complete(uint64_t burst_id);

    cycle_t cycle_ = 0;
    DRAMConfig config_;
    uint32_t cache_line_size_ = 64;

    Ramulator::IFrontEnd* frontend_ = nullptr;
    Ramulator::IMemorySystem* memory_system_ = nullptr;

    int clock_ratio_ = 1;
    double tick_accum_ = 0.0;

    uint64_t next_burst_id_ = 0;
    std::unordered_map<uint64_t, BurstGroup> bursts_;
    std::deque<uint64_t> injection_order_;

    uint64_t total_subreqs_ = 0;
    std::vector<Packet> completed_responses_;
};

}  // namespace npu_sim

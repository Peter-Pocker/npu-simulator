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

private:
    void on_read_complete(int64_t addr);
    void on_write_complete(int64_t addr);

    cycle_t cycle_ = 0;
    DRAMConfig config_;

    Ramulator::IFrontEnd* frontend_ = nullptr;
    Ramulator::IMemorySystem* memory_system_ = nullptr;

    int clock_ratio_ = 1;
    int tick_counter_ = 0;

    struct OutstandingRequest {
        Packet pkt;
        bool is_read;
    };
    std::unordered_multimap<int64_t, OutstandingRequest> outstanding_;

    std::vector<Packet> completed_responses_;
};

}  // namespace npu_sim

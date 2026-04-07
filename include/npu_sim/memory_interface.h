#pragma once

#include "npu_sim/packet.h"
#include "npu_sim/config.h"
#include <memory>
#include <vector>
#include <functional>

namespace npu_sim {

/// Abstract DRAM interface. Concrete implementations wrap Ramulator2, etc.
class MemoryInterface {
public:
    virtual ~MemoryInterface() = default;

    virtual void init(const DRAMConfig& config) = 0;

    /// Submit a memory request. Returns true if accepted.
    virtual bool send_request(const Packet& pkt) = 0;

    /// Advance the DRAM by one cycle.
    virtual void tick() = 0;

    /// Retrieve completed responses this cycle.
    virtual std::vector<Packet> get_responses() = 0;

    virtual cycle_t current_cycle() const = 0;

    virtual uint64_t total_subrequests() const { return 0; }

    static std::unique_ptr<MemoryInterface> create_simple(const DRAMConfig& config);

    static std::unique_ptr<MemoryInterface> create(const DRAMConfig& config);
};

/// Simple fixed-latency DRAM stub.
/// To be replaced by Ramulator2 wrapper for accurate simulation.
class SimpleDRAM : public MemoryInterface {
public:
    void init(const DRAMConfig& config) override;
    bool send_request(const Packet& pkt) override;
    void tick() override;
    std::vector<Packet> get_responses() override;
    cycle_t current_cycle() const override { return cycle_; }

private:
    static constexpr cycle_t DEFAULT_READ_LATENCY = 100;
    static constexpr cycle_t DEFAULT_WRITE_LATENCY = 100;

    DRAMConfig config_;
    cycle_t cycle_ = 0;
    std::vector<Packet> in_flight_;
};

}  // namespace npu_sim

#pragma once

#include "npu_sim/packet.h"
#include "npu_sim/config.h"
#include <vector>
#include <memory>
#include <functional>

namespace npu_sim {

/// Abstract NoC interface. Concrete implementations wrap BookSim2, etc.
class NetworkInterface {
public:
    virtual ~NetworkInterface() = default;

    virtual void init(uint32_t num_nodes, const NoCConfig& config) = 0;

    /// Inject a packet into the network from a source node.
    /// Returns true if the packet was accepted, false if the injection buffer is full.
    virtual bool inject(const Packet& pkt) = 0;

    /// Advance the network by one cycle.
    virtual void tick() = 0;

    /// Retrieve packets delivered to a specific node this cycle.
    virtual std::vector<Packet> get_delivered_packets(core_id_t node_id) = 0;

    /// Check if the network can accept a packet from the given node.
    virtual bool can_inject(core_id_t node_id) const = 0;

    virtual cycle_t current_cycle() const = 0;

    static std::unique_ptr<NetworkInterface> create_simple(
        uint32_t num_nodes, const NoCConfig& config);

    static std::unique_ptr<NetworkInterface> create(
        uint32_t num_nodes, const NoCConfig& config);
};

/// Simple latency-based NoC stub (Manhattan distance * hop_latency).
/// To be replaced by BookSim2 wrapper for accurate simulation.
class SimpleNoC : public NetworkInterface {
public:
    void init(uint32_t num_nodes, const NoCConfig& config) override;
    bool inject(const Packet& pkt) override;
    void tick() override;
    std::vector<Packet> get_delivered_packets(core_id_t node_id) override;
    bool can_inject(core_id_t node_id) const override;
    cycle_t current_cycle() const override { return cycle_; }

private:
    uint32_t calc_latency(core_id_t src, core_id_t dst) const;

    uint32_t num_nodes_ = 0;
    uint32_t mesh_width_ = 0;
    NoCConfig config_;
    cycle_t cycle_ = 0;
    std::vector<Packet> in_flight_;
};

}  // namespace npu_sim

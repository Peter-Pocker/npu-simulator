#pragma once

#include "npu_sim/network_interface.h"
#include <string>
#include <vector>
#include <deque>
#include <memory>
#include <unordered_map>

class BookSimConfig;
class Configuration;
class Network;
class TrafficManager;
class BufferState;
class Flit;

namespace npu_sim {

struct InFlightPacketInfo {
    Packet original_pkt;
    int num_flits;
    int flits_arrived = 0;
};

class BookSim2NoC : public NetworkInterface {
public:
    BookSim2NoC();
    ~BookSim2NoC() override;

    void init(uint32_t num_nodes, const NoCConfig& config,
              uint32_t mesh_width = 0, uint32_t mesh_height = 0) override;
    bool inject(const Packet& pkt) override;
    void tick() override;
    std::vector<Packet> get_delivered_packets(core_id_t node_id) override;
    bool can_inject(core_id_t node_id) const override;
    cycle_t current_cycle() const override { return cycle_; }

private:
    void init_booksim(const std::string& config_path, uint32_t num_nodes,
                      uint32_t mesh_w, uint32_t mesh_h);
    void tick_network();
    int allocate_vc(int source, int subnet, Flit* head_flit);

    cycle_t cycle_ = 0;
    uint32_t num_nodes_ = 0;
    NoCConfig noc_config_;

    BookSimConfig* bs_config_ = nullptr;
    std::vector<Network*> networks_;
    std::vector<std::vector<BufferState*>> buf_states_;
    int subnets_ = 1;
    int vcs_ = 16;
    int classes_ = 1;

    std::vector<std::vector<int>> last_vc_;

    std::vector<std::deque<Flit*>> injection_queues_;

    int next_flit_id_ = 0;
    int next_packet_id_ = 0;
    double tick_accum_ = 0.0;

    std::unordered_map<int, InFlightPacketInfo> in_flight_packets_;

    std::vector<std::vector<Packet>> delivered_this_cycle_;
};

}  // namespace npu_sim

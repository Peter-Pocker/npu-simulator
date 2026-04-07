#pragma once

#include "npu_sim/types.h"
#include "npu_sim/config.h"

#include <vector>
#include <unordered_map>
#include <cstdint>
#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <iostream>

namespace npu_sim {

struct NodePos {
    uint32_t x = 0;
    uint32_t y = 0;
};

class Topology {
public:
    void build(uint32_t num_cores_x, uint32_t num_cores_y,
               const TopologyConfig& topo_cfg) {
        num_cores_x_ = num_cores_x;
        num_cores_y_ = num_cores_y;
        num_compute_cores_ = num_cores_x * num_cores_y;
        dram_on_noc_ = !topo_cfg.dram_controllers.empty();
        policy_ = string_to_dram_routing_policy(topo_cfg.dram_routing_policy);

        uint32_t ox = topo_cfg.core_origin_x;
        uint32_t oy = topo_cfg.core_origin_y;

        uint32_t max_x = 0, max_y = 0;

        core_noc_ids_.resize(num_compute_cores_);
        for (uint32_t c = 0; c < num_compute_cores_; ++c) {
            uint32_t cx = ox + (c % num_cores_x);
            uint32_t cy = oy + (c / num_cores_x);
            core_positions_.push_back({cx, cy});
            max_x = std::max(max_x, cx);
            max_y = std::max(max_y, cy);
        }

        for (auto& dc : topo_cfg.dram_controllers) {
            dram_positions_.push_back({dc.x, dc.y});
            max_x = std::max(max_x, dc.x);
            max_y = std::max(max_y, dc.y);
        }

        if (dram_on_noc_) {
            mesh_width_ = max_x + 1;
            mesh_height_ = max_y + 1;
        } else {
            mesh_width_ = num_cores_x > 0 ? (max_x + 1) : 1;
            mesh_height_ = num_cores_y > 0 ? (max_y + 1) : 1;
        }

        build_mappings();
        validate();
    }

    bool has_dram_on_noc() const { return dram_on_noc_; }
    uint32_t mesh_width() const { return mesh_width_; }
    uint32_t mesh_height() const { return mesh_height_; }
    uint32_t total_noc_nodes() const { return mesh_width_ * mesh_height_; }
    uint32_t num_compute_cores() const { return num_compute_cores_; }
    uint32_t num_dram_controllers() const { return static_cast<uint32_t>(dram_positions_.size()); }

    core_id_t core_to_noc(uint32_t core_id) const {
        if (core_id >= num_compute_cores_) return -1;
        return core_noc_ids_[core_id];
    }

    int32_t noc_to_core(core_id_t noc_node) const {
        auto it = noc_to_core_.find(noc_node);
        return (it != noc_to_core_.end()) ? it->second : -1;
    }

    core_id_t dram_ctrl_to_noc(uint32_t ctrl_id) const {
        if (ctrl_id >= dram_noc_ids_.size()) return -1;
        return dram_noc_ids_[ctrl_id];
    }

    int32_t noc_to_dram_ctrl(core_id_t noc_node) const {
        auto it = noc_to_dram_.find(noc_node);
        return (it != noc_to_dram_.end()) ? it->second : -1;
    }

    bool is_core_node(core_id_t noc_node) const {
        return noc_to_core_.count(noc_node) > 0;
    }

    bool is_dram_node(core_id_t noc_node) const {
        return noc_to_dram_.count(noc_node) > 0;
    }

    NodePos node_position(core_id_t noc_node) const {
        uint32_t n = static_cast<uint32_t>(noc_node);
        return {n % mesh_width_, n / mesh_width_};
    }

    core_id_t select_dram_controller(core_id_t core_noc_id,
                                     uint64_t addr_hint = 0) const {
        if (dram_noc_ids_.empty()) return -1;
        switch (policy_) {
            case DRAMRoutingPolicy::NEAREST:
                return select_nearest(core_noc_id);
            case DRAMRoutingPolicy::HASH:
                return select_hash(addr_hint);
            case DRAMRoutingPolicy::ROUND_ROBIN:
                return select_round_robin();
        }
        return select_nearest(core_noc_id);
    }

    void print_summary() const {
        std::cout << "[Topology] mesh=" << mesh_width_ << "x" << mesh_height_
                  << " (" << total_noc_nodes() << " nodes), "
                  << num_compute_cores_ << " cores, "
                  << dram_positions_.size() << " DRAM controllers";
        if (dram_on_noc_) {
            std::cout << " (on NoC, policy="
                      << dram_routing_policy_to_string(policy_) << ")";
        } else {
            std::cout << " (direct, bypass NoC)";
        }
        std::cout << "\n";
    }

private:
    void build_mappings() {
        noc_to_core_.clear();
        noc_to_dram_.clear();

        for (uint32_t c = 0; c < num_compute_cores_; ++c) {
            auto& pos = core_positions_[c];
            core_id_t noc_id = static_cast<core_id_t>(pos.y * mesh_width_ + pos.x);
            core_noc_ids_[c] = noc_id;
            noc_to_core_[noc_id] = static_cast<int32_t>(c);
        }

        dram_noc_ids_.resize(dram_positions_.size());
        for (uint32_t d = 0; d < dram_positions_.size(); ++d) {
            auto& pos = dram_positions_[d];
            core_id_t noc_id = static_cast<core_id_t>(pos.y * mesh_width_ + pos.x);
            dram_noc_ids_[d] = noc_id;
            noc_to_dram_[noc_id] = static_cast<int32_t>(d);
        }
    }

    void validate() const {
        for (uint32_t c = 0; c < num_compute_cores_; ++c) {
            core_id_t nid = core_noc_ids_[c];
            if (noc_to_dram_.count(nid)) {
                throw std::runtime_error(
                    "Topology error: core " + std::to_string(c) +
                    " overlaps with DRAM controller at NoC node " +
                    std::to_string(nid));
            }
        }
    }

    core_id_t select_nearest(core_id_t core_noc_id) const {
        auto core_pos = node_position(core_noc_id);
        core_id_t best = dram_noc_ids_[0];
        uint32_t best_dist = UINT32_MAX;
        for (auto nid : dram_noc_ids_) {
            auto dpos = node_position(nid);
            uint32_t dist = (core_pos.x > dpos.x ? core_pos.x - dpos.x : dpos.x - core_pos.x)
                          + (core_pos.y > dpos.y ? core_pos.y - dpos.y : dpos.y - core_pos.y);
            if (dist < best_dist) {
                best_dist = dist;
                best = nid;
            }
        }
        return best;
    }

    core_id_t select_hash(uint64_t addr_hint) const {
        uint32_t idx = static_cast<uint32_t>(addr_hint % dram_noc_ids_.size());
        return dram_noc_ids_[idx];
    }

    core_id_t select_round_robin() const {
        uint32_t idx = rr_counter_++ % static_cast<uint32_t>(dram_noc_ids_.size());
        return dram_noc_ids_[idx];
    }

    uint32_t num_cores_x_ = 0;
    uint32_t num_cores_y_ = 0;
    uint32_t num_compute_cores_ = 0;
    uint32_t mesh_width_ = 0;
    uint32_t mesh_height_ = 0;
    bool dram_on_noc_ = false;
    DRAMRoutingPolicy policy_ = DRAMRoutingPolicy::NEAREST;

    std::vector<NodePos> core_positions_;
    std::vector<NodePos> dram_positions_;
    std::vector<core_id_t> core_noc_ids_;
    std::vector<core_id_t> dram_noc_ids_;
    std::unordered_map<core_id_t, int32_t> noc_to_core_;
    std::unordered_map<core_id_t, int32_t> noc_to_dram_;
    mutable uint32_t rr_counter_ = 0;
};

}  // namespace npu_sim

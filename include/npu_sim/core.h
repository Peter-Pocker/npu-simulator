#pragma once

#include "npu_sim/types.h"
#include "npu_sim/config.h"
#include "npu_sim/task.h"
#include "npu_sim/packet.h"
#include "npu_sim/sram.h"

#include <queue>
#include <unordered_set>
#include <unordered_map>
#include <vector>
#include <functional>
#include <sstream>

namespace npu_sim {

struct StateTraceEntry {
    cycle_t cycle;
    CoreState old_state;
    CoreState new_state;
    uint32_t workload_idx;
    std::string layer_name;
    std::string detail;
};

struct CoreStats {
    cycle_t cycles_idle = 0;
    cycle_t cycles_loading = 0;
    cycle_t cycles_computing = 0;
    cycle_t cycles_writeback = 0;
    cycle_t cycles_stall_noc = 0;

    uint64_t total_mac_ops = 0;
    uint64_t total_data_loaded_bytes = 0;
    uint64_t total_data_stored_bytes = 0;
    uint32_t workloads_completed = 0;

    uint64_t peak_sram_usage_bytes = 0;
};

class NPUCore {
public:
    NPUCore(core_id_t id, const CoreConfig& core_cfg,
            const SRAMConfig& sram_cfg,
            const NetworkInterfaceConfig& ni_cfg,
            uint32_t element_size_bits);

    void load_workloads(std::vector<Workload>&& workloads);
    void tick(cycle_t current_cycle);

    void receive_packet(const Packet& pkt);
    bool has_outgoing_packet() const;
    Packet pop_outgoing_packet();

    void set_trace_enabled(bool enabled) { trace_enabled_ = enabled; }
    const std::vector<StateTraceEntry>& trace() const { return trace_; }

    core_id_t id() const { return id_; }
    CoreState state() const { return state_; }
    bool is_done() const { return state_ == CoreState::DONE; }
    const CoreStats& stats() const { return stats_; }
    const SRAM& sram() const { return sram_; }
    size_t pending_loads_count() const { return pending_loads_.size(); }

private:
    void record_transition(CoreState old_s, CoreState new_s,
                           const std::string& detail = "");
    void process_incoming_packets();
    void serve_pending_remote_requests();
    void try_start_next_workload();
    void issue_fetch_requests();
    bool all_buffers_loaded() const;
    cycle_t calculate_compute_cycles() const;
    void mark_outputs_available();
    void issue_writeback_requests();
    bool all_writebacks_complete() const;
    void update_sram_stats();
    void free_consumed_buffers();

    core_id_t id_;
    CoreState state_ = CoreState::IDLE;

    CoreConfig core_cfg_;
    uint32_t element_size_bits_;

    SRAM sram_;
    uint32_t max_outstanding_reqs_;
    uint32_t injection_queue_capacity_;
    uint32_t ejection_queue_capacity_;

    std::vector<Workload> workload_queue_;
    size_t current_workload_idx_ = 0;

    // Loading state: transfer_ids we're still waiting for
    std::unordered_set<transfer_id_t> pending_loads_;
    // Tracks which buffer sources have already been requested
    std::unordered_set<transfer_id_t> requested_transfers_;

    // Computing state
    cycle_t compute_remaining_ = 0;

    // Writeback state: transfers to DRAM still in progress
    std::unordered_set<transfer_id_t> pending_writebacks_;
    bool writeback_requests_issued_ = false;

    // NoC queues
    std::queue<Packet> injection_queue_;
    std::queue<Packet> ejection_queue_;

    // Remote read requests from other cores waiting for data we haven't produced yet
    std::vector<Packet> pending_remote_requests_;

    CoreStats stats_;
    cycle_t current_cycle_ = 0;

    bool trace_enabled_ = false;
    std::vector<StateTraceEntry> trace_;
};

}  // namespace npu_sim

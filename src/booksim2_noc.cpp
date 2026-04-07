#include "npu_sim/booksim2_noc.h"

#include <iostream>
#include <sstream>
#include <cmath>
#include <cassert>
#include <algorithm>

#include "booksim.hpp"
#include "booksim_config.hpp"
#include "network.hpp"
#include "buffer_state.hpp"
#include "flit.hpp"
#include "credit.hpp"
#include "routefunc.hpp"
#include "random_utils.hpp"
#include "globals.hpp"

// BookSim2 globals (normally defined in main.cpp, which we exclude from the lib)
int gK = 0;
int gN = 0;
int gC = 0;
int gNodes = 0;
bool gPrintActivity = false;
bool gTrace = false;
std::ostream* gWatchOut = nullptr;
TrafficManager* trafficManager = nullptr;

static int s_booksim_time = 0;

int GetSimTime() {
    return s_booksim_time;
}

class Stats;
Stats* GetStats(const std::string& /*name*/) {
    return nullptr;
}

namespace npu_sim {

BookSim2NoC::BookSim2NoC() = default;

BookSim2NoC::~BookSim2NoC() {
    for (auto& subnet_bufs : buf_states_) {
        for (auto* bs : subnet_bufs) {
            delete bs;
        }
    }
    for (auto* net : networks_) {
        delete net;
    }
    delete bs_config_;
}

void BookSim2NoC::init(uint32_t num_nodes, const NoCConfig& config,
                       uint32_t mesh_width, uint32_t mesh_height) {
    num_nodes_ = num_nodes;
    noc_config_ = config;

    init_booksim(config.booksim2_config_path, num_nodes, mesh_width, mesh_height);
}

void BookSim2NoC::init_booksim(const std::string& config_path, uint32_t num_nodes,
                               uint32_t mesh_w, uint32_t mesh_h) {
    bs_config_ = new BookSimConfig();

    if (!config_path.empty()) {
        bs_config_->ParseFile(config_path);
    }

    // Override / set all DSE-relevant parameters from NoCConfig,
    // regardless of whether an external file was parsed.
    bs_config_->Assign("num_vcs", static_cast<int>(noc_config_.num_vcs));
    bs_config_->Assign("vc_buf_size", static_cast<int>(noc_config_.vc_buf_size));
    bs_config_->Assign("routing_delay", static_cast<int>(noc_config_.routing_delay));
    bs_config_->Assign("vc_alloc_delay", static_cast<int>(noc_config_.vc_alloc_delay));
    bs_config_->Assign("sw_alloc_delay", static_cast<int>(noc_config_.sw_alloc_delay));
    bs_config_->Assign("st_final_delay", static_cast<int>(noc_config_.st_final_delay));
    bs_config_->Assign("deadlock_warn_timeout", static_cast<int>(noc_config_.deadlock_warn_timeout));

    // Fixed parameters for our use case
    bs_config_->Assign("topology", std::string("mesh"));
    bs_config_->Assign("routing_function", std::string("dim_order"));
    bs_config_->Assign("injection_rate", 0.0);

    // When no external file was loaded, also set sensible defaults
    // for parameters that the file would normally provide.
    if (config_path.empty()) {
        bs_config_->Assign("router", std::string("iq"));
        bs_config_->Assign("vc_allocator", std::string("islip"));
        bs_config_->Assign("sw_allocator", std::string("islip"));
        bs_config_->Assign("alloc_iters", 1);
        bs_config_->Assign("subnets", 1);
        bs_config_->Assign("output_delay", 0);
        bs_config_->Assign("credit_delay", 0);
        bs_config_->Assign("classes", 1);
        bs_config_->Assign("use_read_write", 0);
        bs_config_->Assign("packet_size", 1);
        bs_config_->Assign("hold_switch_for_packet", 0);
        bs_config_->Assign("seed", 0);
        bs_config_->Assign("print_activity", 0);
        bs_config_->Assign("viewer_trace", 0);
        bs_config_->Assign("sim_type", std::string("latency"));
        bs_config_->Assign("traffic", std::string("uniform"));
    }

    uint32_t w, h;
    if (mesh_w > 0 && mesh_h > 0) {
        w = mesh_w;
        h = mesh_h;
    } else {
        w = static_cast<uint32_t>(std::ceil(std::sqrt(num_nodes)));
        h = w;
    }

    uint32_t k = std::max(w, h);
    bs_config_->Assign("k", static_cast<int>(k));
    bs_config_->Assign("n", 2);
    bs_config_->Assign("x", static_cast<int>(w));
    bs_config_->Assign("y", static_cast<int>(h));

    gK = bs_config_->GetInt("k");
    gN = bs_config_->GetInt("n");
    gC = bs_config_->GetInt("c");
    gNodes = static_cast<int>(w * h);

    InitializeRoutingMap(*bs_config_);

    subnets_ = bs_config_->GetInt("subnets");
    vcs_ = bs_config_->GetInt("num_vcs");
    classes_ = bs_config_->GetInt("classes");

    networks_.resize(subnets_);
    for (int i = 0; i < subnets_; ++i) {
        std::ostringstream name;
        name << "network_" << i;
        networks_[i] = Network::New(*bs_config_, name.str());
    }

    uint32_t total = w * h;
    buf_states_.resize(total);
    last_vc_.resize(total, std::vector<int>(subnets_, 0));
    for (uint32_t n = 0; n < total; ++n) {
        buf_states_[n].resize(subnets_);
        for (int s = 0; s < subnets_; ++s) {
            std::ostringstream bs_name;
            bs_name << "terminal_buf_" << n << "_" << s;
            buf_states_[n][s] = new BufferState(*bs_config_, nullptr, bs_name.str());
            const FlitChannel* inject_ch = networks_[s]->GetInject(n);
            if (inject_ch) {
                buf_states_[n][s]->SetMinLatency(inject_ch->GetLatency());
            }
        }
    }

    injection_queues_.resize(total);
    delivered_this_cycle_.resize(total);

    RandomSeed(0);

    std::string src_desc = config_path.empty() ? "inline config" : config_path;
    std::cout << "[BookSim2NoC] Initialized " << w << "x" << h
              << " mesh (" << total << " nodes), " << vcs_ << " VCs, "
              << subnets_ << " subnets (from " << src_desc << ")\n";
}

bool BookSim2NoC::can_inject(core_id_t node_id) const {
    if (node_id < 0 || node_id >= static_cast<core_id_t>(num_nodes_)) return false;
    return injection_queues_[node_id].size() <
           static_cast<size_t>(noc_config_.injection_queue_depth);
}

bool BookSim2NoC::inject(const Packet& pkt) {
    if (pkt.src < 0 || pkt.src >= static_cast<core_id_t>(num_nodes_) ||
        pkt.dst < 0 || pkt.dst >= static_cast<core_id_t>(num_nodes_)) {
        return false;
    }

    if (!can_inject(pkt.src)) return false;

    uint32_t num_flits = pkt.num_flits(noc_config_.flit_size_bytes);
    if (num_flits == 0) num_flits = 1;

    int pid = next_packet_id_++;

    InFlightPacketInfo info;
    info.original_pkt = pkt;
    info.original_pkt.inject_cycle = cycle_;
    info.num_flits = static_cast<int>(num_flits);
    in_flight_packets_[pid] = info;

    Flit::FlitType ftype = Flit::ANY_TYPE;

    for (uint32_t i = 0; i < num_flits; ++i) {
        Flit* f = Flit::New();
        f->id = next_flit_id_++;
        f->pid = pid;
        f->src = pkt.src;
        f->dest = pkt.dst;
        f->type = ftype;
        f->cl = 0;
        f->subnetwork = 0;
        f->head = (i == 0);
        f->tail = (i == num_flits - 1);
        f->vc = -1;
        f->pri = 0;
        f->ctime = static_cast<int>(cycle_);
        f->watch = false;
        f->record = false;
        f->hops = 0;
        f->data = nullptr;

        injection_queues_[pkt.src].push_back(f);
    }

    return true;
}

int BookSim2NoC::allocate_vc(int source, int subnet, Flit* head_flit) {
    BufferState* dest_buf = buf_states_[source][subnet];
    int vc_start = 0;
    int vc_end = vcs_ - 1;
    int vc_count = vc_end - vc_start + 1;

    for (int i = 1; i <= vc_count; ++i) {
        int lvc = last_vc_[source][subnet];
        int vc = (lvc < vc_start || lvc > vc_end) ?
                 vc_start :
                 (vc_start + (lvc - vc_start + i) % vc_count);

        if (dest_buf->IsAvailableFor(vc) && !dest_buf->IsFullFor(vc)) {
            last_vc_[source][subnet] = vc;
            return vc;
        }
    }
    return -1;
}

void BookSim2NoC::tick_network() {
    s_booksim_time = static_cast<int>(cycle_);

    std::vector<std::vector<Flit*>> ejected(subnets_);
    for (int s = 0; s < subnets_; ++s) {
        ejected[s].resize(buf_states_.size(), nullptr);
        for (size_t n = 0; n < buf_states_.size(); ++n) {
            Flit* f = networks_[s]->ReadFlit(static_cast<int>(n));
            if (f) {
                f->atime = static_cast<int>(cycle_);
                ejected[s][n] = f;
            }

            Credit* c = networks_[s]->ReadCredit(static_cast<int>(n));
            if (c) {
                buf_states_[n][s]->ProcessCredit(c);
                c->Free();
            }
        }
        networks_[s]->ReadInputs();
    }

    for (size_t n = 0; n < injection_queues_.size(); ++n) {
        auto& queue = injection_queues_[n];
        if (queue.empty()) continue;

        for (int s = 0; s < subnets_; ++s) {
            if (queue.empty()) break;

            Flit* f = queue.front();
            if (f->subnetwork != s) continue;

            BufferState* dest_buf = buf_states_[n][s];

            if (f->head && f->vc == -1) {
                int vc = allocate_vc(static_cast<int>(n), s, f);
                if (vc == -1) continue;
                f->vc = vc;
            }

            if (f->vc == -1) continue;
            if (dest_buf->IsFullFor(f->vc)) continue;

            queue.pop_front();

            if (f->head) {
                dest_buf->TakeBuffer(f->vc);
            }
            dest_buf->SendingFlit(f);

            f->itime = static_cast<int>(cycle_);

            if (!queue.empty() && !f->tail) {
                Flit* nf = queue.front();
                if (nf->pid == f->pid) {
                    nf->vc = f->vc;
                }
            }

            networks_[s]->WriteFlit(f, static_cast<int>(n));
        }
    }

    for (int s = 0; s < subnets_; ++s) {
        for (size_t n = 0; n < buf_states_.size(); ++n) {
            Flit* f = ejected[s][n];
            if (!f) continue;

            Credit* c = Credit::New();
            c->vc.insert(f->vc);
            networks_[s]->WriteCredit(c, static_cast<int>(n));

            if (f->tail) {
                auto it = in_flight_packets_.find(f->pid);
                if (it != in_flight_packets_.end()) {
                    Packet delivered_pkt = it->second.original_pkt;
                    delivered_pkt.deliver_cycle = cycle_;
                    int dest = static_cast<int>(n);
                    if (dest < static_cast<int>(delivered_this_cycle_.size())) {
                        delivered_this_cycle_[dest].push_back(delivered_pkt);
                    }
                    in_flight_packets_.erase(it);
                }
            }

            f->Free();
        }

        networks_[s]->Evaluate();
        networks_[s]->WriteOutputs();
    }
}

void BookSim2NoC::tick() {
    for (auto& v : delivered_this_cycle_) {
        v.clear();
    }

    double ratio = noc_config_.clock_ratio;
    if (ratio <= 0.0) ratio = 1.0;

    tick_accum_ += 1.0;
    while (tick_accum_ >= ratio) {
        tick_network();
        tick_accum_ -= ratio;
    }

    cycle_++;
}

std::vector<Packet> BookSim2NoC::get_delivered_packets(core_id_t node_id) {
    if (node_id < 0 || node_id >= static_cast<core_id_t>(delivered_this_cycle_.size())) {
        return {};
    }
    return delivered_this_cycle_[node_id];
}

}  // namespace npu_sim

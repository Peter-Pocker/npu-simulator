#include "npu_sim/ramulator2_dram.h"

#include <iostream>
#include <stdexcept>
#include <cassert>

#include "base/base.h"
#include "base/request.h"
#include "base/config.h"
#include "frontend/frontend.h"
#include "memory_system/memory_system.h"

namespace npu_sim {

Ramulator2DRAM::Ramulator2DRAM() = default;

Ramulator2DRAM::~Ramulator2DRAM() {
    if (frontend_) {
        frontend_->finalize();
    }
    if (memory_system_) {
        memory_system_->finalize();
    }
}

void Ramulator2DRAM::init(const DRAMConfig& config) {
    config_ = config;

    std::string yaml_path = config.config_path;
    if (yaml_path.empty()) {
        yaml_path = "configs/ramulator2_ddr4.yaml";
    }

    YAML::Node yaml_config = Ramulator::Config::parse_config_file(yaml_path, {});

    frontend_ = Ramulator::Factory::create_frontend(yaml_config);
    memory_system_ = Ramulator::Factory::create_memory_system(yaml_config);

    frontend_->connect_memory_system(memory_system_);
    memory_system_->connect_frontend(frontend_);

    clock_ratio_ = frontend_->get_clock_ratio();
    if (clock_ratio_ < 1) clock_ratio_ = 1;

    std::cout << "[Ramulator2DRAM] Initialized with config: " << yaml_path
              << ", clock_ratio=" << clock_ratio_ << "\n";
}

bool Ramulator2DRAM::send_request(const Packet& pkt) {
    int req_type;
    if (pkt.type == PacketType::READ_REQUEST) {
        req_type = 0;  // Ramulator::Request::Type::Read
    } else if (pkt.type == PacketType::WRITE_REQUEST) {
        req_type = 1;  // Ramulator::Request::Type::Write
    } else {
        return false;
    }

    int64_t addr = static_cast<int64_t>(pkt.transfer_id) * 64;

    int source_id = static_cast<int>(pkt.src);
    if (source_id < 0) source_id = 0;

    bool is_read = (req_type == 0);

    bool accepted = frontend_->receive_external_requests(
        req_type, addr, source_id,
        [this, addr, is_read](Ramulator::Request& /*req*/) {
            if (is_read) {
                on_read_complete(addr);
            } else {
                on_write_complete(addr);
            }
        }
    );

    if (accepted) {
        OutstandingRequest oreq;
        oreq.pkt = pkt;
        oreq.pkt.inject_cycle = cycle_;
        oreq.is_read = is_read;
        outstanding_.insert({addr, oreq});
    }

    return accepted;
}

void Ramulator2DRAM::on_read_complete(int64_t addr) {
    auto it = outstanding_.find(addr);
    if (it == outstanding_.end()) return;

    Packet resp = it->second.pkt;
    resp.type = PacketType::READ_RESPONSE;
    resp.deliver_cycle = cycle_;
    std::swap(resp.src, resp.dst);

    completed_responses_.push_back(resp);
    outstanding_.erase(it);
}

void Ramulator2DRAM::on_write_complete(int64_t addr) {
    auto it = outstanding_.find(addr);
    if (it == outstanding_.end()) return;

    Packet resp = it->second.pkt;
    resp.type = PacketType::WRITE_RESPONSE;
    resp.deliver_cycle = cycle_;
    std::swap(resp.src, resp.dst);

    completed_responses_.push_back(resp);
    outstanding_.erase(it);
}

void Ramulator2DRAM::tick() {
    memory_system_->tick();
    cycle_++;
}

std::vector<Packet> Ramulator2DRAM::get_responses() {
    std::vector<Packet> responses;
    responses.swap(completed_responses_);
    return responses;
}

}  // namespace npu_sim

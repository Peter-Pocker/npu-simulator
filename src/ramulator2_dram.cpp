#include "npu_sim/ramulator2_dram.h"

#include <iostream>
#include <iomanip>
#include <sstream>
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
    cache_line_size_ = config_.resolved_cache_line_size();

    YAML::Node yaml_config;
    std::string src_desc;

    if (!config.config_path.empty()) {
        yaml_config = Ramulator::Config::parse_config_file(config.config_path, {});
        src_desc = config.config_path;
    } else {
        // 用 YAML 字符串再解析生成 Node，避免直接 node["k"]=v 构建带来的类型/结构歧义；若仍 key not found 则多为库内 LPDDR5 preset/SpecDef 查找问题
        uint32_t ch = config.num_channels > 0 ? config.num_channels : 4u;
        uint32_t rk = config.num_ranks;
        std::ostringstream yaml_str;
        yaml_str << "Frontend:\n  impl: GEM5\n  clock_ratio: " << static_cast<int>(config.frontend_clock_ratio)
                 << "\nMemorySystem:\n  impl: GenericDRAM\n  clock_ratio: 1\n  DRAM:\n    impl: "
                 << config.standard << "\n    org:\n      preset: " << config.org
                 << "\n      channel: " << ch << "\n      rank: " << rk
                 << "\n    timing:\n      preset: " << config.timing
                 << "\n  Controller:\n    impl: Generic\n    Scheduler:\n      impl: " << config.scheduler
                 << "\n    RefreshManager:\n      impl: AllBank\n  AddrMapper:\n    impl: " << config.addr_mapper;
        if (config.addr_mapper == "CustomizedMapper" && !config.addr_mapping.empty()) {
            yaml_str << "\n    mapping: " << config.addr_mapping;
        }
        yaml_str << "\n";
        yaml_config = YAML::Load(yaml_str.str());
        src_desc = "inline config (" + config.standard + " " + config.timing + ")";
    }

    if (config_.num_channels > 0) {
        try {
            yaml_config["MemorySystem"]["DRAM"]["org"]["channel"] = static_cast<int>(config_.num_channels);
        } catch (...) {}
    }

    try {
        frontend_ = Ramulator::Factory::create_frontend(yaml_config);
        memory_system_ = Ramulator::Factory::create_memory_system(yaml_config);
    } catch (const std::exception& e) {
        throw std::runtime_error(
            "[Ramulator2DRAM] init failed. If you see 'key not found', the linked libramulator may not have preset '"
            + config_.org + "' or '" + config_.timing + "'. Use dram.backend=\"simple\" in config, or rebuild Ramulator2. Original: " + e.what());
    }

    frontend_->connect_memory_system(memory_system_);
    memory_system_->connect_frontend(frontend_);

    clock_ratio_ = frontend_->get_clock_ratio();
    if (clock_ratio_ < 1) clock_ratio_ = 1;

    if (config_.clock_ratio <= 0.0) config_.clock_ratio = 1.0;

    std::cout << "[Ramulator2DRAM] Initialized from " << src_desc
              << ", yaml_clock_ratio=" << clock_ratio_
              << ", sim_clock_ratio=" << config_.clock_ratio
              << ", channels=" << config_.num_channels
              << ", cache_line=" << cache_line_size_ << "B"
              << "\n";
}

bool Ramulator2DRAM::send_request(const Packet& pkt) {
    if (pkt.type != PacketType::READ_REQUEST &&
        pkt.type != PacketType::WRITE_REQUEST) {
        return false;
    }

    uint32_t num_lines = static_cast<uint32_t>(
        (pkt.data_size_bytes + cache_line_size_ - 1) / cache_line_size_);
    if (num_lines == 0) num_lines = 1;

    if (config_.log_dram) {
        std::cout << "[Ramulator2DRAM] cycle " << cycle_
                  << " accept " << (pkt.type == PacketType::READ_REQUEST ? "READ" : "WRITE")
                  << " tid=" << pkt.transfer_id << " addr=0x" << std::hex << pkt.address << std::dec
                  << " size=" << pkt.data_size_bytes << " subreqs=" << num_lines << "\n";
    }

    uint64_t bid = next_burst_id_++;
    BurstGroup group;
    group.burst_id = bid;
    group.original_pkt = pkt;
    group.base_addr = pkt.address;
    group.total_lines = num_lines;
    group.injected = 0;
    group.completed = 0;
    group.is_read = (pkt.type == PacketType::READ_REQUEST);

    bursts_.emplace(bid, std::move(group));
    injection_order_.push_back(bid);
    return true;
}

void Ramulator2DRAM::inject_pending_subrequests() {
    auto it = injection_order_.begin();
    while (it != injection_order_.end()) {
        auto burst_it = bursts_.find(*it);
        if (burst_it == bursts_.end()) {
            it = injection_order_.erase(it);
            continue;
        }

        auto& burst = burst_it->second;
        bool made_progress = true;

        while (burst.injected < burst.total_lines && made_progress) {
            int64_t addr = static_cast<int64_t>(
                burst.base_addr + static_cast<uint64_t>(burst.injected) * cache_line_size_);
            int req_type = burst.is_read ? 0 : 1;

            int source_id = static_cast<int>(burst.original_pkt.src);
            if (source_id < 0) source_id = 0;

            uint64_t bid_capture = burst.burst_id;
            bool accepted = frontend_->receive_external_requests(
                req_type, addr, source_id,
                [this, bid_capture](Ramulator::Request& /*req*/) {
                    on_subrequest_complete(bid_capture);
                }
            );

            if (accepted) {
                burst.injected++;
                total_subreqs_++;
            } else {
                made_progress = false;
            }
        }

        if (burst.injected >= burst.total_lines) {
            // 写请求：一旦全部子请求被 Ramulator2 接受进请求队列即发送 WRITE_RESPONSE，
            // 不等待数据实际写入 DRAM（与“接受即确认”的语义一致，便于 core 尽早释放缓冲）。
            if (burst.is_read) {
                it = injection_order_.erase(it);
                // 读请求仍等待 on_subrequest_complete（数据返回）再发 READ_RESPONSE
            } else {
                if (config_.log_dram) {
                    std::cout << "[Ramulator2DRAM] cycle " << cycle_
                              << " WRITE queued (all " << burst.total_lines << " subreqs) tid="
                              << burst.original_pkt.transfer_id << " -> core " << burst.original_pkt.src << "\n";
                }
                Packet resp = burst.original_pkt;
                resp.type = PacketType::WRITE_RESPONSE;
                resp.deliver_cycle = cycle_;
                std::swap(resp.src, resp.dst);
                completed_responses_.push_back(resp);
                bursts_.erase(burst_it);
                it = injection_order_.erase(it);
            }
        } else {
            break;
        }
    }
}

void Ramulator2DRAM::on_subrequest_complete(uint64_t burst_id) {
    auto it = bursts_.find(burst_id);
    // 写请求的 burst 已在全部子请求被接受时移除并发送 WRITE_RESPONSE，此处可能已不存在
    if (it == bursts_.end()) return;

    auto& burst = it->second;
    burst.completed++;

    if (burst.completed >= burst.total_lines) {
        if (config_.log_dram && burst.is_read) {
            std::cout << "[Ramulator2DRAM] cycle " << cycle_
                      << " READ complete tid=" << burst.original_pkt.transfer_id
                      << " -> core " << burst.original_pkt.src << "\n";
        }
        Packet resp = burst.original_pkt;
        resp.type = burst.is_read ? PacketType::READ_RESPONSE : PacketType::WRITE_RESPONSE;
        resp.deliver_cycle = cycle_;
        std::swap(resp.src, resp.dst);
        completed_responses_.push_back(resp);
        bursts_.erase(it);
    }
}

void Ramulator2DRAM::tick() {
    inject_pending_subrequests();

    double ratio = config_.clock_ratio;
    if (ratio <= 0.0) ratio = 1.0;

    tick_accum_ += 1.0;
    while (tick_accum_ >= ratio) {
        memory_system_->tick();
        tick_accum_ -= ratio;
    }
    cycle_++;
}

std::vector<Packet> Ramulator2DRAM::get_responses() {
    std::vector<Packet> responses;
    responses.swap(completed_responses_);
    return responses;
}

}  // namespace npu_sim

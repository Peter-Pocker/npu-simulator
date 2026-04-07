#include "npu_sim/gemini_parser.h"
#include "npu_sim/ir_parser.h"
#include <algorithm>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <set>
#include <unordered_set>
#include <vector>

namespace npu_sim {

std::unique_ptr<IRParser> IRParser::create(const std::string& format) {
    if (format == "gemini") {
        return std::make_unique<GeminiParser>();
    }
    throw std::runtime_error("Unknown IR format: " + format);
}

IRData GeminiParser::parse(const std::string& path) {
    std::ifstream ifs(path);
    if (!ifs.is_open()) {
        throw std::runtime_error("Cannot open IR file: " + path);
    }

    nlohmann::json root;
    ifs >> root;

    IRData ir;
    ir.num_cores_x = root.value("xlen", 0u);
    ir.num_cores_y = root.value("ylen", 0u);
    ir.top_batch_cut = root.value("top_batch_cut", 1u);

    is_scheduler_ir_ = root.contains("buffersize");
    if (is_scheduler_ir_) {
        ir.required_sram_bytes = root.value("buffersize", 0ul);
    }

    uint32_t num_cores = ir.num_cores_y * ir.num_cores_x;
    if (num_cores == 0) num_cores = 1;
    ir.core_workloads.resize(num_cores);

    // First pass: collect all core IDs that have workloads
    std::vector<int> raw_core_ids;
    for (auto& [key, value] : root.items()) {
        if (key == "xlen" || key == "ylen" || key == "top_batch_cut" ||
            key == "buffersize" || key == "-1") continue;
        int cid = -1;
        try { cid = std::stoi(key); } catch (...) { continue; }
        if (cid >= 0 && value.is_array() && !value.empty()) {
            raw_core_ids.push_back(cid);
        }
    }
    std::sort(raw_core_ids.begin(), raw_core_ids.end());

    // Build compact remap: GEMINI xyid → 0-based index
    core_id_remap_.clear();
    bool needs_remap = false;
    for (uint32_t i = 0; i < raw_core_ids.size(); ++i) {
        core_id_remap_[static_cast<uint32_t>(raw_core_ids[i])] = i;
        if (static_cast<uint32_t>(raw_core_ids[i]) != i) needs_remap = true;
    }

    uint32_t compact_cores = static_cast<uint32_t>(raw_core_ids.size());
    if (compact_cores > num_cores) {
        ir.core_workloads.resize(compact_cores);
    }

    for (auto& [key, value] : root.items()) {
        if (key == "xlen" || key == "ylen" || key == "top_batch_cut" ||
            key == "buffersize") continue;

        if (key == "-1") {
            parse_dram_section(value, ir);
            continue;
        }

        int core_id = -1;
        try { core_id = std::stoi(key); } catch (...) { continue; }
        if (core_id < 0) continue;

        uint32_t mapped_id = core_id;
        auto it = core_id_remap_.find(static_cast<uint32_t>(core_id));
        if (it != core_id_remap_.end()) mapped_id = it->second;

        if (mapped_id >= ir.core_workloads.size()) {
            ir.core_workloads.resize(mapped_id + 1);
        }

        if (!value.is_array()) continue;

        for (auto& wl_json : value) {
            ir.core_workloads[mapped_id].push_back(parse_workload(wl_json));
        }
    }

    if (needs_remap) {
        compact_core_ids(ir);
        std::cout << "[GeminiParser] Remapped " << raw_core_ids.size()
                  << " GEMINI core IDs to compact 0-based indices\n";
    }

    topological_sort_workloads(ir);
    fix_output_ref_counts(ir);
    return ir;
}

Workload GeminiParser::parse_workload(const nlohmann::json& wl_json) {
    Workload wl;
    wl.id = wl_json.value("workload_id", 0u);
    wl.layer_name = wl_json.value("layer_name", "");

    std::string layer_type = wl_json.value("layer_type", "custom");
    if (is_scheduler_ir_) {
        wl.op_type = infer_operator_type(layer_type, wl.layer_name);
    } else {
        wl.op_type = string_to_operator_type(layer_type);
    }

    if (wl_json.contains("workload") && wl_json["workload"].is_array() &&
        wl_json["workload"].size() == 2) {
        auto& lower = wl_json["workload"][0];
        auto& upper = wl_json["workload"][1];
        for (int i = 0; i < 4 && i < static_cast<int>(lower.size()); ++i) {
            wl.ofmap_range.lower[i] = lower[i].get<uint32_t>();
            wl.ofmap_range.upper[i] = upper[i].get<uint32_t>();
        }
    }

    uint64_t raw_ofmap = wl_json.value("ofmap_size", 0ul);
    wl.ofmap_size_bytes = is_scheduler_ir_ ? raw_ofmap : (raw_ofmap / 8);

    if (wl_json.contains("weight") && wl_json["weight"].is_object() &&
        wl_json["weight"].contains("size")) {
        uint64_t raw_wt = wl_json["weight"]["size"].get<uint64_t>();
        wl.weight_size_bytes = is_scheduler_ir_ ? raw_wt : (raw_wt / 8);
    }

    wl.analytical_time = wl_json.value("time", 0ul);

    // Collect ofmap transfer_ids to identify output buffers
    std::set<transfer_id_t> ofmap_tids;
    if (wl_json.contains("ofmap") && wl_json["ofmap"].is_array()) {
        for (auto& ofmap_json : wl_json["ofmap"]) {
            wl.outputs.push_back(parse_output(ofmap_json));
            ofmap_tids.insert(wl.outputs.back().transfer_id);
        }
    }

    if (is_scheduler_ir_) {
        // Use ifmap entries for actual data dependencies
        if (wl_json.contains("ifmap") && wl_json["ifmap"].is_array()) {
            for (auto& ifm_json : wl_json["ifmap"]) {
                BufferRequirement buf;
                buf.data_type = DataType::IFMAP;
                buf.size_bytes = ifm_json.value("size", 0ul);

                if (ifm_json.contains("transfer_id") && ifm_json["transfer_id"].is_array()) {
                    for (auto& tid_json : ifm_json["transfer_id"]) {
                        transfer_id_t tid = tid_json.get<transfer_id_t>();
                        buf.transfer_ids.push_back(tid);

                        DataSource ds;
                        ds.transfer_id = tid;
                        ds.size_bytes = buf.size_bytes;
                        ds.layer_name = "";

                        // Determine source: check buffer entries for this tid
                        bool found_source = false;
                        if (wl_json.contains("buffer") && wl_json["buffer"].is_array()) {
                            for (auto& b : wl_json["buffer"]) {
                                if (!b.contains("source") || !b["source"].is_array()) continue;
                                for (auto& s : b["source"]) {
                                    if (s.value("transfer_id", 0u) == tid) {
                                        std::string stype = s.value("type", "");
                                        ds.type = (stype == "DRAM") ? SourceType::DRAM : SourceType::CORE;
                                        ds.source_id = (ds.type == SourceType::DRAM) ? DRAM_ID
                                                       : s.value("core_id", 0);
                                        ds.size_bytes = s.value("size", buf.size_bytes);
                                        ds.layer_name = s.value("layer_name", "");
                                        found_source = true;
                                        break;
                                    }
                                }
                                if (found_source) break;
                            }
                        }
                        if (!found_source) {
                            ds.type = SourceType::DRAM;
                            ds.source_id = DRAM_ID;
                        }
                        buf.sources.push_back(ds);
                    }
                }
                wl.buffers.push_back(std::move(buf));
            }
        }

        // Synthesize weight fetch buffer
        if (wl_json.contains("weight") && wl_json["weight"].is_object()) {
            auto& wt = wl_json["weight"];
            if (wt.contains("transfer_id") && wt["transfer_id"].is_array() &&
                !wt["transfer_id"].empty()) {
                BufferRequirement wbuf;
                wbuf.data_type = DataType::WEIGHT;
                wbuf.source_layer = wl.layer_name;
                wbuf.size_bytes = wt.value("size", 0ul);

                for (auto& tid_json : wt["transfer_id"]) {
                    transfer_id_t tid = tid_json.get<transfer_id_t>();
                    wbuf.transfer_ids.push_back(tid);

                    DataSource ds;
                    ds.type = SourceType::DRAM;
                    ds.source_id = DRAM_ID;
                    ds.size_bytes = wbuf.size_bytes;
                    ds.transfer_id = tid;
                    ds.layer_name = wl.layer_name;
                    wbuf.sources.push_back(ds);
                }
                wl.buffers.push_back(std::move(wbuf));
            }
        }
    } else {
        // Old format: use buffer entries directly
        if (wl_json.contains("buffer") && wl_json["buffer"].is_array()) {
            for (auto& buf_json : wl_json["buffer"]) {
                wl.buffers.push_back(parse_buffer(buf_json));
            }
        }
    }

    return wl;
}

BufferRequirement GeminiParser::parse_buffer(const nlohmann::json& buf_json) {
    BufferRequirement buf;

    std::string type_str = buf_json.value("type", "");
    buf.data_type = (type_str == "weight") ? DataType::WEIGHT : DataType::IFMAP;

    if (is_scheduler_ir_) {
        buf.source_layer = buf_json.value("layer_name", "");
        buf.source_workload_id = buf_json.value("workload_id", 0u);
        buf.size_bytes = buf_json.value("size", 0ul);
    } else {
        buf.source_layer = buf_json.value("layer", "");
        buf.source_workload_id = buf_json.value("workload_id", 0u);
        buf.block_kb = buf_json.value("block", 0ul);
        buf.size_bytes = buf.block_kb * 1024;
    }

    if (buf_json.contains("source") && buf_json["source"].is_array()) {
        for (auto& src_json : buf_json["source"]) {
            DataSource ds;
            std::string src_type = src_json.value("type", "");
            ds.type = (src_type == "DRAM") ? SourceType::DRAM : SourceType::CORE;

            if (is_scheduler_ir_) {
                ds.source_id = src_json.value("core_id", 0);
                ds.size_bytes = src_json.value("size", 0ul);
            } else {
                ds.source_id = src_json.value("id", 0);
                ds.size_bytes = src_json.value("size", 0ul) / 8;
            }

            ds.transfer_id = src_json.value("transfer_id", 0u);
            ds.layer_name = src_json.value("layer_name", "");
            buf.sources.push_back(ds);
        }
    }

    if (buf_json.contains("transfer_id") && buf_json["transfer_id"].is_array()) {
        for (auto& tid : buf_json["transfer_id"]) {
            if (tid.is_array()) {
                for (auto& inner : tid) {
                    buf.transfer_ids.push_back(inner.get<transfer_id_t>());
                }
            } else {
                buf.transfer_ids.push_back(tid.get<transfer_id_t>());
            }
        }
    }

    return buf;
}

OutputTransfer GeminiParser::parse_output(const nlohmann::json& ofmap_json) {
    OutputTransfer out;

    if (ofmap_json.contains("lower") && ofmap_json["lower"].is_array()) {
        for (int i = 0; i < 4 && i < static_cast<int>(ofmap_json["lower"].size()); ++i) {
            out.range.lower[i] = ofmap_json["lower"][i].get<uint32_t>();
        }
    }
    if (ofmap_json.contains("upper") && ofmap_json["upper"].is_array()) {
        for (int i = 0; i < 4 && i < static_cast<int>(ofmap_json["upper"].size()); ++i) {
            out.range.upper[i] = ofmap_json["upper"][i].get<uint32_t>();
        }
    }

    uint64_t raw_size = ofmap_json.value("size", 0ul);
    out.size_bytes = is_scheduler_ir_ ? raw_size : (raw_size / 8);
    out.transfer_id = ofmap_json.value("transfer_id", 0u);

    if (ofmap_json.contains("destination") && ofmap_json["destination"].is_array()) {
        for (auto& dest_json : ofmap_json["destination"]) {
            DataDestination dd;
            std::string dest_type = dest_json.value("type", "");
            dd.type = (dest_type == "DRAM") ? SourceType::DRAM : SourceType::CORE;

            if (is_scheduler_ir_) {
                dd.dest_id = dest_json.value("core_id", 0);
            } else {
                dd.dest_id = dest_json.value("id", 0);
            }

            dd.dest_workload_id = dest_json.value("workload_id", 0u);
            dd.layer_name = dest_json.value("layer_name", "");
            out.destinations.push_back(dd);
        }
    }

    return out;
}

void GeminiParser::parse_dram_section(const nlohmann::json& dram_json, IRData& ir_data) {
    if (dram_json.contains("out") && dram_json["out"].is_array()) {
        for (auto& entry : dram_json["out"]) {
            DRAMTransfer dt;
            std::string type_str = entry.value("type", "");
            dt.data_type = (type_str == "weight") ? DataType::WEIGHT : DataType::IFMAP;
            dt.layer_name = entry.value("layer_name", "");

            uint64_t raw_size = entry.value("size", 0ul);
            dt.size_bytes = is_scheduler_ir_ ? raw_size : (raw_size / 8);

            dt.transfer_id = entry.value("transfer_id", 0u);
            dt.is_read = true;

            if (entry.contains("destination") && entry["destination"].is_array() &&
                !entry["destination"].empty()) {
                auto& d = entry["destination"][0];
                if (is_scheduler_ir_) {
                    dt.core_id = d.value("core_id", 0);
                } else {
                    dt.core_id = d.value("id", 0);
                }
                dt.workload_id = d.value("workload_id", 0u);
            }
            ir_data.dram_reads.push_back(dt);
        }
    }

    if (dram_json.contains("in") && dram_json["in"].is_array()) {
        for (auto& entry : dram_json["in"]) {
            DRAMTransfer dt;
            std::string type_str = entry.value("type", "");
            dt.data_type = (type_str == "weight") ? DataType::WEIGHT : DataType::OFMAP;
            dt.layer_name = entry.value("layer_name", "");
            dt.transfer_id = entry.value("transfer_id", 0u);
            dt.core_id = entry.value("core_id", 0);
            dt.workload_id = entry.value("workload_id", 0u);
            dt.is_read = false;
            ir_data.dram_writes.push_back(dt);
        }
    }
}

void GeminiParser::topological_sort_workloads(IRData& ir_data) {
    for (auto& core_wls : ir_data.core_workloads) {
        if (core_wls.size() <= 1) continue;

        size_t n = core_wls.size();

        // Map transfer_id → index of workload that produces it
        std::unordered_map<transfer_id_t, size_t> tid_producer;
        for (size_t i = 0; i < n; ++i) {
            for (auto& out : core_wls[i].outputs) {
                tid_producer[out.transfer_id] = i;
            }
        }

        // Build adjacency list and in-degree
        std::vector<std::vector<size_t>> adj(n);
        std::vector<int> in_degree(n, 0);
        for (size_t i = 0; i < n; ++i) {
            std::unordered_set<size_t> deps;
            // Collect ofmap tids to filter output buffers
            std::set<transfer_id_t> my_ofmap_tids;
            for (auto& out : core_wls[i].outputs) {
                my_ofmap_tids.insert(out.transfer_id);
            }
            for (auto& buf : core_wls[i].buffers) {
                for (auto& src : buf.sources) {
                    if (src.type != SourceType::CORE) continue;
                    auto it = tid_producer.find(src.transfer_id);
                    if (it != tid_producer.end() && it->second != i) {
                        deps.insert(it->second);
                    }
                }
            }
            for (size_t dep : deps) {
                adj[dep].push_back(i);
                in_degree[i]++;
            }
        }

        // Kahn's algorithm - use original index as tiebreaker for stability
        std::vector<size_t> queue;
        for (size_t i = 0; i < n; ++i) {
            if (in_degree[i] == 0) queue.push_back(i);
        }

        std::vector<Workload> sorted;
        sorted.reserve(n);
        size_t head = 0;
        while (head < queue.size()) {
            size_t u = queue[head++];
            sorted.push_back(std::move(core_wls[u]));
            for (size_t v : adj[u]) {
                if (--in_degree[v] == 0) {
                    queue.push_back(v);
                }
            }
        }

        if (sorted.size() == n) {
            core_wls = std::move(sorted);
        }
    }
}

void GeminiParser::fix_output_ref_counts(IRData& ir_data) {
    for (auto& core_wls : ir_data.core_workloads) {
        // Count how many times each transfer_id is consumed as a buffer source
        std::unordered_map<transfer_id_t, uint32_t> ref_counts;
        for (auto& wl : core_wls) {
            for (auto& buf : wl.buffers) {
                for (auto& src : buf.sources) {
                    if (src.type == SourceType::CORE) {
                        ref_counts[src.transfer_id]++;
                    }
                }
            }
        }

        // Patch each workload's output destinations to reflect actual consumers
        for (auto& wl : core_wls) {
            for (auto& out : wl.outputs) {
                auto it = ref_counts.find(out.transfer_id);
                uint32_t actual_refs = (it != ref_counts.end()) ? it->second : 0;

                // Also count DRAM destinations
                uint32_t dram_dests = 0;
                uint32_t core_dests = 0;
                for (auto& d : out.destinations) {
                    if (d.type == SourceType::DRAM) dram_dests++;
                    else core_dests++;
                }

                if (actual_refs > core_dests) {
                    // More consumers than listed in destinations - add dummy entries
                    for (uint32_t i = core_dests; i < actual_refs; ++i) {
                        DataDestination dd;
                        dd.type = SourceType::CORE;
                        dd.dest_id = 0;
                        dd.dest_workload_id = 0;
                        out.destinations.push_back(dd);
                    }
                }
            }
        }
    }
}

void GeminiParser::compact_core_ids(IRData& ir_data) {
    auto remap = [this](uint32_t id) -> uint32_t {
        auto it = core_id_remap_.find(id);
        return (it != core_id_remap_.end()) ? it->second : id;
    };

    for (auto& core_wls : ir_data.core_workloads) {
        for (auto& wl : core_wls) {
            for (auto& out : wl.outputs) {
                for (auto& d : out.destinations) {
                    if (d.type == SourceType::CORE) {
                        d.dest_id = remap(d.dest_id);
                    }
                }
            }
            for (auto& buf : wl.buffers) {
                for (auto& src : buf.sources) {
                    if (src.type == SourceType::CORE) {
                        src.source_id = remap(src.source_id);
                    }
                }
            }
        }
    }

    for (auto& dt : ir_data.dram_reads) {
        dt.core_id = remap(dt.core_id);
    }
    for (auto& dt : ir_data.dram_writes) {
        dt.core_id = remap(dt.core_id);
    }
}

}  // namespace npu_sim

#pragma once

#include "npu_sim/ir_parser.h"
#include <nlohmann/json.hpp>

namespace npu_sim {

class GeminiParser : public IRParser {
public:
    GeminiParser() = default;
    IRData parse(const std::string& path) override;

private:
    bool is_scheduler_ir_ = false;

    Workload parse_workload(const nlohmann::json& wl_json);
    BufferRequirement parse_buffer(const nlohmann::json& buf_json);
    OutputTransfer parse_output(const nlohmann::json& ofmap_json);
    void parse_dram_section(const nlohmann::json& dram_json, IRData& ir_data);
    void fix_output_ref_counts(IRData& ir_data);
    void topological_sort_workloads(IRData& ir_data);
};

}  // namespace npu_sim

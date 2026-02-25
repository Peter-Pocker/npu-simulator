#pragma once

#include <cstdint>
#include <string>
#include <vector>
#include <array>

namespace npu_sim {

using cycle_t = uint64_t;
using core_id_t = int32_t;
using transfer_id_t = uint32_t;
using workload_id_t = uint32_t;

constexpr core_id_t DRAM_ID = -1;

enum class OperatorType {
    CONV2D,
    FC,
    POOLING,
    ELEMENT_WISE,
    POINT_TO_POINT,
    CUSTOM
};

inline std::string operator_type_to_string(OperatorType t) {
    switch (t) {
        case OperatorType::CONV2D:        return "conv2d";
        case OperatorType::FC:            return "fc";
        case OperatorType::POOLING:       return "pool";
        case OperatorType::ELEMENT_WISE:  return "element_wise";
        case OperatorType::POINT_TO_POINT:return "point_to_point";
        case OperatorType::CUSTOM:        return "custom";
    }
    return "unknown";
}

inline OperatorType string_to_operator_type(const std::string& s) {
    if (s == "conv2d")        return OperatorType::CONV2D;
    if (s == "fc")            return OperatorType::FC;
    if (s == "pool")          return OperatorType::POOLING;
    if (s == "element_wise")  return OperatorType::ELEMENT_WISE;
    if (s == "point_to_point")return OperatorType::POINT_TO_POINT;
    return OperatorType::CUSTOM;
}

/// Infer OperatorType from Scheduler IR layer_type ("pe"/"vp"/"dt") and layer_name prefix
inline OperatorType infer_operator_type(const std::string& layer_type,
                                        const std::string& layer_name) {
    std::string prefix;
    auto pos = layer_name.find('_');
    if (pos != std::string::npos) {
        prefix = layer_name.substr(0, pos);
    } else {
        prefix = layer_name;
    }

    if (layer_type == "pe") {
        if (prefix == "Conv")                return OperatorType::CONV2D;
        if (prefix == "Gemm")                return OperatorType::FC;
        return OperatorType::CONV2D;
    }
    if (layer_type == "vp") {
        if (prefix == "MaxPool" || prefix == "AveragePool" ||
            prefix == "GlobalAveragePool")   return OperatorType::POOLING;
        if (prefix == "Add" || prefix == "Relu" ||
            prefix == "Mul" || prefix == "Sub") return OperatorType::ELEMENT_WISE;
        return OperatorType::POINT_TO_POINT;
    }
    if (layer_type == "dt") {
        return OperatorType::POINT_TO_POINT;
    }

    return string_to_operator_type(layer_type);
}

enum class CoreState {
    IDLE,
    LOADING,
    COMPUTING,
    WRITEBACK,
    DONE
};

enum class SourceType {
    CORE,
    DRAM
};

enum class PacketType {
    READ_REQUEST,
    READ_RESPONSE,
    WRITE_REQUEST,
    WRITE_RESPONSE
};

enum class DataType {
    IFMAP,
    WEIGHT,
    OFMAP
};

}  // namespace npu_sim

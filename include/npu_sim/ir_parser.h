#pragma once

#include "npu_sim/task.h"
#include <string>
#include <memory>

namespace npu_sim {

class IRParser {
public:
    virtual ~IRParser() = default;
    virtual IRData parse(const std::string& path) = 0;

    static std::unique_ptr<IRParser> create(const std::string& format);
};

}  // namespace npu_sim

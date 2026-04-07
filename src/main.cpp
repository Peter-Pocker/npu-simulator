#include "npu_sim/config.h"
#include "npu_sim/simulator.h"

#include <iostream>
#include <string>

int main(int argc, char* argv[]) {
    std::string config_path;
    std::string ir_path;
    bool trace_enabled = false;
    std::string trace_output = "trace";
    bool log_dram = false;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if ((arg == "-c" || arg == "--config") && i + 1 < argc) {
            config_path = argv[++i];
        } else if ((arg == "-i" || arg == "--ir") && i + 1 < argc) {
            ir_path = argv[++i];
        } else if (arg == "-t" || arg == "--trace") {
            trace_enabled = true;
            if (i + 1 < argc && argv[i + 1][0] != '-') {
                trace_output = argv[++i];
            }
        } else if (arg == "--log-dram") {
            log_dram = true;
        } else if (arg == "-h" || arg == "--help") {
            std::cout << "Usage: npu_sim [options]\n"
                      << "  -c, --config <path>   Path to config JSON file\n"
                      << "  -i, --ir <path>        Path to IR JSON file\n"
                      << "  -t, --trace [dir]      Enable state trace (default: ./trace/)\n"
                      << "  --log-dram             Print DRAM request/response logs (for Ramulator2 test)\n"
                      << "  -h, --help             Show this help\n";
            return 0;
        }
    }

    try {
        npu_sim::SimConfig config;
        if (!config_path.empty()) {
            config = npu_sim::SimConfig::load_from_json(config_path);
        }
        if (!ir_path.empty()) {
            config.ir_path = ir_path;
        }
        if (log_dram) {
            config.log_dram = true;
        }

        if (config.ir_path.empty()) {
            std::cerr << "Error: IR path not specified. Use -i <path> or set ir_path in config.\n";
            return 1;
        }

        npu_sim::Simulator sim(config);
        if (trace_enabled) {
            sim.set_trace_enabled(true);
            sim.set_trace_output(trace_output);
        }
        sim.load_ir(config.ir_path);
        if (!sim.run()) {
            sim.print_stats();
            return 1;
        }
        sim.print_stats();

        if (trace_enabled) {
            sim.export_trace();
        }

    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << "\n";
        return 1;
    }

    return 0;
}

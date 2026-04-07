cmake_minimum_required(VERSION 3.16)
project(npu_simulator VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ── Ramulator2 (pre-built shared library) ──
set(RAMULATOR2_DIR ${CMAKE_SOURCE_DIR}/third_party/ramulator2)
add_library(ramulator2 SHARED IMPORTED)
set_target_properties(ramulator2 PROPERTIES
    IMPORTED_LOCATION ${RAMULATOR2_DIR}/libramulator.dylib
)

# ── BookSim2 (pre-built static library) ──
set(BOOKSIM2_DIR ${CMAKE_SOURCE_DIR}/third_party/booksim2/src)
add_library(booksim2 STATIC IMPORTED)
set_target_properties(booksim2 PROPERTIES
    IMPORTED_LOCATION ${BOOKSIM2_DIR}/libbooksim.a
)

# ── NPU Simulator ──
add_executable(npu_sim
    src/main.cpp
    src/sram.cpp
    src/core.cpp
    src/gemini_parser.cpp
    src/simulator.cpp
    src/booksim2_noc.cpp
    src/ramulator2_dram.cpp
    src/address_allocator.cpp
)

target_include_directories(npu_sim PRIVATE
    ${CMAKE_SOURCE_DIR}/include
    ${CMAKE_SOURCE_DIR}/third_party
    ${BOOKSIM2_DIR}
    ${BOOKSIM2_DIR}/arbiters
    ${BOOKSIM2_DIR}/allocators
    ${BOOKSIM2_DIR}/routers
    ${BOOKSIM2_DIR}/networks
    ${BOOKSIM2_DIR}/power
    ${RAMULATOR2_DIR}/src
    ${RAMULATOR2_DIR}/ext/yaml-cpp/include
    ${RAMULATOR2_DIR}/ext/spdlog/include
)

target_link_libraries(npu_sim PRIVATE
    booksim2
    ramulator2
)
# 运行时优先从 third_party/ramulator2 加载 libramulator.dylib（LPDDR5 等需本仓库编译的库）
set_target_properties(npu_sim PROPERTIES
    BUILD_RPATH "${RAMULATOR2_DIR}"
    INSTALL_RPATH "${RAMULATOR2_DIR}"
)
if(APPLE)
    set_target_properties(npu_sim PROPERTIES
        BUILD_RPATH "@executable_path/../third_party/ramulator2;${RAMULATOR2_DIR}"
        INSTALL_RPATH "@executable_path/../third_party/ramulator2;${RAMULATOR2_DIR}"
    )
endif()

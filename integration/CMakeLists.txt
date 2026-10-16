cmake_minimum_required(VERSION 3.21)
project(TarzanPS2 LANGUAGES C CXX)

set(TARZAN_ROOT "${CMAKE_CURRENT_LIST_DIR}/..")
set(TARZAN_PS2RECOMP_SOURCE_DIR "${TARZAN_ROOT}/source/PS2Recomp" CACHE PATH
    "Path to the upstream PS2Recomp checkout")
set(TARZAN_GENERATED_DIR "${TARZAN_ROOT}/generated/SLES-50350-symbolmap-entry" CACHE PATH
    "Directory containing generated C++ and headers for the guest ELF")
set(PS2X_DEFAULT_BOOT_ELF "${TARZAN_ROOT}/extracted/disc/SLES_503.50" CACHE STRING
    "Default guest ELF for the Tarzan native launcher")

foreach(required_file IN ITEMS
    "${TARZAN_GENERATED_DIR}/register_functions.cpp"
    "${TARZAN_GENERATED_DIR}/ps2_recompiled_functions.h"
    "${TARZAN_GENERATED_DIR}/ps2_recompiled_stubs.h")
    if(NOT EXISTS "${required_file}")
        message(FATAL_ERROR "Generated game output is incomplete: ${required_file}")
    endif()
endforeach()

set(PS2X_BUILD_RECOMP OFF CACHE BOOL "" FORCE)
set(PS2X_BUILD_RUNTIME ON CACHE BOOL "" FORCE)
set(PS2X_BUILD_ANALYZER OFF CACHE BOOL "" FORCE)
set(PS2X_BUILD_TEST OFF CACHE BOOL "" FORCE)
set(PS2X_BUILD_STUDIO OFF CACHE BOOL "" FORCE)
set(PS2X_ENABLE_DEBUG_UI OFF CACHE BOOL "" FORCE)
set(PS2X_ENABLE_FFMPEG ON CACHE BOOL "" FORCE)
set(PS2X_ENABLE_RUNTIME_LOGS ON CACHE BOOL "" FORCE)
set(PS2X_ENABLE_AGRESSIVE_LOGS OFF CACHE BOOL "" FORCE)
set(PS2X_ENABLE_IOP_RPC_TRACE OFF CACHE BOOL "" FORCE)
set(PS2X_ENABLE_RUNNER_UNITY_BUILD ON CACHE BOOL "" FORCE)
set(PS2X_RUNNER_UNITY_BUILD_BATCH_SIZE 32 CACHE STRING "" FORCE)

add_subdirectory("${TARZAN_PS2RECOMP_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/ps2recomp")

# CMake's option de-duplication collapses repeated `-framework` tokens from
# pkg-config's static FFmpeg flags. Keep each framework and its name grouped.
if(TARGET PkgConfig::FFMPEG)
    set_property(TARGET PkgConfig::FFMPEG PROPERTY INTERFACE_LINK_OPTIONS
        "-pthread"
        "SHELL:-framework CoreFoundation"
        "SHELL:-framework CoreVideo"
        "SHELL:-framework CoreMedia")
endif()

if(NOT TARGET ps2EntryRunner OR NOT TARGET ps2_runtime)
    message(FATAL_ERROR "Upstream PS2Recomp did not define its runtime targets")
endif()

set(TARZAN_EMPTY_FUNCTION_TABLE
    "${TARZAN_PS2RECOMP_SOURCE_DIR}/ps2xRuntime/src/runner/register_functions.cpp")
set(TARZAN_UPSTREAM_RUNNER_MAIN
    "${TARZAN_PS2RECOMP_SOURCE_DIR}/ps2xRuntime/src/main.cpp")
get_target_property(PS2X_RUNNER_SOURCES ps2EntryRunner SOURCES)
set(TARZAN_FILTERED_RUNNER_SOURCES)
foreach(runner_source IN LISTS PS2X_RUNNER_SOURCES)
    if(runner_source MATCHES "(^|/)runner/register_functions\\.cpp$" OR
       runner_source MATCHES "(^|/)src/main\\.cpp$")
        continue()
    endif()
    list(APPEND TARZAN_FILTERED_RUNNER_SOURCES "${runner_source}")
endforeach()
set(PS2X_RUNNER_SOURCES "${TARZAN_FILTERED_RUNNER_SOURCES}")
list(APPEND PS2X_RUNNER_SOURCES "${CMAKE_CURRENT_LIST_DIR}/launcher.cpp")
set_property(TARGET ps2EntryRunner PROPERTY SOURCES "${PS2X_RUNNER_SOURCES}")

file(GLOB TARZAN_GENERATED_SOURCES CONFIGURE_DEPENDS
    "${TARZAN_GENERATED_DIR}/*.cpp")
if(NOT TARZAN_GENERATED_SOURCES)
    message(FATAL_ERROR "No generated C++ files found in ${TARZAN_GENERATED_DIR}")
endif()
target_sources(ps2EntryRunner PRIVATE ${TARZAN_GENERATED_SOURCES})
target_include_directories(ps2EntryRunner BEFORE PRIVATE "${TARZAN_GENERATED_DIR}")
target_precompile_headers(ps2EntryRunner PRIVATE
    "${TARZAN_GENERATED_DIR}/ps2_recompiled_functions.h"
    "${TARZAN_GENERATED_DIR}/ps2_recompiled_stubs.h")

set_source_files_properties("${TARZAN_GENERATED_DIR}/register_functions.cpp" PROPERTIES
    SKIP_UNITY_BUILD_INCLUSION ON
    COMPILE_OPTIONS "-O1;-fno-lto;-fno-fast-math;-ffp-contract=off")
set_target_properties(ps2EntryRunner PROPERTIES
    UNITY_BUILD ON
    UNITY_BUILD_BATCH_SIZE 32
    INTERPROCEDURAL_OPTIMIZATION_RELEASE OFF)
set_target_properties(ps2_runtime PROPERTIES
    INTERPROCEDURAL_OPTIMIZATION_RELEASE OFF)
target_compile_options(ps2_runtime PRIVATE
    "$<$<CONFIG:Release>:-O2>"
    -fno-lto -fno-fast-math -ffp-contract=off)
target_compile_options(ps2EntryRunner PRIVATE
    "$<$<CONFIG:Release>:-O1>"
    -fno-lto -fno-fast-math -ffp-contract=off)

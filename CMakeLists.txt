cmake_minimum_required(VERSION 3.21)
project(TarzanPS2 LANGUAGES C CXX OBJC OBJCXX)

if(NOT APPLE)
    message(FATAL_ERROR "This project builds only on macOS (Metal)")
endif()

get_filename_component(TARZAN_ROOT "${CMAKE_CURRENT_LIST_DIR}" ABSOLUTE)
option(TARZAN_BUILD_GAME "Build the game runner and Metal host (OFF: upstream tools/tests only)" ON)
set(TARZAN_PS2RECOMP_SOURCE_DIR "${TARZAN_ROOT}/deps/ps2recomp")
set(TARZAN_GENERATED_DIR "${TARZAN_ROOT}/generated/SLES-50350-symbolmap-entry" CACHE PATH
    "Directory containing generated C++ and headers for the guest ELF")
set(PS2X_DEFAULT_BOOT_ELF "extracted/disc/SLES_503.50" CACHE STRING
    "Default guest ELF, relative to the checkout (found at run time by walking up from the executable), or absolute")

enable_testing()

if(NOT TARZAN_BUILD_GAME)
    add_subdirectory("${TARZAN_PS2RECOMP_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/ps2recomp")
    if(TARGET ps2_runtime)
        add_subdirectory(host)
    endif()
    include("${TARZAN_ROOT}/cmake/ps2x-tests.cmake")
    include("${TARZAN_ROOT}/cmake/relative-rpath.cmake")
    cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL tarzan_apply_relative_rpaths)
    return()
endif()

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

set(PS2X_VU0_CATALOG_DIR "${TARZAN_ROOT}/runtime/vu0-catalog" CACHE PATH "Captured VU0 catalogs")
set(PS2X_VU0_GENERATOR "${TARZAN_ROOT}/tools/vu/vu1recomp.py" CACHE FILEPATH "VU static generator" FORCE)
set(PS2X_VU0GEN_DIR "${TARZAN_ROOT}/generated/vu0-native" CACHE PATH "Native VU0 source directory")
set(PS2X_VU1GEN_DIR "${TARZAN_ROOT}/generated/vu1-disc-native" CACHE PATH "Disc-derived guarded VU1 source directory")
add_subdirectory("${TARZAN_PS2RECOMP_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/ps2recomp")

# Keep the canonical disc-derived guarded VU1 source current during incremental
# Ninja builds; custom VU1 generator directories remain entirely user-owned.
get_filename_component(TARZAN_VU1GEN_CANONICAL "${TARZAN_ROOT}/generated/vu1-disc-native" REALPATH)
get_filename_component(TARZAN_VU1GEN_SELECTED "${PS2X_VU1GEN_DIR}" REALPATH)
if(TARZAN_VU1GEN_SELECTED STREQUAL TARZAN_VU1GEN_CANONICAL)
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    set(TARZAN_VU1GEN_STAMP "${CMAKE_CURRENT_BINARY_DIR}/vu1-disc-native-generated.stamp")
    add_custom_command(OUTPUT "${TARZAN_VU1GEN_STAMP}"
        COMMAND "${Python3_EXECUTABLE}" "${TARZAN_ROOT}/tools/disc.py" --vu1-only
        COMMAND "${CMAKE_COMMAND}" -E touch "${TARZAN_VU1GEN_STAMP}"
        DEPENDS "${TARZAN_ROOT}/extracted/disc/SLES_503.50"
                "${TARZAN_ROOT}/tools/disc.py"
                "${TARZAN_ROOT}/tools/vu/extract-vu-microcode.py"
                "${TARZAN_ROOT}/tools/vu/vu1recomp.py"
                "${TARZAN_ROOT}/tools/vu/vu1decode.py"
        BYPRODUCTS "${PS2X_VU1GEN_DIR}/vu1rec_guarded.cpp"
        VERBATIM)
    add_custom_target(tarzan_generate_disc_vu1 DEPENDS "${TARZAN_VU1GEN_STAMP}")
    add_dependencies(ps2EntryRunner tarzan_generate_disc_vu1)
    if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
        # Explicit NEON already supplies vector math; these passes scale poorly
        # on the large guarded CFG. Keep O2 and strict floating-point semantics.
        set_property(SOURCE "${PS2X_VU1GEN_DIR}/vu1rec_guarded.cpp"
            TARGET_DIRECTORY ps2EntryRunner APPEND PROPERTY COMPILE_OPTIONS
            "-fno-slp-vectorize;-mllvm;-misched-regpressure=false")
    endif()
endif()

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
list(APPEND PS2X_RUNNER_SOURCES "${TARZAN_ROOT}/host/launcher.cpp")
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
# First-mission load profiles identify these integer texture conversion loops.
# Optimize only these translation units; preserve guest checkpoints and arithmetic.
foreach(texture_converter IN ITEMS
    "BlockConv8to32__FPUcPUc_0x403df0.cpp"
    "PageConv8to32__FiiPUcPUc_0x404370.cpp"
    "Conv8to32__FiiPUcPUc_0x404d40.cpp")
    set_source_files_properties("${TARZAN_GENERATED_DIR}/${texture_converter}"
        TARGET_DIRECTORY ps2EntryRunner PROPERTIES
        SKIP_UNITY_BUILD_INCLUSION ON
        COMPILE_OPTIONS "-O2;-fno-lto;-fno-fast-math;-ffp-contract=off")
endforeach()
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

# Game configuration only: the Metal host library, its tests and smoke runners.
add_subdirectory(host)

include("${TARZAN_ROOT}/cmake/relative-rpath.cmake")
cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL tarzan_apply_relative_rpaths)

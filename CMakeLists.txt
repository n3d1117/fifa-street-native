cmake_minimum_required(VERSION 3.21)
project(Ps2NativePort LANGUAGES C CXX OBJC OBJCXX)

if(NOT APPLE)
    message(FATAL_ERROR "This project builds only on macOS (Metal)")
endif()

get_filename_component(RX_ROOT "${CMAKE_CURRENT_LIST_DIR}" ABSOLUTE)
include("${RX_ROOT}/cmake/ccache.cmake")
set(RX_PS2RECOMP_SOURCE_DIR "${RX_ROOT}/deps/ps2recomp")

# Game identity: config/game.json is the one source (also read by rx and tools/).
file(READ "${RX_ROOT}/config/game.json" RX_GAME_JSON)
string(JSON RX_GAME_ID GET "${RX_GAME_JSON}" id)
string(JSON RX_GAME_ELF GET "${RX_GAME_JSON}" elf)
string(JSON RX_APP_NAME GET "${RX_GAME_JSON}" app_name)
string(JSON RX_APP_EXECUTABLE GET "${RX_GAME_JSON}" executable)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${RX_ROOT}/config/game.json")

set(RX_GENERATED_DIR "${RX_ROOT}/generated/${RX_GAME_ID}" CACHE PATH
    "Directory containing generated C++ and headers for the guest ELF")
set(PS2X_DEFAULT_BOOT_ELF "extracted/disc/${RX_GAME_ELF}" CACHE STRING
    "Default guest ELF, relative to the checkout (found at run time by walking up from the executable), or absolute" FORCE)

# Ninja job pools: translation units that take minutes and gigabytes are
# serialised (JOB_POOL_COMPILE heavy) while the rest of the build runs at the host-derived width (tools/jobs.py).
set_property(GLOBAL APPEND PROPERTY JOB_POOLS heavy=1)

enable_testing()

# One project, one build tree: the game (ps2EntryRunner + Metal host), the
# recompiler (ps2_recomp) and every test binary are targets of this configure.
# The generated guest sources only exist after the recompiler has run once, so
# until then only the recompiler and the tests are configured as live targets;
# `rx build` reconfigures after generating.
set(RX_GAME_READY ON)
foreach(required_file IN ITEMS
    "${RX_GENERATED_DIR}/register_functions.cpp"
    "${RX_GENERATED_DIR}/ps2_recompiled_functions.h"
    "${RX_GENERATED_DIR}/ps2_recompiled_stubs.h")
    if(NOT EXISTS "${required_file}")
        set(RX_GAME_READY OFF)
    endif()
endforeach()
set(RX_GAME_CONFIGURED ${RX_GAME_READY} CACHE INTERNAL "read by rx" FORCE)
if(NOT RX_GAME_READY)
    message(STATUS "Generated game output not found in ${RX_GENERATED_DIR}: configuring recompiler and tests only")
endif()

set(PS2X_BUILD_RECOMP ON CACHE BOOL "" FORCE)
set(PS2X_BUILD_RUNTIME ON CACHE BOOL "" FORCE)
set(PS2X_BUILD_ANALYZER ON CACHE BOOL "" FORCE)
set(PS2X_BUILD_TEST ON CACHE BOOL "" FORCE)
set(PS2X_IOP_BUILD_TESTS ON CACHE BOOL "" FORCE)
set(PS2X_BUILD_STUDIO OFF CACHE BOOL "" FORCE)
set(PS2X_USE_METAL ON)
set(PS2X_ENABLE_DEBUG_UI OFF CACHE BOOL "" FORCE)
set(PS2X_ENABLE_FFMPEG ON CACHE BOOL "" FORCE)
set(PS2X_ENABLE_RUNTIME_LOGS ON CACHE BOOL "" FORCE)
set(PS2X_ENABLE_AGRESSIVE_LOGS OFF CACHE BOOL "" FORCE)
set(PS2X_ENABLE_IOP_RPC_TRACE OFF CACHE BOOL "" FORCE)
set(PS2X_ENABLE_RUNNER_UNITY_BUILD ON CACHE BOOL "" FORCE)
set(PS2X_RUNNER_UNITY_BUILD_BATCH_SIZE 32 CACHE STRING "" FORCE)

if(NOT PS2X_VU1_GENERATOR)
    set(PS2X_VU1_GENERATOR "${RX_ROOT}/tools/vu/vu1recomp.py" CACHE FILEPATH
        "VU static generator" FORCE)
endif()
set(PS2X_VU1_PROGRAM_EXTRACTOR "${RX_ROOT}/tools/vu/extract-programs.py" CACHE FILEPATH
    "VU1 program extractor" FORCE)
set(PS2X_VU1_PROGRAM_DIR "${RX_ROOT}/generated/vu1-programs" CACHE PATH
    "Extracted VU1 microprograms" FORCE)
set(PS2X_DISC_DIR "${RX_ROOT}/extracted/disc" CACHE PATH "Extracted disc root" FORCE)
if(NOT PS2X_VU1GEN_DIR)
    set(PS2X_VU1GEN_DIR "${RX_ROOT}/generated/vu1-native" CACHE PATH
        "Native VU1 source directory" FORCE)
endif()

find_package(Python3 COMPONENTS Interpreter REQUIRED)
add_test(NAME vu_generator_tests
    COMMAND "${Python3_EXECUTABLE}" -B -m unittest discover
        -s "${RX_ROOT}/tools/vu/test" -p "test_*.py")
set_tests_properties(vu_generator_tests PROPERTIES LABELS tools)

add_subdirectory("${RX_PS2RECOMP_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/ps2recomp")
set_property(SOURCE "${RX_PS2RECOMP_SOURCE_DIR}/ps2xRuntime/src/lib/ps2_pad.cpp"
    TARGET_DIRECTORY ps2_runtime APPEND PROPERTY INCLUDE_DIRECTORIES "${RX_ROOT}/host")

# Test targets: no LTO, strict floating point like the shipped runtime, and the
# shipped PS2X_USE_METAL configuration (see cmake/ps2x-tests.cmake).
include("${RX_ROOT}/cmake/ps2x-tests.cmake")
foreach(tool_target IN ITEMS ps2_recomp ps2_recomp_lib ps2_analyzer ps2_analyzer_lib ps2_test_lib ps2x_tests)
    if(TARGET ${tool_target})
        set_property(TARGET ${tool_target} PROPERTY INTERPROCEDURAL_OPTIMIZATION_RELEASE OFF)
    endif()
endforeach()

# The Metal host library and smoke runners (game configuration only) and the
# host unit tests (always).
add_subdirectory(host)

if(NOT RX_GAME_READY)
    set_target_properties(ps2EntryRunner PROPERTIES EXCLUDE_FROM_ALL ON)
    include("${RX_ROOT}/cmake/relative-rpath.cmake")
    cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL rx_apply_relative_rpaths)
    return()
endif()

if(NOT TARGET ps2EntryRunner OR NOT TARGET ps2_runtime)
    message(FATAL_ERROR "Upstream PS2Recomp did not define its runtime targets")
endif()

get_target_property(PS2X_RUNNER_SOURCES ps2EntryRunner SOURCES)
set(RX_FILTERED_RUNNER_SOURCES)
foreach(runner_source IN LISTS PS2X_RUNNER_SOURCES)
    if(runner_source MATCHES "(^|/)runner/register_functions\\.cpp$" OR
       runner_source MATCHES "(^|/)src/main\\.cpp$")
        continue()
    endif()
    list(APPEND RX_FILTERED_RUNNER_SOURCES "${runner_source}")
endforeach()
list(APPEND RX_FILTERED_RUNNER_SOURCES "${RX_ROOT}/host/launcher.cpp" "${RX_ROOT}/host/boot-match.cpp"
    "${RX_ROOT}/host/poll-idle.cpp")
set_property(TARGET ps2EntryRunner PROPERTY SOURCES "${RX_FILTERED_RUNNER_SOURCES}")
# The target lives in the ps2recomp directory: source properties must be set in its scope.
set_source_files_properties("${RX_ROOT}/host/boot-match.cpp" "${RX_ROOT}/host/poll-idle.cpp"
    TARGET_DIRECTORY ps2EntryRunner PROPERTIES
    SKIP_UNITY_BUILD_INCLUSION ON)
set_source_files_properties("${RX_ROOT}/host/launcher.cpp" TARGET_DIRECTORY ps2EntryRunner PROPERTIES
    SKIP_UNITY_BUILD_INCLUSION ON
    COMPILE_DEFINITIONS "PS2X_APP_NAME=\"${RX_APP_NAME}\";PS2X_APP_EXECUTABLE=\"${RX_APP_EXECUTABLE}\"")

file(GLOB RX_GENERATED_SOURCES CONFIGURE_DEPENDS
    "${RX_GENERATED_DIR}/*.cpp")
if(NOT RX_GENERATED_SOURCES)
    message(FATAL_ERROR "No generated C++ files found in ${RX_GENERATED_DIR}")
endif()
target_sources(ps2EntryRunner PRIVATE ${RX_GENERATED_SOURCES})
target_include_directories(ps2EntryRunner BEFORE PRIVATE "${RX_GENERATED_DIR}")
target_precompile_headers(ps2EntryRunner PRIVATE
    "${RX_GENERATED_DIR}/ps2_recompiled_functions.h"
    "${RX_GENERATED_DIR}/ps2_recompiled_stubs.h")

set_source_files_properties("${RX_GENERATED_DIR}/register_functions.cpp" PROPERTIES
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

include("${RX_ROOT}/cmake/relative-rpath.cmake")
cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL rx_apply_relative_rpaths)

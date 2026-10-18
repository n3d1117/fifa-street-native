# Include after tarzanMetalHost is declared. SDL is used only for its
# Gamepad/Joystick subsystem; no SDL video or raylib window backend is enabled.
get_filename_component(TARZAN_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
if(DEFINED ENV{TARZAN_NATIVE_TOOLS_ROOT} AND NOT "$ENV{TARZAN_NATIVE_TOOLS_ROOT}" STREQUAL "")
    set(_tarzan_native_tools_default "$ENV{TARZAN_NATIVE_TOOLS_ROOT}")
else()
    set(_tarzan_native_tools_default "${TARZAN_ROOT}")
endif()
set(TARZAN_NATIVE_TOOLS_ROOT "${_tarzan_native_tools_default}" CACHE PATH
    "Root of the local compiler and build tools")

if(NOT TARGET tarzanMetalHost)
    message(FATAL_ERROR "gamepad.cmake must be included after tarzanMetalHost")
endif()

set(TARZAN_SDL3_PREFIX "${TARZAN_ROOT}/tools/metal-deps/prefix" CACHE PATH
    "Local SDL3 static dependency prefix")
if(NOT EXISTS "${TARZAN_SDL3_PREFIX}/lib/cmake/SDL3/SDL3Config.cmake")
    message(FATAL_ERROR "Missing pinned SDL3 package under ${TARZAN_SDL3_PREFIX}")
endif()

find_package(SDL3 CONFIG REQUIRED COMPONENTS SDL3-static
    PATHS "${TARZAN_SDL3_PREFIX}/lib/cmake/SDL3" NO_DEFAULT_PATH)
set(TARZAN_LLVM_AVAILABILITY_SOURCE
    "${TARZAN_ROOT}/tools/metal-deps/compat/llvm19-os_version_check.c")
if(NOT EXISTS "${TARZAN_LLVM_AVAILABILITY_SOURCE}")
    message(FATAL_ERROR "Missing local LLVM availability-check runtime source")
endif()
add_library(tarzanCompilerAvailability STATIC "${TARZAN_LLVM_AVAILABILITY_SOURCE}")
set_target_properties(tarzanCompilerAvailability PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_sources(tarzanMetalHost PRIVATE "${CMAKE_CURRENT_LIST_DIR}/gamepad-input.mm")
target_include_directories(tarzanMetalHost PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
target_link_libraries(tarzanMetalHost PRIVATE SDL3::SDL3-static tarzanCompilerAvailability)
set_property(SOURCE "${TARZAN_PS2RECOMP_SOURCE_DIR}/ps2xRuntime/src/lib/ps2_pad.cpp"
    TARGET_DIRECTORY ps2_runtime APPEND PROPERTY INCLUDE_DIRECTORIES "${CMAKE_CURRENT_LIST_DIR}")
set_source_files_properties("${CMAKE_CURRENT_LIST_DIR}/gamepad-input.mm"
    PROPERTIES COMPILE_OPTIONS "-fobjc-arc" SKIP_UNITY_BUILD_INCLUSION ON
    SKIP_PRECOMPILE_HEADERS ON)

# Standalone input smoke; it creates no SDL window or renderer.
add_executable(tarzanGamepadSmoke
    "${CMAKE_CURRENT_LIST_DIR}/gamepad-input.mm"
    "${CMAKE_CURRENT_LIST_DIR}/gamepad-smoke.mm")
target_include_directories(tarzanGamepadSmoke PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
target_compile_features(tarzanGamepadSmoke PRIVATE cxx_std_20)
target_compile_options(tarzanGamepadSmoke PRIVATE -stdlib=libc++ -nostdinc++
    -isystem "${TARZAN_NATIVE_TOOLS_ROOT}/tools/build/llvm19/include/c++/v1")
target_link_options(tarzanGamepadSmoke PRIVATE -stdlib=libc++)
target_link_libraries(tarzanGamepadSmoke PRIVATE SDL3::SDL3-static
    tarzanCompilerAvailability "-framework AppKit")
set_source_files_properties(
    "${CMAKE_CURRENT_LIST_DIR}/gamepad-input.mm"
    "${CMAKE_CURRENT_LIST_DIR}/gamepad-smoke.mm"
    PROPERTIES COMPILE_OPTIONS "-fobjc-arc" SKIP_UNITY_BUILD_INCLUSION ON
    SKIP_PRECOMPILE_HEADERS ON)

# Native Cocoa/Metal host for the recompiled game plus its tests. Included from
# the root project; the host library and runner wiring exist only in game builds.
set(TARZAN_METAL_BRIDGE "${TARZAN_ROOT}/build/metal-bridge/pcsx2/pcsx2-gs-bridge/librrv-pcsx2-gs-bridge.dylib"
    CACHE FILEPATH "Pinned PCSX2 GS bridge dylib")
if(NOT EXISTS "${TARZAN_METAL_BRIDGE}")
    message(FATAL_ERROR "Missing pinned PCSX2 Metal bridge: ${TARZAN_METAL_BRIDGE}")
endif()
set(TARZAN_SPU2_BACKEND "${TARZAN_ROOT}/build/spu2-backend/librrv_pcsx2_spu2.a"
    CACHE FILEPATH "Built pinned PCSX2 SPU2 static library")
if(NOT EXISTS "${TARZAN_SPU2_BACKEND}")
    message(FATAL_ERROR "Missing PCSX2 SPU2 backend; run tools/build-deps.sh: ${TARZAN_SPU2_BACKEND}")
endif()
set(TARZAN_SOUNDTOUCH_LIBRARY "${TARZAN_ROOT}/build/metal-bridge/pcsx2/3rdparty/soundtouch/libpcsx2-soundtouch.a"
    CACHE FILEPATH "Built pinned PCSX2 SoundTouch static library")
if(NOT EXISTS "${TARZAN_SOUNDTOUCH_LIBRARY}")
    message(FATAL_ERROR "Missing pinned PCSX2 SoundTouch library: ${TARZAN_SOUNDTOUCH_LIBRARY}")
endif()
set(TARZAN_LIBCXX_INCLUDE "${TARZAN_LLVM}/include/c++/v1")

if(TARZAN_BUILD_GAME)
    target_include_directories(ps2EntryRunner PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_compile_definitions(ps2_runtime PRIVATE TARZAN_USE_METAL=1)
    add_library(tarzanMetalHost STATIC
        "${CMAKE_CURRENT_SOURCE_DIR}/metal-host.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/keyboard-controls.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/experimental-settings.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/experimental-clock.cpp"
        "${CMAKE_CURRENT_SOURCE_DIR}/diagnostic-input.cpp"
        "${CMAKE_CURRENT_SOURCE_DIR}/spu2-host.cpp"
        "${CMAKE_CURRENT_SOURCE_DIR}/metal-gs-adapter.cpp")
    target_include_directories(tarzanMetalHost PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}"
        "${TARZAN_ROOT}/deps/fukami/tools/pcsx2-gs-bridge"
        "${TARZAN_ROOT}/deps/fukami/tools/pcsx2-spu2"
        "${TARZAN_ROOT}/deps/pcsx2/3rdparty/soundtouch/soundtouch"
        "${TARZAN_PS2RECOMP_SOURCE_DIR}/ps2xIOP/include")
    target_compile_features(tarzanMetalHost PRIVATE cxx_std_20)
    target_compile_options(tarzanMetalHost PRIVATE
        -stdlib=libc++ -nostdinc++ -isystem "${TARZAN_LIBCXX_INCLUDE}")
    set_target_properties(tarzanMetalHost PROPERTIES UNITY_BUILD OFF)
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/metal-host.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/keyboard-controls.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/experimental-settings.mm" PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc" SKIP_UNITY_BUILD_INCLUSION ON SKIP_PRECOMPILE_HEADERS ON)
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/metal-gs-adapter.cpp" PROPERTIES
        SKIP_UNITY_BUILD_INCLUSION ON SKIP_PRECOMPILE_HEADERS ON)
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/spu2-host.cpp" PROPERTIES
        SKIP_UNITY_BUILD_INCLUSION ON SKIP_PRECOMPILE_HEADERS ON)
    target_link_options(tarzanMetalHost PRIVATE -stdlib=libc++)
    target_link_libraries(tarzanMetalHost PUBLIC
        "${TARZAN_METAL_BRIDGE}"
        "-framework AppKit" "-framework QuartzCore" "-framework Metal" "-framework CoreGraphics")
    find_package(Threads REQUIRED)
    target_link_libraries(tarzanMetalHost PRIVATE "${TARZAN_SPU2_BACKEND}" Threads::Threads ps2_iop)
    target_link_libraries(tarzanMetalHost PRIVATE "${TARZAN_SOUNDTOUCH_LIBRARY}")
endif()

if(NOT TARZAN_BUILD_GAME)
    # Tests compile the shipped runtime configuration; see test-metal-seam.cpp.
    target_compile_definitions(ps2_runtime PUBLIC TARZAN_USE_METAL=1)
    add_library(tarzanTestMetalSeam STATIC "${CMAKE_CURRENT_SOURCE_DIR}/test-metal-seam.cpp")
    target_include_directories(tarzanTestMetalSeam PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_compile_features(tarzanTestMetalSeam PRIVATE cxx_std_20)
    target_link_libraries(ps2_runtime PUBLIC tarzanTestMetalSeam)
    set_property(SOURCE "${TARZAN_PS2RECOMP_SOURCE_DIR}/ps2xRuntime/src/lib/ps2_pad.cpp"
        TARGET_DIRECTORY ps2_runtime APPEND PROPERTY INCLUDE_DIRECTORIES "${CMAKE_CURRENT_SOURCE_DIR}")
endif()

add_executable(tarzanAudioStretcherTest
    "${TARZAN_ROOT}/deps/fukami/tools/pcsx2-spu2/rrv_audio_stretcher.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/audio-stretcher-test.cpp")
target_include_directories(tarzanAudioStretcherTest PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${TARZAN_ROOT}/deps/fukami/tools/pcsx2-spu2"
    "${TARZAN_ROOT}/deps/pcsx2/3rdparty/soundtouch/soundtouch")
target_compile_definitions(tarzanAudioStretcherTest PRIVATE SOUNDTOUCH_FLOAT_SAMPLES)
target_compile_features(tarzanAudioStretcherTest PRIVATE cxx_std_20)
target_link_options(tarzanAudioStretcherTest PRIVATE -stdlib=libc++)
target_link_libraries(tarzanAudioStretcherTest PRIVATE "${TARZAN_SOUNDTOUCH_LIBRARY}")
add_test(NAME tarzan_audio_stretcher COMMAND tarzanAudioStretcherTest)

if(TARZAN_BUILD_GAME)
    target_link_libraries(ps2EntryRunner PRIVATE tarzanMetalHost)
    add_executable(tarzanMetalHostSmoke "${CMAKE_CURRENT_SOURCE_DIR}/metal-host-smoke.cpp")
    target_include_directories(tarzanMetalHostSmoke PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_compile_features(tarzanMetalHostSmoke PRIVATE cxx_std_20)
    target_link_options(tarzanMetalHostSmoke PRIVATE -stdlib=libc++)
    target_link_libraries(tarzanMetalHostSmoke PRIVATE tarzanMetalHost)

    # SDL is used only for its Gamepad/Joystick subsystem; no SDL video or
    # raylib window backend is enabled.
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
    target_sources(tarzanMetalHost PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/gamepad-input.mm")
    target_include_directories(tarzanMetalHost PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_link_libraries(tarzanMetalHost PRIVATE SDL3::SDL3-static tarzanCompilerAvailability)
    set_property(SOURCE "${TARZAN_PS2RECOMP_SOURCE_DIR}/ps2xRuntime/src/lib/ps2_pad.cpp"
        TARGET_DIRECTORY ps2_runtime APPEND PROPERTY INCLUDE_DIRECTORIES "${CMAKE_CURRENT_SOURCE_DIR}")
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/gamepad-input.mm"
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc" SKIP_UNITY_BUILD_INCLUSION ON
        SKIP_PRECOMPILE_HEADERS ON)

    # Standalone input smoke; it creates no SDL window or renderer.
    add_executable(tarzanGamepadSmoke
        "${CMAKE_CURRENT_SOURCE_DIR}/gamepad-input.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/gamepad-smoke.mm")
    target_include_directories(tarzanGamepadSmoke PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_compile_features(tarzanGamepadSmoke PRIVATE cxx_std_20)
    target_compile_options(tarzanGamepadSmoke PRIVATE -stdlib=libc++ -nostdinc++
        -isystem "${TARZAN_LIBCXX_INCLUDE}")
    target_link_options(tarzanGamepadSmoke PRIVATE -stdlib=libc++)
    target_link_libraries(tarzanGamepadSmoke PRIVATE SDL3::SDL3-static
        tarzanCompilerAvailability "-framework AppKit")
    set_source_files_properties(
        "${CMAKE_CURRENT_SOURCE_DIR}/gamepad-input.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/gamepad-smoke.mm"
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc" SKIP_UNITY_BUILD_INCLUSION ON
        SKIP_PRECOMPILE_HEADERS ON)

    set_property(TARGET ps2EntryRunner APPEND PROPERTY INSTALL_RPATH
        "@loader_path/../Frameworks" "@loader_path/../Resources/pcsx2-gs-bridge")

    add_test(NAME tarzan_metal_host_smoke COMMAND tarzanMetalHostSmoke)
    add_test(NAME tarzan_gamepad_smoke COMMAND tarzanGamepadSmoke)
    set_tests_properties(tarzan_metal_host_smoke tarzan_gamepad_smoke PROPERTIES LABELS gui)
endif()

# Host unit tests that need neither a window nor a device.
add_executable(tarzanKeyboardControlsTest
    "${TARZAN_ROOT}/tests/keyboard-controls-test.mm"
    "${CMAKE_CURRENT_SOURCE_DIR}/keyboard-controls.mm")
target_include_directories(tarzanKeyboardControlsTest PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_features(tarzanKeyboardControlsTest PRIVATE cxx_std_20)
target_compile_options(tarzanKeyboardControlsTest PRIVATE -fobjc-arc -UNDEBUG)
target_link_options(tarzanKeyboardControlsTest PRIVATE -stdlib=libc++)
target_link_libraries(tarzanKeyboardControlsTest PRIVATE "-framework Foundation" "-framework AppKit")
add_test(NAME tarzan_keyboard_controls COMMAND tarzanKeyboardControlsTest)

add_executable(tarzanMetalGsAdapterTest
    "${TARZAN_ROOT}/tests/metal-gs-adapter-test.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/metal-gs-adapter.cpp")
target_include_directories(tarzanMetalGsAdapterTest PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${TARZAN_ROOT}/deps/fukami/tools/pcsx2-gs-bridge")
target_compile_features(tarzanMetalGsAdapterTest PRIVATE cxx_std_20)
target_compile_options(tarzanMetalGsAdapterTest PRIVATE -UNDEBUG)
target_link_options(tarzanMetalGsAdapterTest PRIVATE -stdlib=libc++)
set_source_files_properties("${TARZAN_ROOT}/tests/metal-gs-adapter-test.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/metal-gs-adapter.cpp" PROPERTIES SKIP_PRECOMPILE_HEADERS ON)
add_test(NAME tarzan_metal_gs_adapter COMMAND tarzanMetalGsAdapterTest)

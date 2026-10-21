# Native Cocoa/Metal host for the recompiled game plus its tests. The host
# library and smoke runners exist only once the generated game sources do.
set(RX_METAL_BRIDGE "${RX_ROOT}/build/metal-bridge/pcsx2/pcsx2-gs-bridge/librrv-pcsx2-gs-bridge.dylib"
    CACHE FILEPATH "Pinned PCSX2 GS bridge dylib")
if(NOT EXISTS "${RX_METAL_BRIDGE}")
    message(FATAL_ERROR "Missing pinned PCSX2 Metal bridge: ${RX_METAL_BRIDGE}")
endif()
set(RX_SPU2_BACKEND "${RX_ROOT}/build/spu2-backend/librrv_pcsx2_spu2.a"
    CACHE FILEPATH "Built pinned PCSX2 SPU2 static library")
if(NOT EXISTS "${RX_SPU2_BACKEND}")
    message(FATAL_ERROR "Missing PCSX2 SPU2 backend; run tools/build-deps.sh: ${RX_SPU2_BACKEND}")
endif()
set(RX_SOUNDTOUCH_LIBRARY "${RX_ROOT}/build/metal-bridge/pcsx2/3rdparty/soundtouch/libpcsx2-soundtouch.a"
    CACHE FILEPATH "Built pinned PCSX2 SoundTouch static library")
if(NOT EXISTS "${RX_SOUNDTOUCH_LIBRARY}")
    message(FATAL_ERROR "Missing pinned PCSX2 SoundTouch library: ${RX_SOUNDTOUCH_LIBRARY}")
endif()
set(RX_LIBCXX_INCLUDE "${RX_LLVM}/include/c++/v1")

if(RX_GAME_READY)
    target_include_directories(ps2EntryRunner PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    add_library(metalHost STATIC
        "${CMAKE_CURRENT_SOURCE_DIR}/metal-host.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/keyboard-controls.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/settings-window.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/settings.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/diagnostic-input.cpp"
        "${CMAKE_CURRENT_SOURCE_DIR}/spu2-host.cpp"
        "${CMAKE_CURRENT_SOURCE_DIR}/metal-gs-adapter.cpp")
    target_include_directories(metalHost PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}"
        "${RX_ROOT}/bridge/gs"
        "${RX_ROOT}/bridge/spu2"
        "${RX_ROOT}/deps/pcsx2/3rdparty/soundtouch/soundtouch"
        "${RX_PS2RECOMP_SOURCE_DIR}/ps2xRuntime/include"
        "${RX_PS2RECOMP_SOURCE_DIR}/ps2xIOP/include")
    target_compile_features(metalHost PRIVATE cxx_std_20)
    target_compile_options(metalHost PRIVATE
        -stdlib=libc++ -nostdinc++ -isystem "${RX_LIBCXX_INCLUDE}")
    set_target_properties(metalHost PROPERTIES UNITY_BUILD OFF)
    set_property(SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/metal-host.mm" APPEND PROPERTY COMPILE_DEFINITIONS
        "PS2X_APP_NAME=\"${RX_APP_NAME}\"")
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/metal-host.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/keyboard-controls.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/settings-window.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/settings.mm" PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc" SKIP_UNITY_BUILD_INCLUSION ON SKIP_PRECOMPILE_HEADERS ON)
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/metal-gs-adapter.cpp" PROPERTIES
        SKIP_UNITY_BUILD_INCLUSION ON SKIP_PRECOMPILE_HEADERS ON)
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/spu2-host.cpp" PROPERTIES
        SKIP_UNITY_BUILD_INCLUSION ON SKIP_PRECOMPILE_HEADERS ON)
    target_link_options(metalHost PRIVATE -stdlib=libc++)
    target_link_libraries(metalHost PUBLIC
        "${RX_METAL_BRIDGE}"
        "-framework AppKit" "-framework QuartzCore" "-framework Metal" "-framework CoreGraphics")
    find_package(Threads REQUIRED)
    target_link_libraries(metalHost PRIVATE "${RX_SPU2_BACKEND}" Threads::Threads ps2_iop)
    target_link_libraries(metalHost PRIVATE "${RX_SOUNDTOUCH_LIBRARY}")
endif()

add_executable(audioStretcherTest
    "${RX_ROOT}/bridge/spu2/rrv_audio_stretcher.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/audio-stretcher-test.cpp")
target_include_directories(audioStretcherTest PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${RX_ROOT}/bridge/spu2"
    "${RX_ROOT}/deps/pcsx2/3rdparty/soundtouch/soundtouch")
target_compile_definitions(audioStretcherTest PRIVATE SOUNDTOUCH_FLOAT_SAMPLES)
target_compile_features(audioStretcherTest PRIVATE cxx_std_20)
target_link_options(audioStretcherTest PRIVATE -stdlib=libc++)
target_link_libraries(audioStretcherTest PRIVATE "${RX_SOUNDTOUCH_LIBRARY}")
add_test(NAME audio_stretcher COMMAND audioStretcherTest)

if(RX_GAME_READY)
    target_link_libraries(ps2EntryRunner PRIVATE metalHost)
    add_executable(metalHostSmoke "${CMAKE_CURRENT_SOURCE_DIR}/metal-host-smoke.cpp")
    target_include_directories(metalHostSmoke PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_compile_features(metalHostSmoke PRIVATE cxx_std_20)
    target_link_options(metalHostSmoke PRIVATE -stdlib=libc++)
    target_link_libraries(metalHostSmoke PRIVATE metalHost)

    # SDL is used only for its Gamepad/Joystick subsystem; no SDL video or
    # raylib window backend is enabled.
    set(RX_SDL3_PREFIX "${RX_ROOT}/tools/metal-deps/prefix" CACHE PATH
        "Local SDL3 static dependency prefix")
    if(NOT EXISTS "${RX_SDL3_PREFIX}/lib/cmake/SDL3/SDL3Config.cmake")
        message(FATAL_ERROR "Missing pinned SDL3 package under ${RX_SDL3_PREFIX}")
    endif()
    find_package(SDL3 CONFIG REQUIRED COMPONENTS SDL3-static
        PATHS "${RX_SDL3_PREFIX}/lib/cmake/SDL3" NO_DEFAULT_PATH)
    # SDL entry points and their Carbon dependency come from the GS bridge.
    get_target_property(RX_SDL3_LINK_LIBRARIES SDL3::SDL3-static INTERFACE_LINK_LIBRARIES)
    list(FILTER RX_SDL3_LINK_LIBRARIES EXCLUDE REGEX "FRAMEWORK,Carbon>")
    set_property(TARGET SDL3::SDL3-static PROPERTY
        INTERFACE_LINK_LIBRARIES "${RX_SDL3_LINK_LIBRARIES}")
    set(RX_LLVM_AVAILABILITY_SOURCE
        "${RX_ROOT}/tools/metal-deps/compat/llvm19-os_version_check.c")
    if(NOT EXISTS "${RX_LLVM_AVAILABILITY_SOURCE}")
        message(FATAL_ERROR "Missing local LLVM availability-check runtime source")
    endif()
    add_library(compilerAvailability STATIC "${RX_LLVM_AVAILABILITY_SOURCE}")
    set_target_properties(compilerAvailability PROPERTIES POSITION_INDEPENDENT_CODE ON)
    target_sources(metalHost PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/gamepad-input.mm")
    target_include_directories(metalHost PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_link_libraries(metalHost PRIVATE SDL3::SDL3-static compilerAvailability)
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/gamepad-input.mm"
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc" SKIP_UNITY_BUILD_INCLUSION ON
        SKIP_PRECOMPILE_HEADERS ON)

    # Standalone input smoke; it creates no SDL window or renderer.
    add_executable(gamepadSmoke
        "${CMAKE_CURRENT_SOURCE_DIR}/gamepad-input.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/gamepad-smoke.mm")
    target_include_directories(gamepadSmoke PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_compile_features(gamepadSmoke PRIVATE cxx_std_20)
    target_compile_options(gamepadSmoke PRIVATE -stdlib=libc++ -nostdinc++
        -isystem "${RX_LIBCXX_INCLUDE}")
    target_link_options(gamepadSmoke PRIVATE -stdlib=libc++)
    # Match the runner: its SDL entry points resolve from the GS bridge.
    target_link_libraries(gamepadSmoke PRIVATE "${RX_METAL_BRIDGE}" SDL3::SDL3-static
        compilerAvailability "-framework AppKit")
    set_source_files_properties(
        "${CMAKE_CURRENT_SOURCE_DIR}/gamepad-input.mm"
        "${CMAKE_CURRENT_SOURCE_DIR}/gamepad-smoke.mm"
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc" SKIP_UNITY_BUILD_INCLUSION ON
        SKIP_PRECOMPILE_HEADERS ON)

    set_property(TARGET ps2EntryRunner APPEND PROPERTY INSTALL_RPATH
        "@loader_path/../Frameworks" "@loader_path/../Resources/pcsx2-gs-bridge")

    add_test(NAME metal_host_smoke COMMAND metalHostSmoke)
    add_test(NAME gamepad_smoke COMMAND gamepadSmoke)
    set_tests_properties(metal_host_smoke gamepad_smoke PROPERTIES LABELS gui)
endif()

# Host unit tests that need neither a window nor a device.
add_executable(keyboardControlsTest
    "${RX_ROOT}/tests/keyboard-controls-test.mm"
    "${CMAKE_CURRENT_SOURCE_DIR}/keyboard-controls.mm")
target_include_directories(keyboardControlsTest PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_features(keyboardControlsTest PRIVATE cxx_std_20)
target_compile_options(keyboardControlsTest PRIVATE -fobjc-arc -UNDEBUG)
target_link_options(keyboardControlsTest PRIVATE -stdlib=libc++)
target_link_libraries(keyboardControlsTest PRIVATE "-framework Foundation" "-framework AppKit")
add_test(NAME keyboard_controls COMMAND keyboardControlsTest)

add_executable(settingsTest
    "${RX_ROOT}/tests/settings-test.mm"
    "${CMAKE_CURRENT_SOURCE_DIR}/settings.mm")
target_include_directories(settingsTest PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_features(settingsTest PRIVATE cxx_std_20)
target_compile_options(settingsTest PRIVATE -fobjc-arc -UNDEBUG)
target_link_options(settingsTest PRIVATE -stdlib=libc++)
target_link_libraries(settingsTest PRIVATE "-framework Foundation")
add_test(NAME settings COMMAND settingsTest)

add_executable(padRumbleTest "${RX_ROOT}/tests/pad-rumble-test.cpp")
target_include_directories(padRumbleTest PRIVATE
    "${RX_PS2RECOMP_SOURCE_DIR}/ps2xRuntime/src/lib/Kernel/Stubs")
target_compile_features(padRumbleTest PRIVATE cxx_std_20)
target_compile_options(padRumbleTest PRIVATE -UNDEBUG)
add_test(NAME pad_rumble COMMAND padRumbleTest)

add_executable(metalGsAdapterTest
    "${RX_ROOT}/tests/metal-gs-adapter-test.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/metal-gs-adapter.cpp")
target_include_directories(metalGsAdapterTest PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${RX_ROOT}/bridge/gs"
    "${RX_PS2RECOMP_SOURCE_DIR}/ps2xRuntime/include")
target_compile_features(metalGsAdapterTest PRIVATE cxx_std_20)
target_compile_options(metalGsAdapterTest PRIVATE -UNDEBUG)
target_link_options(metalGsAdapterTest PRIVATE -stdlib=libc++)
set_source_files_properties("${RX_ROOT}/tests/metal-gs-adapter-test.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/metal-gs-adapter.cpp" PROPERTIES SKIP_PRECOMPILE_HEADERS ON)
add_test(NAME metal_gs_adapter COMMAND metalGsAdapterTest)

add_executable(bootMatchTest
    "${RX_ROOT}/tests/boot-match-test.cpp")
target_include_directories(bootMatchTest PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_features(bootMatchTest PRIVATE cxx_std_20)
target_compile_options(bootMatchTest PRIVATE -UNDEBUG)
target_link_options(bootMatchTest PRIVATE -stdlib=libc++)
set_source_files_properties("${RX_ROOT}/tests/boot-match-test.cpp" PROPERTIES SKIP_PRECOMPILE_HEADERS ON)
add_test(NAME boot_match COMMAND bootMatchTest)

add_executable(pollIdleTest
    "${RX_ROOT}/tests/poll-idle-test.cpp")
target_include_directories(pollIdleTest PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_features(pollIdleTest PRIVATE cxx_std_20)
target_compile_options(pollIdleTest PRIVATE -UNDEBUG)
target_link_options(pollIdleTest PRIVATE -stdlib=libc++)
set_source_files_properties("${RX_ROOT}/tests/poll-idle-test.cpp" PROPERTIES SKIP_PRECOMPILE_HEADERS ON)
add_test(NAME poll_idle COMMAND pollIdleTest)

add_executable(videoModeTest "${RX_ROOT}/tests/video-mode-test.cpp")
target_include_directories(videoModeTest PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_features(videoModeTest PRIVATE cxx_std_20)
target_compile_options(videoModeTest PRIVATE -UNDEBUG)
target_link_options(videoModeTest PRIVATE -stdlib=libc++)
set_source_files_properties("${RX_ROOT}/tests/video-mode-test.cpp" PROPERTIES SKIP_PRECOMPILE_HEADERS ON)
add_test(NAME video_mode COMMAND videoModeTest)

# Native Cocoa/Metal host for the recompiled game.
# Embedded (iOS/tvOS) builds keep their dependencies per platform:
# build/embedded/<RX_APPLE_PLATFORM>/..., SDL3 under tools/embedded/<platform>/prefix.
if(RX_EMBEDDED)
    if(NOT RX_APPLE_PLATFORM)
        message(FATAL_ERROR "Set RX_APPLE_PLATFORM (see cmake/toolchain-llvm19-embedded.cmake)")
    endif()
    set(RX_DEPS_BUILD "${RX_ROOT}/build/embedded/${RX_APPLE_PLATFORM}")
    set(RX_BRIDGE_DEFAULT "${RX_DEPS_BUILD}/metal-bridge/pcsx2-gs-bridge/librrv-pcsx2-gs-bridge.dylib")
    set(RX_SPU2_DEFAULT "${RX_DEPS_BUILD}/spu2-backend/librrv_pcsx2_spu2.a")
    set(RX_SDL3_DEFAULT "${RX_ROOT}/tools/embedded/${RX_APPLE_PLATFORM}/prefix")
else()
    set(RX_BRIDGE_DEFAULT "${RX_ROOT}/build/metal-bridge/pcsx2/pcsx2-gs-bridge/librrv-pcsx2-gs-bridge.dylib")
    set(RX_SPU2_DEFAULT "${RX_ROOT}/build/spu2-backend/librrv_pcsx2_spu2.a")
    set(RX_SDL3_DEFAULT "${RX_ROOT}/tools/metal-deps/prefix")
endif()
set(RX_METAL_BRIDGE "${RX_BRIDGE_DEFAULT}"
    CACHE FILEPATH "Pinned PCSX2 GS bridge dylib")
if(NOT EXISTS "${RX_METAL_BRIDGE}")
    message(FATAL_ERROR "Missing pinned PCSX2 Metal bridge: ${RX_METAL_BRIDGE}")
endif()
set(RX_SPU2_BACKEND "${RX_SPU2_DEFAULT}"
    CACHE FILEPATH "Built pinned PCSX2 SPU2 static library")
if(NOT EXISTS "${RX_SPU2_BACKEND}")
    message(FATAL_ERROR "Missing PCSX2 SPU2 backend; run tools/build-deps.sh: ${RX_SPU2_BACKEND}")
endif()
if(RX_EMBEDDED)
    set(RX_LIBCXX_INCLUDE "${RX_ROOT}/tools/embedded/${RX_APPLE_PLATFORM}/libcxx/include/c++/v1")
else()
    set(RX_LIBCXX_INCLUDE "${RX_LLVM}/include/c++/v1")
endif()

if(RX_GAME_READY)
    target_include_directories(ps2EntryRunner PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    if(RX_EMBEDDED)
        set(RX_HOST_PLATFORM_SOURCES
            "${CMAKE_CURRENT_SOURCE_DIR}/ios-host.mm"
            "${CMAKE_CURRENT_SOURCE_DIR}/settings.mm")
        set(RX_HOST_ARC_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/ios-host.mm"
            "${CMAKE_CURRENT_SOURCE_DIR}/settings.mm")
        set(RX_HOST_MAIN_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/ios-host.mm")
    else()
        set(RX_HOST_PLATFORM_SOURCES
            "${CMAKE_CURRENT_SOURCE_DIR}/metal-host.mm"
            "${CMAKE_CURRENT_SOURCE_DIR}/keyboard-controls.mm"
            "${CMAKE_CURRENT_SOURCE_DIR}/settings-window.mm"
            "${CMAKE_CURRENT_SOURCE_DIR}/settings.mm")
        set(RX_HOST_ARC_SOURCES ${RX_HOST_PLATFORM_SOURCES})
        set(RX_HOST_MAIN_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/metal-host.mm")
    endif()
    add_library(metalHost STATIC
        ${RX_HOST_PLATFORM_SOURCES}
        "${CMAKE_CURRENT_SOURCE_DIR}/spu2-host.cpp"
        "${CMAKE_CURRENT_SOURCE_DIR}/metal-gs-adapter.cpp")
    target_include_directories(metalHost PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}"
        "${RX_ROOT}/bridge/gs"
        "${RX_ROOT}/bridge/spu2"
        "${RX_PS2RECOMP_SOURCE_DIR}/ps2xRuntime/include"
        "${RX_PS2RECOMP_SOURCE_DIR}/ps2xIOP/include")
    target_compile_features(metalHost PRIVATE cxx_std_20)
    target_compile_options(metalHost PRIVATE
        -stdlib=libc++ -nostdinc++ -isystem "${RX_LIBCXX_INCLUDE}")
    set_target_properties(metalHost PROPERTIES UNITY_BUILD OFF)
    set_property(SOURCE "${RX_HOST_MAIN_SOURCE}" APPEND PROPERTY COMPILE_DEFINITIONS
        "PS2X_APP_NAME=\"${RX_APP_NAME}\"")
    set_source_files_properties(${RX_HOST_ARC_SOURCES} PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc" SKIP_UNITY_BUILD_INCLUSION ON SKIP_PRECOMPILE_HEADERS ON)
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/metal-gs-adapter.cpp" PROPERTIES
        SKIP_UNITY_BUILD_INCLUSION ON SKIP_PRECOMPILE_HEADERS ON)
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/spu2-host.cpp" PROPERTIES
        SKIP_UNITY_BUILD_INCLUSION ON SKIP_PRECOMPILE_HEADERS ON)
    target_link_options(metalHost PRIVATE -stdlib=libc++)
    if(RX_EMBEDDED)
        target_link_libraries(metalHost PUBLIC
            "${RX_METAL_BRIDGE}"
            "-framework UIKit" "-framework Foundation" "-framework QuartzCore" "-framework Metal"
            "-framework CoreGraphics" "-framework AVFoundation" "-framework GameController"
            "-framework CoreHaptics")
    else()
        target_link_libraries(metalHost PUBLIC
            "${RX_METAL_BRIDGE}"
            "-framework AppKit" "-framework QuartzCore" "-framework Metal" "-framework CoreGraphics")
    endif()
    find_package(Threads REQUIRED)
    target_link_libraries(metalHost PRIVATE "${RX_SPU2_BACKEND}" Threads::Threads ps2_iop)
endif()

if(RX_GAME_READY)
    target_link_libraries(ps2EntryRunner PRIVATE metalHost)
    # SDL is used only for its Gamepad/Joystick subsystem; no SDL video or
    # raylib window backend is enabled.
    set(RX_SDL3_PREFIX "${RX_SDL3_DEFAULT}" CACHE PATH
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

    if(RX_EMBEDDED)
        # Flat bundle: <App>.app/Frameworks holds the bridge dylib.
        target_link_options(ps2EntryRunner PRIVATE "-Wl,-rpath,@executable_path/Frameworks")
    else()
        set_property(TARGET ps2EntryRunner APPEND PROPERTY INSTALL_RPATH
            "@loader_path/../Frameworks" "@loader_path/../Resources/pcsx2-gs-bridge")
    endif()
endif()

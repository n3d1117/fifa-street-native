# Optional true-Metal GS host. Included by integration/CMakeLists.txt only for
# TARZAN_USE_METAL builds; does not affect the default raylib diagnostic build.
get_filename_component(TARZAN_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
if(DEFINED ENV{TARZAN_NATIVE_TOOLS_ROOT} AND NOT "$ENV{TARZAN_NATIVE_TOOLS_ROOT}" STREQUAL "")
    set(_tarzan_native_tools_default "$ENV{TARZAN_NATIVE_TOOLS_ROOT}")
else()
    set(_tarzan_native_tools_default "${TARZAN_ROOT}")
endif()
set(TARZAN_NATIVE_TOOLS_ROOT "${_tarzan_native_tools_default}" CACHE PATH
    "Root of the local compiler and build tools")

option(TARZAN_USE_METAL "Use Cocoa + the native PCSX2 Metal GS bridge" OFF)
if(TARZAN_USE_METAL)
    if(NOT APPLE)
        message(FATAL_ERROR "TARZAN_USE_METAL requires macOS")
    endif()
    if(NOT CMAKE_OBJCXX_COMPILER)
        message(FATAL_ERROR "Configure with LLVM 19 as CMAKE_OBJCXX_COMPILER")
    endif()
    set(TARZAN_METAL_BRIDGE "${TARZAN_ROOT}/build/metal-bridge/pcsx2/pcsx2-gs-bridge/librrv-pcsx2-gs-bridge.dylib"
        CACHE FILEPATH "Pinned PCSX2 GS bridge dylib")
    if(NOT EXISTS "${TARZAN_METAL_BRIDGE}")
        message(FATAL_ERROR "Missing pinned PCSX2 Metal bridge: ${TARZAN_METAL_BRIDGE}")
    endif()
    set(TARZAN_SPU2_BACKEND "${TARZAN_ROOT}/build/spu2-backend/librrv_pcsx2_spu2.a"
        CACHE FILEPATH "Built pinned PCSX2 SPU2 static library")
    if(NOT EXISTS "${TARZAN_SPU2_BACKEND}")
        message(FATAL_ERROR "Missing PCSX2 SPU2 backend; run scripts/build-spu2-backend.sh: ${TARZAN_SPU2_BACKEND}")
    endif()
    set(TARZAN_SOUNDTOUCH_LIBRARY "${TARZAN_ROOT}/build/metal-bridge/pcsx2/3rdparty/soundtouch/libpcsx2-soundtouch.a"
        CACHE FILEPATH "Built pinned PCSX2 SoundTouch static library")
    if(NOT EXISTS "${TARZAN_SOUNDTOUCH_LIBRARY}")
        message(FATAL_ERROR "Missing pinned PCSX2 SoundTouch library: ${TARZAN_SOUNDTOUCH_LIBRARY}")
    endif()
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    execute_process(COMMAND "${Python3_EXECUTABLE}" "${TARZAN_ROOT}/scripts/patch-generated-experiments.py"
        --generated-dir "${TARZAN_GENERATED_DIR}" --elf "${TARZAN_ROOT}/extracted/disc/SLES_503.50"
        RESULT_VARIABLE _tarzan_experiment_patch_result)
    if(NOT _tarzan_experiment_patch_result EQUAL 0)
        message(FATAL_ERROR "Could not install guarded experimental game hooks")
    endif()
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${TARZAN_ROOT}/scripts/patch-generated-experiments.py")
    target_include_directories(ps2EntryRunner PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
    target_compile_definitions(ps2_runtime PRIVATE TARZAN_USE_METAL=1)
    add_library(tarzanMetalHost STATIC
        "${CMAKE_CURRENT_LIST_DIR}/metal-host.mm"
        "${CMAKE_CURRENT_LIST_DIR}/keyboard-controls.mm"
        "${CMAKE_CURRENT_LIST_DIR}/experimental-settings.mm"
        "${CMAKE_CURRENT_LIST_DIR}/experimental-clock.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/diagnostic-input.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/spu2-host.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/metal-gs-adapter.cpp")
    target_include_directories(tarzanMetalHost PRIVATE
        "${CMAKE_CURRENT_LIST_DIR}"
        "${TARZAN_ROOT}/source/fukami/tools/pcsx2-gs-bridge"
        "${TARZAN_ROOT}/source/fukami/tools/pcsx2-spu2"
        "${TARZAN_ROOT}/source/deps/pcsx2-2.8.2/3rdparty/soundtouch/soundtouch"
        "${TARZAN_PS2RECOMP_SOURCE_DIR}/ps2xIOP/include")
    target_compile_features(tarzanMetalHost PRIVATE cxx_std_20)
    target_compile_options(tarzanMetalHost PRIVATE
        -stdlib=libc++ -nostdinc++
        -isystem "${TARZAN_NATIVE_TOOLS_ROOT}/tools/build/llvm19/include/c++/v1")
    set_target_properties(tarzanMetalHost PROPERTIES UNITY_BUILD OFF)
    set_source_files_properties("${CMAKE_CURRENT_LIST_DIR}/metal-host.mm"
        "${CMAKE_CURRENT_LIST_DIR}/keyboard-controls.mm"
        "${CMAKE_CURRENT_LIST_DIR}/experimental-settings.mm" PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc" SKIP_UNITY_BUILD_INCLUSION ON SKIP_PRECOMPILE_HEADERS ON)
    set_source_files_properties("${CMAKE_CURRENT_LIST_DIR}/metal-gs-adapter.cpp" PROPERTIES
        SKIP_UNITY_BUILD_INCLUSION ON SKIP_PRECOMPILE_HEADERS ON)
    set_source_files_properties("${CMAKE_CURRENT_LIST_DIR}/spu2-host.cpp" PROPERTIES
        SKIP_UNITY_BUILD_INCLUSION ON SKIP_PRECOMPILE_HEADERS ON)
    target_link_options(tarzanMetalHost PRIVATE -stdlib=libc++)
    target_link_libraries(tarzanMetalHost PUBLIC
        "${TARZAN_METAL_BRIDGE}"
        "-framework AppKit" "-framework QuartzCore" "-framework Metal" "-framework CoreGraphics")
    find_package(Threads REQUIRED)
    target_link_libraries(tarzanMetalHost PRIVATE "${TARZAN_SPU2_BACKEND}" Threads::Threads ps2_iop)
    target_link_libraries(tarzanMetalHost PRIVATE "${TARZAN_SOUNDTOUCH_LIBRARY}")
    enable_testing()
    add_executable(tarzanAudioStretcherTest
        "${TARZAN_ROOT}/source/fukami/tools/pcsx2-spu2/rrv_audio_stretcher.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/audio-stretcher-test.cpp")
    target_include_directories(tarzanAudioStretcherTest PRIVATE
        "${CMAKE_CURRENT_LIST_DIR}"
        "${TARZAN_ROOT}/source/fukami/tools/pcsx2-spu2"
        "${TARZAN_ROOT}/source/deps/pcsx2-2.8.2/3rdparty/soundtouch/soundtouch")
    target_compile_definitions(tarzanAudioStretcherTest PRIVATE SOUNDTOUCH_FLOAT_SAMPLES)
    target_compile_features(tarzanAudioStretcherTest PRIVATE cxx_std_20)
    target_link_options(tarzanAudioStretcherTest PRIVATE -stdlib=libc++)
    target_link_libraries(tarzanAudioStretcherTest PRIVATE "${TARZAN_SOUNDTOUCH_LIBRARY}")
    add_test(NAME tarzan_audio_stretcher COMMAND tarzanAudioStretcherTest)
    target_link_libraries(ps2EntryRunner PRIVATE tarzanMetalHost)
    add_executable(tarzanMetalHostSmoke "${CMAKE_CURRENT_LIST_DIR}/metal-host-smoke.cpp")
    target_include_directories(tarzanMetalHostSmoke PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
    target_compile_features(tarzanMetalHostSmoke PRIVATE cxx_std_20)
    target_link_options(tarzanMetalHostSmoke PRIVATE -stdlib=libc++)
    target_link_libraries(tarzanMetalHostSmoke PRIVATE tarzanMetalHost)
    include("${CMAKE_CURRENT_LIST_DIR}/gamepad.cmake")
    set_property(TARGET ps2EntryRunner APPEND PROPERTY BUILD_RPATH
        "${TARZAN_NATIVE_TOOLS_ROOT}/tools/build/llvm19/lib"
        "${TARZAN_ROOT}/build/metal-bridge/pcsx2/pcsx2-gs-bridge")
    set_property(TARGET ps2EntryRunner APPEND PROPERTY INSTALL_RPATH
        "@loader_path/../Frameworks" "@loader_path/../Resources/pcsx2-gs-bridge")
    set_property(TARGET tarzanMetalHostSmoke APPEND PROPERTY BUILD_RPATH
        "${TARZAN_NATIVE_TOOLS_ROOT}/tools/build/llvm19/lib"
        "${TARZAN_ROOT}/build/metal-bridge/pcsx2/pcsx2-gs-bridge")
endif()

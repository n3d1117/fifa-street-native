# Added to the deps/pcsx2 build by tools/build-deps.sh (RRV_PCSX2_GS_BRIDGE_SOURCE_DIR).
# gsrunner_host.cpp supplies the minimal Host implementation PCSX2's GS/device
# code requires; it derives from upstream pcsx2-gsrunner (GPL-3.0+).
add_library(rrv-pcsx2-gs-bridge SHARED
    bridge.cpp
    gsrunner_host.cpp
)
if(CMAKE_SYSTEM_NAME MATCHES "^(iOS|tvOS)$")
    target_sources(rrv-pcsx2-gs-bridge PRIVATE embedded_platform_stubs.cpp)
endif()

if(APPLE)
    target_sources(rrv-pcsx2-gs-bridge PRIVATE metal_surface_adapter.mm)
    # PCSX2's target PCH is generated as C++; Clang cannot consume it while
    # compiling Objective-C++. This small adapter is the only ObjC++ source.
    set_source_files_properties(metal_surface_adapter.mm PROPERTIES
        SKIP_PRECOMPILE_HEADERS ON)
endif()

target_include_directories(rrv-pcsx2-gs-bridge
    PUBLIC "${CMAKE_CURRENT_LIST_DIR}"
    PRIVATE
        "${CMAKE_BINARY_DIR}/common/include"
        "${CMAKE_SOURCE_DIR}"
        "${CMAKE_SOURCE_DIR}/pcsx2"
)

target_link_libraries(rrv-pcsx2-gs-bridge PRIVATE PCSX2_FLAGS PCSX2)
if(TARGET PCSX2_COMPILER_RT_VERSION_CHECK)
    target_sources(rrv-pcsx2-gs-bridge PRIVATE $<TARGET_OBJECTS:PCSX2_COMPILER_RT_VERSION_CHECK>)
endif()
target_compile_definitions(rrv-pcsx2-gs-bridge PRIVATE RRV_PCSX2_GS_BRIDGE_BUILD=1)
set_target_properties(rrv-pcsx2-gs-bridge PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN YES
)

# GSopen(SW) still initializes PCSX2's font/shader presentation plumbing.
# Stage the pinned redistributable PCSX2 resources beside the optional bridge;
# runtime discovery is relative to the loaded library, not the RRV executable.
add_custom_command(TARGET rrv-pcsx2-gs-bridge POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E copy_directory
            "${CMAKE_SOURCE_DIR}/bin/resources"
            "$<TARGET_FILE_DIR:rrv-pcsx2-gs-bridge>/resources"
    VERBATIM
)

if(APPLE)
    # PCSX2's Metal shaders are compiled at bridge start from this source tree
    # (GSMTLDeviceInfo.mm); stage them as one inlined source beside the bridge.
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    get_property(rrv_metal_shaders GLOBAL PROPERTY PCSX2_METAL_SHADERS)
    get_property(rrv_pcsx2_source_dir GLOBAL PROPERTY PCSX2_SOURCE_DIR)
    list(TRANSFORM rrv_metal_shaders PREPEND "${rrv_pcsx2_source_dir}/")
    set(rrv_metal_source "${CMAKE_CURRENT_BINARY_DIR}/pcsx2-metal-shaders.metal")
    add_custom_command(OUTPUT "${rrv_metal_source}"
        COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/inline_metal_sources.py"
                "${rrv_metal_source}" "${rrv_metal_source}.d" ${rrv_metal_shaders}
        DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/inline_metal_sources.py" ${rrv_metal_shaders}
        DEPFILE "${rrv_metal_source}.d"
        VERBATIM
    )
    add_custom_target(rrv-pcsx2-metal-source DEPENDS "${rrv_metal_source}")
    add_dependencies(rrv-pcsx2-gs-bridge rrv-pcsx2-metal-source)
    add_custom_command(TARGET rrv-pcsx2-gs-bridge POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -E rm -f
                "$<TARGET_FILE_DIR:rrv-pcsx2-gs-bridge>/resources/default.metallib"
                "$<TARGET_FILE_DIR:rrv-pcsx2-gs-bridge>/resources/Metal22.metallib"
                "$<TARGET_FILE_DIR:rrv-pcsx2-gs-bridge>/resources/Metal23.metallib"
        COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                "${rrv_metal_source}"
                "$<TARGET_FILE_DIR:rrv-pcsx2-gs-bridge>/resources/pcsx2-metal-shaders.metal"
        VERBATIM
    )
endif()

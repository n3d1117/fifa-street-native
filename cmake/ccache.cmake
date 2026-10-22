file(GLOB _rx_local_ccache CONFIGURE_DEPENDS "${RX_ROOT}/tools/build/ccache/ccache")
find_program(RX_CCACHE ccache HINTS "${RX_ROOT}/tools/build/ccache" NO_CACHE)
if(RX_CCACHE)
    if(NOT RX_APPLE_PLATFORM)
        set(_rx_cache_launcher "${CMAKE_COMMAND}" -E env
            "CCACHE_DIR=${CMAKE_BINARY_DIR}/ccache"
            "CCACHE_CONFIGPATH=${RX_ROOT}/cmake/ccache-settings.txt"
            "CCACHE_MAXSIZE=5G"
            "${RX_CCACHE}")
        foreach(lang IN ITEMS C CXX OBJC OBJCXX)
            set(CMAKE_${lang}_COMPILER_LAUNCHER ${_rx_cache_launcher})
        endforeach()
    endif()
    set(PS2X_ENABLE_SCCACHE OFF)
    if(NOT RX_APPLE_PLATFORM)
        # Clang embeds header mtimes in PCH files unless explicitly disabled.
        add_compile_options("$<$<COMPILE_LANG_AND_ID:CXX,Clang,AppleClang>:-Xclang;-fno-pch-timestamp>")
    endif()
    message(STATUS "Using ccache: ${RX_CCACHE} (CCACHE_DISABLE=1 bypasses cache)")
endif()

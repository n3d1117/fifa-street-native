file(GLOB _tarzan_local_ccache CONFIGURE_DEPENDS "${TARZAN_ROOT}/tools/build/ccache/ccache")
find_program(TARZAN_CCACHE ccache HINTS "${TARZAN_ROOT}/tools/build/ccache" NO_CACHE)
if(TARZAN_CCACHE)
    set(_tarzan_cache_launcher "${CMAKE_COMMAND}" -E env
        "CCACHE_DIR=${CMAKE_BINARY_DIR}/ccache"
        "CCACHE_MAXSIZE=5G"
        "CCACHE_SLOPPINESS=pch_defines,time_macros,include_file_mtime,include_file_ctime"
        "${TARZAN_CCACHE}")
    foreach(lang IN ITEMS C CXX OBJC OBJCXX)
        set(CMAKE_${lang}_COMPILER_LAUNCHER ${_tarzan_cache_launcher})
    endforeach()
    set(PS2X_ENABLE_SCCACHE OFF)
    # Clang embeds header mtimes in PCH files unless explicitly disabled.
    add_compile_options("$<$<COMPILE_LANG_AND_ID:CXX,Clang,AppleClang>:-Xclang;-fno-pch-timestamp>")
    message(STATUS "Using ccache: ${TARZAN_CCACHE} (CCACHE_DISABLE=1 bypasses cache)")
endif()

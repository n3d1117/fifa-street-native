# LLVM 19 + libc++ on arm64 macOS. Compilers come from RX_LLVM, which
# defaults to tools/build/llvm19 (set the RX_LLVM environment variable or
# cache entry to use another install, e.g. Homebrew llvm@19).
get_filename_component(RX_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
if(NOT RX_LLVM)
    if(DEFINED ENV{RX_LLVM} AND NOT "$ENV{RX_LLVM}" STREQUAL "")
        set(RX_LLVM "$ENV{RX_LLVM}")
    else()
        set(RX_LLVM "${RX_ROOT}/tools/build/llvm19")
    endif()
endif()
set(RX_LLVM "${RX_LLVM}" CACHE PATH "LLVM 19 install prefix")

set(CMAKE_SYSTEM_NAME Darwin)
set(CMAKE_SYSTEM_PROCESSOR arm64)
set(CMAKE_OSX_ARCHITECTURES arm64)
set(CMAKE_OSX_DEPLOYMENT_TARGET 11.0)
execute_process(COMMAND xcrun --show-sdk-path
    OUTPUT_VARIABLE _rx_sdk OUTPUT_STRIP_TRAILING_WHITESPACE)
set(CMAKE_OSX_SYSROOT "${_rx_sdk}")

set(CMAKE_C_COMPILER "${RX_LLVM}/bin/clang")
set(CMAKE_CXX_COMPILER "${RX_LLVM}/bin/clang++")
set(CMAKE_OBJC_COMPILER "${RX_LLVM}/bin/clang")
set(CMAKE_OBJCXX_COMPILER "${RX_LLVM}/bin/clang++")

set(_rx_libcxx "-nostdinc++ -isystem ${RX_LLVM}/include/c++/v1 -stdlib=libc++")
set(_rx_prefix_map "-ffile-prefix-map=${RX_ROOT}=.")
set(CMAKE_C_FLAGS_INIT "${_rx_prefix_map}")
set(CMAKE_OBJC_FLAGS_INIT "${_rx_prefix_map}")
set(CMAKE_CXX_FLAGS_INIT "${_rx_libcxx} ${_rx_prefix_map}")
set(CMAKE_OBJCXX_FLAGS_INIT "${_rx_libcxx} ${_rx_prefix_map}")

set(_rx_link "-L${RX_LLVM}/lib -stdlib=libc++")
set(CMAKE_EXE_LINKER_FLAGS_INIT "${_rx_link}")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${_rx_link}")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "${_rx_link}")

# Binaries find libc++ and the bridge through relative rpaths added by
# cmake/relative-rpath.cmake; CMake's own absolute build rpaths are disabled.
set(CMAKE_SKIP_BUILD_RPATH ON)

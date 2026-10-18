# LLVM 19 + libc++ on arm64 macOS. Compilers come from TARZAN_LLVM, which
# defaults to tools/build/llvm19 (set the TARZAN_LLVM environment variable or
# cache entry to use another install, e.g. Homebrew llvm@19).
get_filename_component(TARZAN_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
if(NOT TARZAN_LLVM)
    if(DEFINED ENV{TARZAN_LLVM} AND NOT "$ENV{TARZAN_LLVM}" STREQUAL "")
        set(TARZAN_LLVM "$ENV{TARZAN_LLVM}")
    else()
        set(TARZAN_LLVM "${TARZAN_ROOT}/tools/build/llvm19")
    endif()
endif()
set(TARZAN_LLVM "${TARZAN_LLVM}" CACHE PATH "LLVM 19 install prefix")

set(CMAKE_SYSTEM_NAME Darwin)
set(CMAKE_SYSTEM_PROCESSOR arm64)
set(CMAKE_OSX_ARCHITECTURES arm64)
set(CMAKE_OSX_DEPLOYMENT_TARGET 11.0)
execute_process(COMMAND xcrun --show-sdk-path
    OUTPUT_VARIABLE _tarzan_sdk OUTPUT_STRIP_TRAILING_WHITESPACE)
set(CMAKE_OSX_SYSROOT "${_tarzan_sdk}")

set(CMAKE_C_COMPILER "${TARZAN_LLVM}/bin/clang")
set(CMAKE_CXX_COMPILER "${TARZAN_LLVM}/bin/clang++")
set(CMAKE_OBJC_COMPILER "${TARZAN_LLVM}/bin/clang")
set(CMAKE_OBJCXX_COMPILER "${TARZAN_LLVM}/bin/clang++")

set(_tarzan_libcxx "-nostdinc++ -isystem ${TARZAN_LLVM}/include/c++/v1 -stdlib=libc++")
set(_tarzan_prefix_map "-ffile-prefix-map=${TARZAN_ROOT}=.")
set(CMAKE_C_FLAGS_INIT "${_tarzan_prefix_map}")
set(CMAKE_OBJC_FLAGS_INIT "${_tarzan_prefix_map}")
set(CMAKE_CXX_FLAGS_INIT "${_tarzan_libcxx} ${_tarzan_prefix_map}")
set(CMAKE_OBJCXX_FLAGS_INIT "${_tarzan_libcxx} ${_tarzan_prefix_map}")

set(_tarzan_link "-L${TARZAN_LLVM}/lib -stdlib=libc++")
set(CMAKE_EXE_LINKER_FLAGS_INIT "${_tarzan_link}")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${_tarzan_link}")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "${_tarzan_link}")

# Binaries find libc++ and the bridge through relative rpaths added by
# cmake/relative-rpath.cmake; CMake's own absolute build rpaths are disabled.
set(CMAKE_SKIP_BUILD_RPATH ON)

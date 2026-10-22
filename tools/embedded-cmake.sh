#!/usr/bin/env bash

# Configure an embedded CMake tree, invalidating only CMake's cached configure
# state when toolchain-derived inputs change. Call with cmake, toolchain,
# platform, LLVM prefix, then the complete CMake configure argument list.
embedded_cmake_configure() {
  local cmake_bin="$1" toolchain="$2" platform="$3" llvm="$4"
  shift 4
  local build_dir="" i stamp hash expected_system actual_system actual_platform actual_llvm
  local -a args=("$@")

  for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[i]}" == "-B" && $((i + 1)) -lt ${#args[@]} ]]; then
      build_dir="${args[i + 1]}"
      break
    fi
  done
  [[ -n "$build_dir" ]] || { echo "embedded CMake configure is missing -B <build dir>" >&2; return 2; }

  stamp="$build_dir/rx-configure.stamp"
  hash="$( { printf '%s\0' "$toolchain" "$platform" "$llvm" "${args[@]}"; cat "$toolchain"; } | shasum -a 256 | awk '{print $1}')"
  expected_system="iOS"
  [[ "$platform" == appletvos || "$platform" == appletvsimulator ]] && expected_system="tvOS"
  for ((i = 0; i < ${#args[@]}; i++)); do
    case "${args[i]}" in
      -DCMAKE_SYSTEM_NAME=*) expected_system="${args[i]#*=}" ;;
      -DCMAKE_SYSTEM_NAME:*) expected_system="${args[i]##*=}" ;;
      -DRX_BUILDING_LIBCXX=ON) expected_system="Darwin" ;;
    esac
  done
  actual_system="$(sed -n 's/^CMAKE_SYSTEM_NAME:[^=]*=//p' "$build_dir/CMakeCache.txt" 2>/dev/null)"
  actual_platform="$(sed -n 's/^RX_APPLE_PLATFORM:[^=]*=//p' "$build_dir/CMakeCache.txt" 2>/dev/null)"
  actual_llvm="$(sed -n 's/^RX_LLVM:[^=]*=//p' "$build_dir/CMakeCache.txt" 2>/dev/null)"
  if [[ -f "$build_dir/CMakeCache.txt" && -f "$stamp" && "$(<"$stamp")" == "$hash" &&
        ( -z "$actual_system" || "$actual_system" == "$expected_system" ) &&
        "$actual_platform" == "$platform" &&
        "$actual_llvm" == "$llvm" ]]; then
    echo "embedded CMake inputs unchanged; keeping configured tree: $build_dir" >&2
    return 0
  fi
  if [[ -f "$build_dir/CMakeCache.txt" ]]; then
    echo "embedded CMake inputs changed; clearing cached configure state: $build_dir" >&2
    rm -f "$build_dir/CMakeCache.txt"
    rm -rf "$build_dir/CMakeFiles"
  fi

  "$cmake_bin" "${args[@]}" || return $?
  printf '%s\n' "$hash" > "$stamp.tmp"
  mv "$stamp.tmp" "$stamp"
}

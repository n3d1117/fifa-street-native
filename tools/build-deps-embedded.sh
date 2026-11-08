#!/usr/bin/env bash
# Build the static libraries linked by the native GS/SPU2 bridge for an Apple
# embedded platform. All mutable sources and outputs stay in this worktree.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && /bin/pwd -P)"
export PWD="$ROOT"
PLATFORM="${1:-}"
case "$PLATFORM" in
  iphonesimulator|iphoneos|appletvos|appletvsimulator) ;;
  *) echo "Usage: $0 <iphonesimulator|iphoneos|appletvos|appletvsimulator>" >&2; exit 2 ;;
esac
export DEVELOPER_DIR="${DEVELOPER_DIR:-$(xcode-select -p)}"
LLVM="$ROOT/tools/build/llvm19"
CMAKE="$ROOT/tools/build/cmake-3.31.12/CMake.app/Contents/bin/cmake"
NINJA="$ROOT/tools/build/ninja-1.13.1/ninja"
NCPU="${RX_JOBS:-$(python3 "$ROOT/tools/jobs.py") }"
NCPU="${NCPU% }"
BASE="$ROOT/tools/embedded"
SRC="$BASE/src"
PREFIX="$BASE/$PLATFORM/prefix"
BUILD="$ROOT/build/embedded/$PLATFORM/deps"
LOG="$ROOT/build/embedded/logs"
source "$ROOT/tools/embedded-cmake.sh"
source "$ROOT/tools/ffmpeg-minimal-components.sh"
mkdir -p "$SRC" "$PREFIX" "$BUILD" "$LOG"
for tool in "$LLVM/bin/clang++" "$CMAKE" "$NINJA"; do
  [[ -x "$tool" ]] || { echo "Missing $tool" >&2; exit 2; }
done
export PATH="$LLVM/bin:$(dirname "$CMAKE"):$(dirname "$NINJA"):$PATH"

sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }
fetch() { # name URL SHA256 source-cache [fallback-cache]
  local name="$1" url="$2" hash="$3" source_cache="$4" fallback="${5:-}"
  local output="$SRC/$name"
  if [[ -f "$output" && "$(sha256_of "$output")" == "$hash" ]]; then return; fi
  if [[ -f "$source_cache/$name" && "$(sha256_of "$source_cache/$name")" == "$hash" ]]; then
    cp "$source_cache/$name" "$output"
    return
  fi
  if [[ -n "$fallback" && -f "$fallback/$name" && "$(sha256_of "$fallback/$name")" == "$hash" ]]; then
    cp "$fallback/$name" "$output"
    return
  fi
  curl -fL --retry 3 "$url" -o "$output.part"
  [[ "$(sha256_of "$output.part")" == "$hash" ]] || { echo "SHA-256 mismatch: $name" >&2; exit 1; }
  mv "$output.part" "$output"
}
extract() { # archive directory
  local archive="$1" directory="$2"
  if [[ ! -d "$SRC/$directory" ]]; then
    mkdir -p "$SRC/$directory"
    tar -xf "$SRC/$archive" -C "$SRC/$directory" --strip-components=1
  fi
}
cmake_install() { # package source [options...]
  local name="$1" source="$2"; shift 2
  embedded_cmake_configure "$CMAKE" "$ROOT/cmake/toolchain-llvm19-embedded.cmake" "$PLATFORM" "$LLVM" \
    -S "$source" -B "$BUILD/$name" -G Ninja \
    -DCMAKE_MAKE_PROGRAM="$NINJA" \
    -DCMAKE_TOOLCHAIN_FILE="$ROOT/cmake/toolchain-llvm19-embedded.cmake" \
    -DRX_APPLE_PLATFORM="$PLATFORM" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_PREFIX_PATH="$PREFIX" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DBUILD_SHARED_LIBS=OFF "$@"
  "$CMAKE" --build "$BUILD/$name" --parallel "$NCPU"
  "$CMAKE" --install "$BUILD/$name"
}

# FFmpeg 7.1.2, matching tools/build-deps.sh; reuse its verified archive.
FFMPEG_VER=7.1.2
FFMPEG_HASH=089bc60fb59d6aecc5d994ff530fd0dcb3ee39aa55867849a2bbc4e555f9c304
fetch "ffmpeg-$FFMPEG_VER.tar.xz" "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VER.tar.xz" "$FFMPEG_HASH" \
  "$ROOT/tools/ffmpeg"
extract "ffmpeg-$FFMPEG_VER.tar.xz" "ffmpeg-$FFMPEG_VER"
FFMPEG_SOURCE="$SRC/ffmpeg-$FFMPEG_VER"
FFMPEG_PREFIX="$PREFIX"
SDK_NAME="$PLATFORM"
case "$PLATFORM" in
  iphonesimulator) SDK_NAME=iphonesimulator14.5 ;;
  iphoneos) SDK_NAME=iphoneos14.5 ;;
  appletvos) SDK_NAME=appletvos14.5 ;;
  appletvsimulator) SDK_NAME=appletvsimulator14.5 ;;
esac
SDK="$(xcrun --sdk "$SDK_NAME" --show-sdk-path)"
TARGET="arm64-apple-ios14.0"
MIN_FLAG="-mios-version-min=14.0"
case "$PLATFORM" in
  iphonesimulator) TARGET="$TARGET-simulator"; MIN_FLAG="-mios-simulator-version-min=14.0" ;;
  appletvos) TARGET="arm64-apple-tvos14.0"; MIN_FLAG="-mtvos-version-min=14.0" ;;
  appletvsimulator) TARGET="arm64-apple-tvos14.0-simulator"; MIN_FLAG="-mtvos-simulator-version-min=14.0" ;;
esac
CPU_FLAG=""
if [[ "$PLATFORM" == iphoneos || "$PLATFORM" == appletvos ]]; then CPU_FLAG="-mcpu=apple-a12"; fi
mkdir -p "$FFMPEG_PREFIX/lib" "$FFMPEG_PREFIX/include"
(
  cd "$FFMPEG_SOURCE"
  # This source tree is reused for all four Apple targets. FFmpeg's Makefile
  # does not track CFLAGS changes, so a prior platform's objects would survive
  # a plain reconfigure and contaminate the next static archive.
  if [[ -f ffbuild/config.mak ]]; then make distclean; fi
  for library in avcodec avformat avutil swscale swresample; do
    rm -f "lib$library/lib$library.a" "$FFMPEG_PREFIX/lib/lib$library.a"
  done
  export CC="$LLVM/bin/clang"
  BUILD_CC="$CC"
  export AR="$(xcrun --find ar)" NM="$(xcrun --find nm)"
  export RANLIB="$(xcrun --find ranlib)" STRIP="$(xcrun --find strip)"
  export CFLAGS="--target=$TARGET $CPU_FLAG -isysroot $SDK $MIN_FLAG -ffile-prefix-map=$ROOT=."
  export ASFLAGS="$CFLAGS"
  export LDFLAGS="$CFLAGS"
  ./configure --prefix="$FFMPEG_PREFIX" --pkg-config-flags=--static \
    --arch=aarch64 --target-os=darwin --enable-cross-compile --cc="$CC" \
    --ar="$AR" --nm="$NM" --ranlib="$RANLIB" --strip="$STRIP" --sysroot="$SDK" \
    --disable-shared --enable-static --disable-programs --disable-doc --disable-debug \
    --disable-network --disable-autodetect --disable-x86asm "${FFMPEG_COMPONENT_FLAGS[@]}" \
    --disable-muxers --disable-filters --disable-devices --enable-pic \
    2>&1 | tee "$LOG/$PLATFORM-ffmpeg-configure.log"
  make -j"$NCPU" CC="$BUILD_CC" 2>&1 | tee "$LOG/$PLATFORM-ffmpeg-build.log"
  make install CC="$BUILD_CC" 2>&1 | tee "$LOG/$PLATFORM-ffmpeg-install.log"
)

# Same hashes and source revisions as build_metal_deps() in build-deps.sh.
fetch lz4-1.10.0.tar.gz https://github.com/lz4/lz4/releases/download/v1.10.0/lz4-1.10.0.tar.gz 537512904744b35e232912055ccf8ec66d768639ff3abe5788d90d792ec5f48b "$ROOT/tools/metal-deps/src"
fetch zstd-1.5.7.tar.gz https://github.com/facebook/zstd/releases/download/v1.5.7/zstd-1.5.7.tar.gz eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3 "$ROOT/tools/metal-deps/src"
fetch libpng-1.6.58.tar.xz https://downloads.sourceforge.net/project/libpng/libpng16/1.6.58/libpng-1.6.58.tar.xz 28eb403f51f0f7405249132cecfe82ea5c0ef97f1b32c5a65828814ae0d34775 "$ROOT/tools/metal-deps/src"
fetch libjpeg-turbo-3.2.0.tar.gz https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.2.0/libjpeg-turbo-3.2.0.tar.gz 6f30092cef9fb839779646608f4ee14ae3cbac989c47fa05e841b0841f09878e "$ROOT/tools/metal-deps/src"
fetch libwebp-1.6.0.tar.gz https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-1.6.0.tar.gz e4ab7009bf0629fd11982d4c2aa83964cf244cffba7347ecd39019a9e38c4564 "$ROOT/tools/metal-deps/src"
fetch SDL3-3.4.12.tar.gz https://libsdl.org/release/SDL3-3.4.12.tar.gz f07b958a9ac5020fb7a44cadb957f658b2149c3c8abb4f63145fac9303249db7 "$ROOT/tools/metal-deps/src"
fetch freetype-2.14.3.tar.xz https://sourceforge.net/projects/freetype/files/freetype2/2.14.3/freetype-2.14.3.tar.xz/download 36bc4f1cc413335368ee656c42afca65c5a3987e8768cc28cf11ba775e785a5f "$ROOT/tools/metal-deps/src"
fetch harfbuzz-14.2.0.tar.gz https://github.com/harfbuzz/harfbuzz/archive/14.2.0/harfbuzz-14.2.0.tar.gz c652d5d94971031654ab3989891a490a895d3e3f2b71171c62692b28e94b1b93 "$ROOT/tools/metal-deps/src"
fetch plutovg-1.3.2.tar.gz https://github.com/sammycage/plutovg/archive/v1.3.2/plutovg-1.3.2.tar.gz 7bd4e79ce18b1d47517e7e91fbb7cf19d4f01942804a519bc7c0bf32b6325dd5 "$ROOT/tools/metal-deps/src"
fetch plutosvg-0.0.7.tar.gz https://github.com/sammycage/plutosvg/archive/v0.0.7/plutosvg-0.0.7.tar.gz 78561b571ac224030cdc450ca2986b4de915c2ba7616004a6d71a379bffd15f3 "$ROOT/tools/metal-deps/src"
fetch rapidyaml-0.12.1-src.tgz https://github.com/biojppm/rapidyaml/releases/download/v0.12.1/rapidyaml-0.12.1-src.tgz e9efcdd17f86287748793cf21d106e461fcad8d103a3e5a23632afe93828660d "$ROOT/tools/metal-deps/src"

extract lz4-1.10.0.tar.gz lz4-1.10.0
cmake_install lz4 "$SRC/lz4-1.10.0/build/cmake" -DBUILD_SHARED_LIBS=OFF -DLZ4_BUILD_CLI=OFF -DLZ4_BUILD_LEGACY_LZ4C=OFF
extract zstd-1.5.7.tar.gz zstd-1.5.7
cmake_install zstd "$SRC/zstd-1.5.7/build/cmake" -DZSTD_BUILD_SHARED=OFF -DZSTD_BUILD_STATIC=ON -DZSTD_BUILD_PROGRAMS=OFF -DZSTD_BUILD_TESTS=OFF
extract libpng-1.6.58.tar.xz libpng-1.6.58
cmake_install png "$SRC/libpng-1.6.58" -DPNG_SHARED=OFF -DPNG_STATIC=ON -DPNG_TESTS=OFF -DPNG_TOOLS=OFF
extract libjpeg-turbo-3.2.0.tar.gz libjpeg-turbo-3.2.0
cmake_install jpeg "$SRC/libjpeg-turbo-3.2.0" -DENABLE_STATIC=ON -DENABLE_SHARED=OFF -DWITH_TURBOJPEG=OFF
extract libwebp-1.6.0.tar.gz libwebp-1.6.0
cmake_install webp "$SRC/libwebp-1.6.0" -DBUILD_SHARED_LIBS=OFF -DWEBP_BUILD_ANIM_UTILS=OFF -DWEBP_BUILD_CWEBP=OFF -DWEBP_BUILD_DWEBP=OFF -DWEBP_BUILD_GIF2WEBP=OFF -DWEBP_BUILD_IMG2WEBP=OFF -DWEBP_BUILD_VWEBP=OFF -DWEBP_BUILD_WEBPINFO=OFF -DWEBP_BUILD_WEBPMUX=OFF -DWEBP_BUILD_EXTRAS=OFF
extract SDL3-3.4.12.tar.gz SDL3-3.4.12
python3 - "$SRC/SDL3-3.4.12" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
for path in (root / 'src/main/ios/SDL_sysmain_callbacks.m', root / 'src/video/uikit/SDL_uikitviewcontroller.m'):
    source = path.read_text()
    line = 'self.displayLink.preferredFrameRateRange = CAFrameRateRangeMake((frame_rate * 2) / 3, frame_rate, frame_rate);' if path.name == 'SDL_sysmain_callbacks.m' else 'displayLink.preferredFrameRateRange = CAFrameRateRangeMake((frame_rate * 2) / 3, frame_rate, frame_rate);'
    if '__IPHONE_OS_VERSION_MAX_ALLOWED >= 150000' not in source:
        if line not in source:
            raise SystemExit(f'unexpected SDL 3.4.12 frame-rate source: {path}')
        indented = next(line_text[:len(line_text) - len(line_text.lstrip())] for line_text in source.splitlines() if line_text.strip() == line)
        source = source.replace(indented + line, '#if (defined(__IPHONE_OS_VERSION_MAX_ALLOWED) && __IPHONE_OS_VERSION_MAX_ALLOWED >= 150000) || (defined(__TV_OS_VERSION_MAX_ALLOWED) && __TV_OS_VERSION_MAX_ALLOWED >= 150000)\n' + indented + line + '\n#endif', 1)
    path.write_text(source)
path = root / 'src/video/uikit/SDL_uikitmodes.m'
source = path.read_text()
old = '''    if (@available(iOS 16.0, *)) {
        if (uiscreen.currentEDRHeadroom > 1.0f) {
            display.HDR.HDR_headroom = uiscreen.currentEDRHeadroom;
        } else {
            display.HDR.HDR_headroom = uiscreen.potentialEDRHeadroom;
        }
    }'''.replace('+', '')
new = '''#if (defined(__IPHONE_OS_VERSION_MAX_ALLOWED) && __IPHONE_OS_VERSION_MAX_ALLOWED >= 160000) || (defined(__TV_OS_VERSION_MAX_ALLOWED) && __TV_OS_VERSION_MAX_ALLOWED >= 160000)
''' + old + '''
#endif'''
if old in source:
    source = source.replace(old, new, 1)
elif '__IPHONE_OS_VERSION_MAX_ALLOWED >= 160000' not in source:
    raise SystemExit(f'unexpected SDL 3.4.12 EDR source: {path}')
path.write_text(source)
PY
cmake_install sdl "$SRC/SDL3-3.4.12" -DSDL_SHARED=OFF -DSDL_STATIC=ON -DSDL_TEST_LIBRARY=OFF -DSDL_VIDEO=ON -DSDL_JOYSTICK=ON -DSDL_HAPTIC=ON -DSDL_RENDER=OFF -DSDL_GPU=OFF -DSDL_METAL=OFF
extract freetype-2.14.3.tar.xz freetype-2.14.3
extract harfbuzz-14.2.0.tar.gz harfbuzz-14.2.0
cmake_install harfbuzz "$SRC/harfbuzz-14.2.0" -DHB_BUILD_UTILS=OFF -DHB_BUILD_SUBSET=OFF
cmake_install freetype "$SRC/freetype-2.14.3" -DFT_REQUIRE_ZLIB=ON -DFT_REQUIRE_PNG=ON -DPNG_PNG_INCLUDE_DIR="$PREFIX/include" -DPNG_LIBRARY_RELEASE="$PREFIX/lib/libpng16.a" -DFT_REQUIRE_HARFBUZZ=ON -DFT_DISABLE_BZIP2=ON -DFT_DISABLE_BROTLI=ON
extract plutovg-1.3.2.tar.gz plutovg-1.3.2
cmake_install plutovg "$SRC/plutovg-1.3.2" -DPLUTOVG_BUILD_EXAMPLES=OFF
extract plutosvg-0.0.7.tar.gz plutosvg-0.0.7
cmake_install plutosvg "$SRC/plutosvg-0.0.7" -Dplutovg_DIR="$PREFIX/lib/cmake/plutovg" -DFREETYPE_LIBRARY="$PREFIX/lib/libfreetype.a" -DFREETYPE_INCLUDE_DIR_freetype2="$PREFIX/include/freetype2" -DFREETYPE_INCLUDE_DIR_ft2build="$PREFIX/include/freetype2" -DPLUTOSVG_ENABLE_FREETYPE=ON -DPLUTOSVG_BUILD_EXAMPLES=OFF
extract rapidyaml-0.12.1-src.tgz rapidyaml-0.12.1
cmake_install rapidyaml "$SRC/rapidyaml-0.12.1" -DRYML_BUILD_TESTS=OFF -DRYML_BUILD_TOOLS=OFF

echo "== embedded static dependencies ready: $PREFIX"
printf '%s\n' "$FFMPEG_HASH" > "$BUILD/embedded-deps.stamp"

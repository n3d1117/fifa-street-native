#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$ROOT/tools/ffmpeg"
SOURCE="$TOOLS/src/ffmpeg-7.1.2"
PREFIX="$TOOLS/arm64"
NATIVE="${TARZAN_NATIVE_TOOLS_ROOT:-$(dirname "$ROOT")/tarzan-native}"
LLVM="$NATIVE/tools/build/llvm19"
CMAKE="$NATIVE/tools/build/cmake-3.31.12/CMake.app/Contents/bin/cmake"
NINJA="$NATIVE/tools/build/ninja-1.13.1/ninja"
SDK="$(xcrun --show-sdk-path)"
ARCHIVE="$TOOLS/ffmpeg-7.1.2.tar.xz"

mkdir -p "$TOOLS/src" "$PREFIX" "$ROOT/logs"
if [ ! -d "$SOURCE" ]; then
  if [ ! -f "$ARCHIVE" ]; then
    curl --fail --location --retry 3 \
      https://ffmpeg.org/releases/ffmpeg-7.1.2.tar.xz \
      --output "$ARCHIVE"
  fi
  tar -xJf "$ARCHIVE" -C "$TOOLS/src"
fi

cd "$SOURCE"
export CC="$LLVM/bin/clang"
export AR="$(xcrun --find ar)"
export NM="$(xcrun --find nm)"
export RANLIB="$(xcrun --find ranlib)"
export STRIP="$(xcrun --find strip)"
export CFLAGS="-arch arm64 -mmacosx-version-min=11.0 -isysroot $SDK"
export LDFLAGS="-arch arm64 -mmacosx-version-min=11.0 -isysroot $SDK"

./configure \
  --prefix="$PREFIX" \
  --pkg-config-flags="--static" \
  --arch=aarch64 \
  --target-os=darwin \
  --enable-cross-compile \
  --cc="$CC" \
  --ar="$AR" \
  --nm="$NM" \
  --ranlib="$RANLIB" \
  --strip="$STRIP" \
  --sysroot="$SDK" \
  --disable-shared \
  --enable-static \
  --disable-programs \
  --disable-doc \
  --disable-debug \
  --disable-network \
  --disable-autodetect \
  --disable-x86asm \
  --disable-encoders \
  --disable-muxers \
  --disable-filters \
  --disable-devices \
  --enable-pic \
  2>&1 | tee "$ROOT/logs/ffmpeg-configure.log"

make -j2 V=1 2>&1 | tee "$ROOT/logs/ffmpeg-build.log"
make install 2>&1 | tee "$ROOT/logs/ffmpeg-install.log"

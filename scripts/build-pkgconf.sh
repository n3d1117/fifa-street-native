#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$ROOT/tools/pkgconf"
SOURCE="$TOOLS/src/pkgconf-3.0.5"
PREFIX="$TOOLS"
ARCHIVE="$TOOLS/src/pkgconf-3.0.5.tar.xz"

mkdir -p "$TOOLS/src" "$TOOLS/bin" "$ROOT/logs"
if [ ! -d "$SOURCE" ]; then
  tar -xJf "$ARCHIVE" -C "$TOOLS/src"
fi

cd "$SOURCE"
export CC="$(xcrun --find clang)"
SDK="$(xcrun --show-sdk-path)"
export CFLAGS="-arch arm64 -mmacosx-version-min=11.0 -isysroot $SDK"
export LDFLAGS="$CFLAGS"

./configure --prefix="$PREFIX" --disable-shared --enable-static \
  2>&1 | tee "$ROOT/logs/pkgconf-configure.log"
make -j2 2>&1 | tee "$ROOT/logs/pkgconf-build.log"
make install 2>&1 | tee "$ROOT/logs/pkgconf-install.log"
ln -sf pkgconf "$PREFIX/bin/pkg-config"

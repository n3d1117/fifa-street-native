#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$ROOT/scripts/send-native-key.m"
BINARY="$ROOT/build/send-native-key"
if [ ! -x "$BINARY" ] || [ "$SOURCE" -nt "$BINARY" ]; then
    mkdir -p "$ROOT/build"
    /usr/bin/clang -x objective-c -fobjc-arc -arch arm64 -mmacosx-version-min=11.0 \
        "$SOURCE" -framework AppKit -framework ApplicationServices -o "$BINARY"
fi
exec "$BINARY" "$@"

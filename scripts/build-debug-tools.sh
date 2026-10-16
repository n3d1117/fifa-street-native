#!/bin/bash
# Small native helpers used by the repeatable GUI/gameplay probes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/build"
xcrun clang -fobjc-arc -framework Cocoa -framework CoreGraphics -framework ImageIO \
    "$ROOT/scripts/capture-window.m" -o "$ROOT/build/capture-window"
xcrun clang -fobjc-arc -framework Cocoa -framework Vision -framework ImageIO \
    "$ROOT/scripts/ocr-window.m" -o "$ROOT/build/ocr-window"
xcrun clang -fobjc-arc -framework Cocoa -framework ApplicationServices -framework Carbon \
    "$ROOT/scripts/send-native-key.m" -o "$ROOT/build/send-native-key"

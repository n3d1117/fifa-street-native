#!/bin/bash
# Generate the project icon from native drawing commands; no game artwork.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/build/AppIcon.iconset"
xcrun clang -fobjc-arc -framework AppKit "$ROOT/scripts/make-icon.m" -o "$ROOT/build/make-icon"
"$ROOT/build/make-icon" "$ROOT/build/AppIcon-1024.png"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$ROOT/build/AppIcon-1024.png" --out "$ROOT/build/AppIcon.iconset/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" "$ROOT/build/AppIcon-1024.png" --out "$ROOT/build/AppIcon.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ROOT/build/AppIcon.iconset" -o "$ROOT/build/AppIcon.icns"

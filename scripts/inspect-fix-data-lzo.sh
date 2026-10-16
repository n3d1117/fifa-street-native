#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
FFMPEG="$ROOT/tools/ffmpeg/arm64"
ASSET="${1:-$ROOT/extracted/disc/GAMEDATA/BINARY/BIG/FIX_DATA.000}"
PROBE="${TMPDIR:-/tmp}/tarzan-fix-data-lzo-probe-$$"
trap 'rm -f "$PROBE"' EXIT HUP INT TERM
cc -I"$FFMPEG/include" "$ROOT/reports/fix-data-lzo-probe.c" \
  "$FFMPEG/lib/libavutil.a" -o "$PROBE"
"$PROBE" "$ASSET"

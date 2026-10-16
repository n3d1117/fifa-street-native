#!/bin/bash
# Canonical game builds always use the native Metal target.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec "$SCRIPT_DIR/build-metal-game.sh" "$@"

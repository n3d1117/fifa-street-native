#!/bin/bash
set -euo pipefail
cd "${SRCROOT:?}"

case "${PLATFORM_NAME:?}" in
  iphonesimulator) ./rx ipa --platform ios-sim --app-only ;;
  iphoneos) ./rx ipa --platform ios --app-only ;;
  appletvsimulator) ./rx ipa --platform tvos-sim --app-only ;;
  appletvos) ./rx ipa --platform tvos --app-only ;;
  macosx) ./rx build && python3 tools/package/package.py ;;
  *) echo "Unsupported Xcode platform: ${PLATFORM_NAME}" >&2; exit 2 ;;
esac

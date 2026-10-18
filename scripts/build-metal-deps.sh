#!/usr/bin/env bash
# Build the minimal pinned PCSX2 2.8.2 macOS dependency set into a private arm64 prefix.
# Version URLs and SHA-256 values are from that pinned source's official macOS CI recipe.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
native_root="${TARZAN_NATIVE_TOOLS_ROOT:-$root}"
pcsx2="$root/source/deps/pcsx2-2.8.2-bridge"
base="$root/tools/metal-deps"
src="$base/src"
prefix="$base/prefix"
build="$base/build"
cmake="$native_root/tools/build/cmake-3.31.12/CMake.app/Contents/bin/cmake"
ninja="$native_root/tools/build/ninja-1.13.1/ninja"
llvm="$native_root/tools/build/llvm19/bin"
sdk="$(xcrun --show-sdk-path)"
nproc="$(sysctl -n hw.ncpu)"
mkdir -p "$src" "$prefix" "$build"
export PATH="$llvm:$(dirname "$cmake"):$(dirname "$ninja"):$PATH"
export CC="$llvm/clang" CXX="$llvm/clang++" OBJC="$llvm/clang" OBJCXX="$llvm/clang++"
export MACOSX_DEPLOYMENT_TARGET=11.0
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig"
export CFLAGS="-I$prefix/include" CXXFLAGS="-I$prefix/include" LDFLAGS="-L$prefix/lib"

fetch() {
    local name="$1" url="$2" sha="$3"
    local file="$src/$name"
    if [[ ! -f "$file" ]] || [[ "$(shasum -a 256 "$file" | awk '{print $1}')" != "$sha" ]]; then
        rm -f "$file.part"
        curl -fL --retry 2 "$url" -o "$file.part"
        [[ "$(shasum -a 256 "$file.part" | awk '{print $1}')" == "$sha" ]] || { echo "SHA-256 mismatch: $name" >&2; rm -f "$file.part"; exit 1; }
        mv "$file.part" "$file"
    fi
}
extract() { local archive="$1" directory="$2"; if [[ ! -d "$src/$directory" ]]; then mkdir -p "$src/$directory"; tar -xf "$src/$archive" -C "$src/$directory" --strip-components=1; fi; }
cmake_install() {
    local name="$1" source="$2"; shift 2
    "$cmake" -S "$source" -B "$build/$name" -G Ninja \
      -DCMAKE_MAKE_PROGRAM="$ninja" -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_PREFIX_PATH="$prefix" \
      -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 \
      -DCMAKE_OSX_SYSROOT="$sdk" -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" "$@"
    "$cmake" --build "$build/$name" --parallel "$nproc"
    "$cmake" --install "$build/$name"
}

# Release/checksum pins copied from .github/workflows/scripts/macos/build-dependencies.sh.
fetch lz4-1.10.0.tar.gz https://github.com/lz4/lz4/releases/download/v1.10.0/lz4-1.10.0.tar.gz 537512904744b35e232912055ccf8ec66d768639ff3abe5788d90d792ec5f48b
fetch zstd-1.5.7.tar.gz https://github.com/facebook/zstd/releases/download/v1.5.7/zstd-1.5.7.tar.gz eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3
fetch libpng-1.6.58.tar.xz https://downloads.sourceforge.net/project/libpng/libpng16/1.6.58/libpng-1.6.58.tar.xz 28eb403f51f0f7405249132cecfe82ea5c0ef97f1b32c5a65828814ae0d34775
fetch libjpeg-turbo-3.2.0.tar.gz https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.2.0/libjpeg-turbo-3.2.0.tar.gz 6f30092cef9fb839779646608f4ee14ae3cbac989c47fa05e841b0841f09878e
fetch libwebp-1.6.0.tar.gz https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-1.6.0.tar.gz e4ab7009bf0629fd11982d4c2aa83964cf244cffba7347ecd39019a9e38c4564
fetch SDL3-3.4.12.tar.gz https://libsdl.org/release/SDL3-3.4.12.tar.gz f07b958a9ac5020fb7a44cadb957f658b2149c3c8abb4f63145fac9303249db7
fetch freetype-2.14.3.tar.xz https://sourceforge.net/projects/freetype/files/freetype2/2.14.3/freetype-2.14.3.tar.xz/download 36bc4f1cc413335368ee656c42afca65c5a3987e8768cc28cf11ba775e785a5f
fetch harfbuzz-14.2.0.tar.gz https://github.com/harfbuzz/harfbuzz/archive/14.2.0/harfbuzz-14.2.0.tar.gz c652d5d94971031654ab3989891a490a895d3e3f2b71171c62692b28e94b1b93
fetch plutovg-1.3.2.tar.gz https://github.com/sammycage/plutovg/archive/v1.3.2/plutovg-1.3.2.tar.gz 7bd4e79ce18b1d47517e7e91fbb7cf19d4f01942804a519bc7c0bf32b6325dd5
fetch plutosvg-0.0.7.tar.gz https://github.com/sammycage/plutosvg/archive/v0.0.7/plutosvg-0.0.7.tar.gz 78561b571ac224030cdc450ca2986b4de915c2ba7616004a6d71a379bffd15f3
fetch rapidyaml-0.12.1-src.tgz https://github.com/biojppm/rapidyaml/releases/download/v0.12.1/rapidyaml-0.12.1-src.tgz e9efcdd17f86287748793cf21d106e461fcad8d103a3e5a23632afe93828660d

extract lz4-1.10.0.tar.gz lz4-1.10.0
cmake_install lz4 "$src/lz4-1.10.0/build/cmake" -DBUILD_SHARED_LIBS=OFF -DLZ4_BUILD_CLI=OFF -DLZ4_BUILD_LEGACY_LZ4C=OFF
extract zstd-1.5.7.tar.gz zstd-1.5.7
cmake_install zstd "$src/zstd-1.5.7/build/cmake" -DZSTD_BUILD_SHARED=OFF -DZSTD_BUILD_STATIC=ON -DZSTD_BUILD_PROGRAMS=OFF -DZSTD_BUILD_TESTS=OFF
extract libpng-1.6.58.tar.xz libpng-1.6.58
cmake_install libpng "$src/libpng-1.6.58" -DPNG_SHARED=OFF -DPNG_STATIC=ON -DPNG_TESTS=OFF -DPNG_TOOLS=OFF
extract libjpeg-turbo-3.2.0.tar.gz libjpeg-turbo-3.2.0
cmake_install jpeg "$src/libjpeg-turbo-3.2.0" -DENABLE_STATIC=ON -DENABLE_SHARED=OFF -DWITH_TURBOJPEG=OFF
extract libwebp-1.6.0.tar.gz libwebp-1.6.0
cmake_install webp "$src/libwebp-1.6.0" -DBUILD_SHARED_LIBS=OFF -DWEBP_BUILD_ANIM_UTILS=OFF -DWEBP_BUILD_CWEBP=OFF -DWEBP_BUILD_DWEBP=OFF -DWEBP_BUILD_GIF2WEBP=OFF -DWEBP_BUILD_IMG2WEBP=OFF -DWEBP_BUILD_VWEBP=OFF -DWEBP_BUILD_WEBPINFO=OFF -DWEBP_BUILD_WEBPMUX=OFF -DWEBP_BUILD_EXTRAS=OFF
extract SDL3-3.4.12.tar.gz SDL3-3.4.12
cmake_install sdl "$src/SDL3-3.4.12" -DBUILD_SHARED_LIBS=OFF -DSDL_SHARED=OFF -DSDL_STATIC=ON -DSDL_TEST_LIBRARY=OFF
extract freetype-2.14.3.tar.xz freetype-2.14.3
extract harfbuzz-14.2.0.tar.gz harfbuzz-14.2.0
cmake_install harfbuzz "$src/harfbuzz-14.2.0" -DBUILD_SHARED_LIBS=OFF -DHB_BUILD_UTILS=OFF -DHB_BUILD_SUBSET=OFF
cmake_install freetype "$src/freetype-2.14.3" -DBUILD_SHARED_LIBS=OFF -DFT_REQUIRE_ZLIB=ON -DFT_REQUIRE_PNG=ON -DFT_REQUIRE_HARFBUZZ=ON -DFT_DISABLE_BZIP2=ON -DFT_DISABLE_BROTLI=ON
extract plutovg-1.3.2.tar.gz plutovg-1.3.2
cmake_install plutovg "$src/plutovg-1.3.2" -DBUILD_SHARED_LIBS=OFF -DPLUTOVG_BUILD_EXAMPLES=OFF
extract plutosvg-0.0.7.tar.gz plutosvg-0.0.7
cmake_install plutosvg "$src/plutosvg-0.0.7" -DBUILD_SHARED_LIBS=OFF -DPLUTOSVG_ENABLE_FREETYPE=ON -DPLUTOSVG_BUILD_EXAMPLES=OFF
extract rapidyaml-0.12.1-src.tgz rapidyaml-0.12.1
cmake_install rapidyaml "$src/rapidyaml-0.12.1" -DBUILD_SHARED_LIBS=OFF -DRYML_BUILD_TESTS=OFF -DRYML_BUILD_TOOLS=OFF

python3 - "$prefix" "$src" <<'PY'
import hashlib, json, pathlib, sys
p = pathlib.Path(sys.argv[1])
items = sorted(x for x in p.rglob('*') if x.is_file())
archives = pathlib.Path(sys.argv[2])
source_files = sorted(x for x in archives.iterdir() if x.is_file())
(p / 'metal-deps-manifest.json').write_text(json.dumps({
  'origin': 'PCSX2 v2.8.2 official macOS dependency recipe', 'arch': 'arm64', 'minimum_macos': '11.0',
  'packages': {
    'SDL3': '3.4.12', 'libpng': '1.6.58', 'libjpeg-turbo': '3.2.0', 'libwebp': '1.6.0',
    'FreeType': '2.14.3', 'HarfBuzz': '14.2.0', 'zstd': '1.5.7', 'LZ4': '1.10.0',
    'PlutoVG': '1.3.2', 'PlutoSVG': '0.0.7', 'rapidyaml': '0.12.1',
    'curl': 'macOS SDK 11.3', 'libpcap': 'macOS SDK 11.3', 'zlib': 'macOS SDK 11.3'
  },
  'source_archives': {x.name: hashlib.sha256(x.read_bytes()).hexdigest() for x in source_files},
  'installed_files': {str(x.relative_to(p)): hashlib.sha256(x.read_bytes()).hexdigest() for x in items}
}, indent=2) + '\n')
PY
echo "Pinned static dependency prefix ready: $prefix"

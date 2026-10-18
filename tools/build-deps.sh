#!/usr/bin/env bash
# Build every local dependency under tools/ and build/ from pinned, hash-verified
# sources. Idempotent: each section is skipped when its output already exists.
#   tools/build-deps.sh                      # build what is missing
#   TARZAN_FORCE="bridge spu2" tools/build-deps.sh   # rebuild named sections
# Sections: pkgconf ffmpeg metal-deps metal-resources bridge spu2
# Needs: toolchain/bootstrap.sh done (or TARZAN_LLVM set), `python3 tools/deps.py sync` done.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LLVM="${TARZAN_LLVM:-$ROOT/tools/build/llvm19}"
CMAKE="$ROOT/tools/build/cmake-3.31.12/CMake.app/Contents/bin/cmake"
CTEST="$(dirname "$CMAKE")/ctest"
NINJA="$ROOT/tools/build/ninja-1.13.1/ninja"
TOOLCHAIN="$ROOT/cmake/toolchain-llvm19.cmake"
SDK="$(xcrun --show-sdk-path)"
# Parallelism comes from the one shared host-derived formula (tools/jobs.py; TZ_JOBS overrides).
NCPU="${TZ_JOBS:-$(python3 "$ROOT/tools/jobs.py")}"
LOGS="$ROOT/logs"
FORCE=" ${TARZAN_FORCE:-} "
mkdir -p "$LOGS"
export TARZAN_LLVM="$LLVM"

for tool in "$CMAKE" "$NINJA" "$LLVM/bin/clang++"; do
  [[ -x "$tool" ]] || { echo "Missing $tool; run toolchain/bootstrap.sh" >&2; exit 2; }
done

sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }
fetch() { # name url sha256 [dir; default $FETCH_DIR]
  local dir="${4:-$FETCH_DIR}" file="${4:-$FETCH_DIR}/$1"
  mkdir -p "$dir"
  if [[ -f "$file" && "$(sha256_of "$file")" == "$3" ]]; then return; fi
  rm -f "$file.part"
  curl -fL --retry 3 "$2" -o "$file.part"
  [[ "$(sha256_of "$file.part")" == "$3" ]] || { echo "SHA-256 mismatch: $1" >&2; rm -f "$file.part"; exit 1; }
  mv "$file.part" "$file"
}
needed() { # section output...   (true when the section must run)
  local s="$1"; shift
  [[ "$FORCE" == *" $s "* ]] && return 0
  local f
  for f in "$@"; do [[ -e "$f" ]] || return 0; done
  echo "== $s: up to date"
  return 1
}

# ---------------------------------------------------------------- pkgconf
build_pkgconf() {
  local base="$ROOT/tools/pkgconf" ver=3.0.5
  fetch "pkgconf-$ver.tar.xz" "https://distfiles.ariadne.space/pkgconf/pkgconf-$ver.tar.xz" \
    3acd3a8a3cce65a8d620321855d92fb602e026cbe8e13ee36bdec58483b59ace "$base/src"
  [[ -d "$base/src/pkgconf-$ver" ]] || tar -xJf "$base/src/pkgconf-$ver.tar.xz" -C "$base/src"
  mkdir -p "$base/bin"
  (
    cd "$base/src/pkgconf-$ver"
    export CC="$(xcrun --find clang)"
    export CFLAGS="-arch arm64 -mmacosx-version-min=11.0 -isysroot $SDK"
    export LDFLAGS="$CFLAGS"
    ./configure --prefix="$base" --disable-shared --enable-static 2>&1 | tee "$LOGS/pkgconf-configure.log"
    make -j"$NCPU" 2>&1 | tee "$LOGS/pkgconf-build.log"
    make install 2>&1 | tee "$LOGS/pkgconf-install.log"
  )
  ln -sf pkgconf "$base/bin/pkg-config"
}

# ----------------------------------------------------------------- ffmpeg
build_ffmpeg() {
  local base="$ROOT/tools/ffmpeg" ver=7.1.2
  local prefix="$base/arm64"
  fetch "ffmpeg-$ver.tar.xz" "https://ffmpeg.org/releases/ffmpeg-$ver.tar.xz" \
    089bc60fb59d6aecc5d994ff530fd0dcb3ee39aa55867849a2bbc4e555f9c304 "$base"
  mkdir -p "$base/src" "$prefix"
  [[ -d "$base/src/ffmpeg-$ver" ]] || tar -xJf "$base/ffmpeg-$ver.tar.xz" -C "$base/src"
  (
    cd "$base/src/ffmpeg-$ver"
    export CC="$LLVM/bin/clang"
    export AR="$(xcrun --find ar)" NM="$(xcrun --find nm)" RANLIB="$(xcrun --find ranlib)" STRIP="$(xcrun --find strip)"
    export CFLAGS="-arch arm64 -mmacosx-version-min=11.0 -isysroot $SDK"
    export LDFLAGS="$CFLAGS"
    ./configure \
      --prefix="$prefix" --pkg-config-flags="--static" \
      --arch=aarch64 --target-os=darwin --enable-cross-compile \
      --cc="$CC" --ar="$AR" --nm="$NM" --ranlib="$RANLIB" --strip="$STRIP" --sysroot="$SDK" \
      --disable-shared --enable-static --disable-programs --disable-doc --disable-debug \
      --disable-network --disable-autodetect --disable-x86asm --disable-encoders \
      --disable-muxers --disable-filters --disable-devices --enable-pic \
      2>&1 | tee "$LOGS/ffmpeg-configure.log"
    make -j"$NCPU" V=1 2>&1 | tee "$LOGS/ffmpeg-build.log"
    make install 2>&1 | tee "$LOGS/ffmpeg-install.log"
  )
}

# ------------------------------------------------------------- metal-deps
# Minimal pinned PCSX2 2.8.2 macOS dependency set in a private arm64 prefix.
# Versions and SHA-256 values come from that source's official macOS CI recipe.
build_metal_deps() {
  local base="$ROOT/tools/metal-deps"
  local src="$base/src" prefix="$base/prefix" build="$base/build"
  FETCH_DIR="$src"
  mkdir -p "$src" "$prefix" "$build"
  export PATH="$LLVM/bin:$(dirname "$CMAKE"):$(dirname "$NINJA"):$PATH"
  export CC="$LLVM/bin/clang" CXX="$LLVM/bin/clang++" OBJC="$LLVM/bin/clang" OBJCXX="$LLVM/bin/clang++"
  export MACOSX_DEPLOYMENT_TARGET=11.0
  export PKG_CONFIG_PATH="$prefix/lib/pkgconfig"
  export CFLAGS="-I$prefix/include -ffile-prefix-map=$ROOT=." CXXFLAGS="-I$prefix/include -ffile-prefix-map=$ROOT=." LDFLAGS="-L$prefix/lib"
  local sdk="$SDK" nproc="$NCPU" cmake="$CMAKE" ninja="$NINJA"
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
}

# --------------------------------------------------------- metal-resources
# Provenance-checked PCSX2 v2.8.2 Metal shaders (pinned release asset + sha256).
build_metal_resources() {
  python3 -I "$ROOT/tools/prepare-metal-bridge.py"
}

# ----------------------------------------------------------------- bridge
# Metal GS bridge from deps/pcsx2 (deps/fukami supplies the bridge target).
build_bridge() {
  local pcsx2="$ROOT/deps/pcsx2" bridge="$ROOT/deps/fukami/tools/pcsx2-gs-bridge"
  local build="$ROOT/build/metal-bridge/pcsx2"
  local resources="$ROOT/build/metal-bridge/pcsx2-2.8.2-resources"
  local deps_prefix="$ROOT/tools/metal-deps/prefix"
  [[ -d "$pcsx2/.git" && -d "$bridge" ]] || { echo 'Missing deps/; run python3 tools/deps.py sync' >&2; exit 1; }
  # The dylib carries a relative LLVM runtime rpath instead of an absolute one.
  local rel
  rel="$(python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$LLVM/lib" "$build/pcsx2-gs-bridge")"
  local link="-L$LLVM/lib -stdlib=libc++ -Wl,-rpath,@loader_path/$rel"
  export PATH="$(dirname "$NINJA"):$PATH"
  export PKG_CONFIG_PATH="$deps_prefix/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
  "$CMAKE" -S "$pcsx2" -B "$build" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN" \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_MAKE_PROGRAM="$NINJA" \
    -DCMAKE_PREFIX_PATH="$deps_prefix" \
    -DENABLE_QT_UI=OFF -DENABLE_TESTS=OFF -DENABLE_GSRUNNER=OFF \
    -DUSE_VULKAN=OFF -DSKIP_POSTPROCESS_BUNDLE=ON \
    -DRRV_PCSX2_GS_BRIDGE_SOURCE_DIR="$bridge" \
    -DRRV_PCSX2_GS_METAL_RESOURCE_DIR="$resources" \
    -DPCSX2_COMPILER_RT_OS_VERSION_CHECK_SOURCE="$ROOT/tools/metal-deps/compat/llvm19-os_version_check.c" \
    -DCMAKE_EXE_LINKER_FLAGS="$link" \
    -DCMAKE_SHARED_LINKER_FLAGS="$link" \
    -DCMAKE_MODULE_LINKER_FLAGS="$link"
  CMAKE_BUILD_PARALLEL_LEVEL="$NCPU" "$CMAKE" --build "$build" --target rrv-pcsx2-gs-bridge
}

# ------------------------------------------------------------------- spu2
build_spu2() {
  local build="$ROOT/build/spu2-backend" pcsx2="$ROOT/deps/pcsx2" source="$ROOT/deps/fukami/tools/pcsx2-spu2"
  [[ -f "$pcsx2/pcsx2/SPU2/spu2sys.cpp" && -d "$source" ]] || { echo 'Missing deps/; run python3 tools/deps.py sync' >&2; exit 1; }
  {
    "$CMAKE" -S "$source" -B "$build" -G Ninja \
      -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN" \
      -DCMAKE_MAKE_PROGRAM="$NINJA" \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_EXE_LINKER_FLAGS="-L$LLVM/lib -stdlib=libc++ -Wl,-rpath,$LLVM/lib" \
      -DRRV_PCSX2_SOURCE_DIR="$pcsx2" \
      -DRRV_SPU2_BUILD_TESTS=ON \
      -DRRV_SPU2_TSAN_TESTS=OFF
    "$NINJA" -C "$build" -j"$NCPU"
    "$CMAKE" -E chdir "$build" "$CTEST" --output-on-failure
  } 2>&1 | tee "$LOGS/spu2-backend-build.log"
}

# ------------------------------------------------------------------- main
RES="$ROOT/build/metal-bridge/pcsx2-2.8.2-resources"
if needed pkgconf "$ROOT/tools/pkgconf/bin/pkgconf" "$ROOT/tools/pkgconf/bin/pkg-config"; then build_pkgconf; fi
if needed ffmpeg "$ROOT/tools/ffmpeg/arm64/lib/libavcodec.a"; then build_ffmpeg; fi
if needed metal-deps "$ROOT/tools/metal-deps/prefix/metal-deps-manifest.json"; then (build_metal_deps); fi
if needed metal-resources "$RES/default.metallib" "$RES/Metal22.metallib" "$RES/Metal23.metallib"; then build_metal_resources; fi
if needed bridge "$ROOT/build/metal-bridge/pcsx2/pcsx2-gs-bridge/librrv-pcsx2-gs-bridge.dylib" \
    "$ROOT/build/metal-bridge/pcsx2/3rdparty/soundtouch/libpcsx2-soundtouch.a"; then build_bridge; fi
if needed spu2 "$ROOT/build/spu2-backend/librrv_pcsx2_spu2.a"; then build_spu2; fi
echo "== build-deps done"

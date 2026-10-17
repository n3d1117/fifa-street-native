#!/usr/bin/env python3
"""Apply the guarded Tarzan generated-code experiments to four functions.

This is a BYOD post-generation step. It verifies the supplied SLES-50350 ELF
before editing and only writes the four named generated functions.
"""

from __future__ import annotations

import argparse
import os
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "generated/SLES-50350-symbolmap-entry"
DEFAULT_ELF = ROOT / "extracted/disc/SLES_503.50"

FILES = {
    "camera": "CAM_vAdjustCameraToViewport_0x3699c0.cpp",
    "init": "GAM_InitEngineState_Playing__Fv_0x35ec20.cpp",
    "sync": "osrSyncCallback__Fi_0x3f8cf0.cpp",
    "clock": "fn_vActualizeEngineClock_0x360140.cpp",
}
INCLUDE = '#include "experimental-settings.hpp"'
ZOOM_MARK = "// TARZAN_EXPERIMENT_HD_ZOOM"
FOV_MARK = "// TARZAN_EXPERIMENT_HD_FOV"
INIT_MARK = "// TARZAN_EXPERIMENT_HIGH_FPS_INIT"
SYNC_MARK = "// TARZAN_EXPERIMENT_HIGH_FPS_SYNC"
CLOCK_MARK = "// TARZAN_EXPERIMENT_CLOCK_TRACE"
CAMERA_TRACE_MARK = "// TARZAN_EXPERIMENT_CAMERA_CONTEXT_TRACE"


class PatchError(Exception):
    pass


def elf_word_at(data: bytes, address: int) -> int:
    if len(data) < 52 or data[:4] != b"\x7fELF":
        raise PatchError("input is not an ELF file")
    if data[4] != 1 or data[5] != 1:
        raise PatchError("expected a 32-bit little-endian ELF")
    fields = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    machine, phoff, ehsize, phentsize, phnum = fields[1], fields[4], fields[7], fields[8], fields[9]
    if machine != 8 or ehsize != 52 or phentsize < 32 or phnum > 128:
        raise PatchError("ELF is not the expected MIPS32 executable format")
    if phoff + phentsize * phnum > len(data):
        raise PatchError("ELF program header table is truncated")
    for i in range(phnum):
        off = phoff + i * phentsize
        p_type, p_offset, p_vaddr, _paddr, p_filesz = struct.unpack_from("<IIIII", data, off)
        if p_type != 1 or address < p_vaddr or address + 4 > p_vaddr + p_filesz:
            continue
        file_offset = p_offset + address - p_vaddr
        if file_offset + 4 > len(data):
            raise PatchError(f"ELF PT_LOAD for 0x{address:x} exceeds file bounds")
        return struct.unpack_from("<I", data, file_offset)[0]
    raise PatchError(f"guest address 0x{address:x} is not file-backed by PT_LOAD")


def verify_elf(path: Path) -> None:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PatchError(f"cannot read ELF {path}: {exc}") from exc
    expected = {
        0x369A3C: 0x3C023F00,
        0x369A7C: 0x4614A843,
        0x3699C4: 0x7FBF0030,
        0x3699C8: 0x7FB10020,
        0x338BD8: 0x0C0DA670,
        0x33B324: 0xAC850004,
        0x360388: 0xFC241A10,
    }
    for address, word in expected.items():
        actual = elf_word_at(data, address)
        if actual != word:
            raise PatchError(
                f"unsupported ELF variant at 0x{address:x}: expected 0x{word:08x}, "
                f"found 0x{actual:08x}"
            )


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected exactly one source anchor, found {count}")
    return text.replace(old, new, 1)


def ensure_include(text: str, label: str) -> str:
    if text.count(INCLUDE) > 1:
        raise PatchError(f"{label}: duplicate experimental-settings include")
    if INCLUDE in text:
        return text
    anchor = '#include "ps2_runtime.h"\n'
    return replace_once(text, anchor, anchor + INCLUDE + "\n", f"{label} include")


def ensure_standard_includes(text: str, required: tuple[str, ...], label: str) -> str:
    anchor = "#include <stdexcept>\n"
    if text.count(anchor) != 1:
        raise PatchError(f"{label}: expected one standard include anchor")
    missing = [header for header in required if f"#include <{header}>\n" not in text]
    return text.replace(anchor, anchor + "".join(f"#include <{header}>\n" for header in missing), 1)


def patch_camera(text: str) -> str:
    if text.count(ZOOM_MARK) == 0:
        old = """    // 0x369a3c: 0x3c023f00  lui         $v0, 0x3F00
    ctx->pc = 0x369a3cu;
    SET_GPR_S32(ctx, 2, (int32_t)((uint32_t)16128 << 16));"""
        new = """    // 0x369a3c: 0x3c023f00  lui         $v0, 0x3F00
    ctx->pc = 0x369a3cu;
    // TARZAN_EXPERIMENT_HD_ZOOM: upstream PAL-M zoom constant, opt-in.
    SET_GPR_U32(ctx, 2, tarzan_experiments_hd_widescreen()
                            ? 0x3f210000u : 0x3f000000u);"""
        text = replace_once(text, old, new, "camera zoom at PC 0x369a3c")
    elif text.count(ZOOM_MARK) != 1:
        raise PatchError("camera zoom: multiple experiment markers")

    if text.count(FOV_MARK) == 0:
        old = """    // 0x369a7c: 0x4614a843  div.s       $f1, $f21, $f20
    ctx->pc = 0x369a7cu;
    if (ctx->f[20] == 0.0f) { ctx->fcr31 |= 0x100000; /* DZ flag */ ctx->f[1] = copysignf(INFINITY, ctx->f[21] * 0.0f); } else ctx->f[1] = ctx->f[21] / ctx->f[20];"""
        new = """    // 0x369a7c: 0x4614a843  div.s       $f1, $f21, $f20
    ctx->pc = 0x369a7cu;
    // TARZAN_EXPERIMENT_HD_FOV: upstream PAL-M Y-FOV stub, opt-in.
    if (tarzan_experiments_hd_widescreen()) {
        if (ctx->f[20] == 0.0f) { ctx->fcr31 |= 0x100000; /* DZ flag */ ctx->f[1] = copysignf(INFINITY, ctx->f[21] * 0.0f); }
        else ctx->f[1] = ctx->f[21] / ctx->f[20];
        SET_GPR_U32(ctx, 1, 0x3f400000u);
        { const uint32_t bits = GPR_U32(ctx, 1); std::memcpy(&ctx->f[30], &bits, sizeof(bits)); }
        ctx->f[1] = FPU_MUL_S(ctx->f[1], ctx->f[30]);
    } else {
        if (ctx->f[20] == 0.0f) { ctx->fcr31 |= 0x100000; /* DZ flag */ ctx->f[1] = copysignf(INFINITY, ctx->f[21] * 0.0f); }
        else ctx->f[1] = ctx->f[21] / ctx->f[20];
    }"""
        text = replace_once(text, old, new, "camera FOV at PC 0x369a7c")
    elif text.count(FOV_MARK) != 1:
        raise PatchError("camera FOV: multiple experiment markers")

    trace = """    // TARZAN_EXPERIMENT_CAMERA_CONTEXT_TRACE: bounded, opt-in instance sampling.
    if (GPR_U32(ctx, 31) == 0x338BE0u) {
        const uint32_t worldDialog = GPR_U32(ctx, 17);
        uint32_t dialogId = 0, worldObject = 0;
        if (worldDialog <= 0x01FFFF08u) {
            dialogId = READ32(worldDialog + 4u);
            worldObject = READ32(worldDialog + 0xF4u);
        }
        tarzan_experiments_trace_camera_context(GPR_U32(ctx, 31), GPR_U32(ctx, 4),
                                                worldDialog, dialogId, worldObject);
    } else {
        tarzan_experiments_trace_camera_context(GPR_U32(ctx, 31), GPR_U32(ctx, 4),
                                                0, 0, 0);
    }
"""
    if text.count(CAMERA_TRACE_MARK):
        if text.count(CAMERA_TRACE_MARK) != 1:
            raise PatchError("camera trace: multiple experiment markers")
    else:
        old_trace = """    // Diagnostic-only: compare the active camera/dialog instances in menu and gameplay.
    // Only the known C3DWorldDlg caller has a meaningful $s1 dialog pointer here.
    if (GPR_U32(ctx, 31) == 0x338BE0u) {
        const uint32_t worldDialog = GPR_U32(ctx, 17);
        uint32_t dialogId = 0, worldObject = 0;
        if (worldDialog <= 0x01FFFF08u) {
            dialogId = READ32(worldDialog + 4u);
            worldObject = READ32(worldDialog + 0xF4u);
        }
        tarzan_experiments_trace_camera_context(GPR_U32(ctx, 31), GPR_U32(ctx, 4),
                                                worldDialog, dialogId, worldObject);
    } else {
        tarzan_experiments_trace_camera_context(GPR_U32(ctx, 31), GPR_U32(ctx, 4),
                                                0, 0, 0);
    }
"""
        anchor = """    ctx->pc = 0x3699c0u;

    // 0x3699c0: 0x27bdffb0  addiu       $sp, $sp, -0x50"""
        if text.count(anchor) == 1:
            text = text.replace(anchor, """    ctx->pc = 0x3699c0u;

""" + trace + """    // 0x3699c0: 0x27bdffb0  addiu       $sp, $sp, -0x50""", 1)
        elif text.count(old_trace) == 1:
            text = text.replace(old_trace, trace, 1)
        else:
            raise PatchError("camera trace: expected one CAM entry anchor or known diagnostic block")
        if text.count(CAMERA_TRACE_MARK) != 1:
            raise PatchError("camera trace: failed to install marker")

    old_getter = "tarzan_experiments_hd_widescreen()"
    new_getter = "tarzan_experiments_widescreen_camera(rdram, GPR_U32(ctx, 29))"
    if text.count(new_getter) == 0:
        if text.count(old_getter) != 2:
            raise PatchError(f"camera scope gate: expected two legacy getter calls, found {text.count(old_getter)}")
        text = text.replace(old_getter, new_getter)
    elif text.count(new_getter) != 2 or old_getter in text:
        raise PatchError("camera scope gate: mixed or duplicate getter forms")
    return ensure_include(text, "camera")


def patch_init(text: str) -> str:
    legacy = """    // Controlled opt-in implementation of the upstream SLES-50350 50 FPS patch.
    // The original state-dependent value remains the default; no clock multiplier.
    do {
        static const bool enhanced = [] {
            const char* value = std::getenv("TARZAN_EXPERIMENT_50FPS");
            return value && value[0] == '1' && value[1] == '\\0';
        }();
        const uint8_t original = static_cast<uint8_t>(GPR_U32(ctx, 2));
        const uint8_t value = enhanced ? 0u : original;
        ps2TraceGuestWrite(rdram, 0x6B00B8u, 1u, value, 0u, "WRITE8", ctx);
        FAST_WRITE8(0x6B00B8u, value);
        std::fprintf(stderr, "[tarzan-frame-mode] originalMinSync=%u effectiveMinSync=%u experimental50=%u\\n",
                     original, value, enhanced ? 1u : 0u);
    } while (0);"""
    original = """    WRITE8(ADD32(GPR_U32(ctx, 1), 184), (uint8_t)GPR_U32(ctx, 2));"""
    new = """    // TARZAN_EXPERIMENT_HIGH_FPS_INIT: settings opt-in plus legacy A/B switch.
    do {
        const char* legacy = std::getenv("TARZAN_EXPERIMENT_50FPS");
        const bool enhanced = tarzan_experiments_high_fps() != 0 ||
                              (legacy && legacy[0] == '1' && legacy[1] == '\\0');
        const uint8_t original = static_cast<uint8_t>(GPR_U32(ctx, 2));
        const uint8_t value = enhanced ? 0u : original;
        ps2TraceGuestWrite(rdram, 0x6B00B8u, 1u, value, 0u, "WRITE8", ctx);
        FAST_WRITE8(0x6B00B8u, value);
        std::fprintf(stderr, "[tarzan-frame-mode] originalMinSync=%u effectiveMinSync=%u experimental50=%u\\n",
                     original, value, enhanced ? 1u : 0u);
    } while (0);"""
    if text.count(INIT_MARK):
        if text.count(INIT_MARK) != 1:
            raise PatchError("init FPS: multiple experiment markers")
    else:
        matches = text.count(legacy) + text.count(original)
        if matches != 1:
            raise PatchError(f"init FPS hook at PC 0x35ec74: expected one known source form, found {matches}")
        text = text.replace(legacy if legacy in text else original, new, 1)
    text = ensure_include(text, "init")
    return ensure_standard_includes(text, ("cstdlib", "cstdio"), "init")


def patch_sync(text: str) -> str:
    legacy = """    // Runtime A/B probe of the published 50 FPS sync guard; default is untouched.
    // A marker allows comparison in the same loaded mission and process.
    {
        static const char* marker = std::getenv("TARZAN_EXPERIMENT_50FPS_FILE");
        uint8_t value = FAST_READ8(0x6B00B8u);
        if (marker && *marker) {
            struct stat info{};
            if (stat(marker, &info) == 0) value = 0u;
        }
        SET_GPR_ZE32(ctx, 2, value);
    }"""
    original = """    SET_GPR_ZE32(ctx, 2, (uint8_t)READ8(ADD32(GPR_U32(ctx, 1), 184)));"""
    new = """    // TARZAN_EXPERIMENT_HIGH_FPS_SYNC: setting and legacy marker enable mode 0.
    {
        const char* marker = std::getenv("TARZAN_EXPERIMENT_50FPS_FILE");
        uint8_t value = FAST_READ8(0x6B00B8u);
        if (tarzan_experiments_high_fps() != 0) value = 0u;
        if (marker && *marker) {
            struct stat info{};
            if (stat(marker, &info) == 0) value = 0u;
        }
        SET_GPR_ZE32(ctx, 2, value);
    }"""
    if text.count(SYNC_MARK):
        if text.count(SYNC_MARK) != 1:
            raise PatchError("sync FPS: multiple experiment markers")
    else:
        matches = text.count(legacy) + text.count(original)
        if matches != 1:
            raise PatchError(f"sync FPS hook at PC 0x3f8df4: expected one known source form, found {matches}")
        text = text.replace(legacy if legacy in text else original, new, 1)
    text = ensure_include(text, "sync")
    return ensure_standard_includes(text, ("cstdlib", "sys/stat.h"), "sync")


def patch_clock(text: str) -> str:
    old = """    // 0x360388: 0xfc241a10  sd          $a0, 0x1A10($at)
    ctx->pc = 0x360388u;
    WRITE64(ADD32(GPR_U32(ctx, 1), 6672), GPR_U64(ctx, 4));"""
    new = """    // 0x360388: 0xfc241a10  sd          $a0, 0x1A10($at)
    ctx->pc = 0x360388u;
    WRITE64(ADD32(GPR_U32(ctx, 1), 6672), GPR_U64(ctx, 4));
    // TARZAN_EXPERIMENT_CLOCK_TRACE: guest clock pair after its original store.
    tarzan_experiments_trace_clock(GPR_U64(ctx, 4), GPR_U64(ctx, 16));"""
    if text.count(CLOCK_MARK):
        if text.count(CLOCK_MARK) != 1:
            raise PatchError("clock trace: multiple experiment markers")
    else:
        text = replace_once(text, old, new, "clock trace store at PC 0x360388")
    return ensure_include(text, "clock")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-dir", type=Path, default=GENERATED)
    parser.add_argument("--elf", type=Path, default=DEFAULT_ELF)
    args = parser.parse_args()
    try:
        verify_elf(args.elf)
        paths = {key: args.generated_dir / name for key, name in FILES.items()}
        contents = {key: path.read_text() for key, path in paths.items()}
        updated = {
            "camera": patch_camera(contents["camera"]),
            "init": patch_init(contents["init"]),
            "sync": patch_sync(contents["sync"]),
            "clock": patch_clock(contents["clock"]),
        }
    except (OSError, PatchError) as exc:
        parser.error(str(exc))
    changed = []
    for key, path in paths.items():
        if updated[key] != contents[key]:
            path.write_text(updated[key])
            changed.append(path.name)
    print("patched: " + (", ".join(changed) if changed else "already current"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

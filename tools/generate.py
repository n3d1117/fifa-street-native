#!/usr/bin/env python3
"""Regenerate the game C++ from the canonical PS2Recomp config, safely.

Compiles into a temporary directory, applies the guarded Tarzan experiment
hooks, checks structural quality (0 skipped, 0 decode failures, 0 unhandled
instructions, 0 errors, entry registered), compares the inventory with the
previous run (build/generate-inventory.json) and only then atomically replaces
the files that changed. Unchanged files keep their mtimes.

  tools/generate.py                 regenerate generated/SLES-50350-symbolmap-entry
  tools/generate.py --out DIR       same, into DIR (for comparing against generated/)
  tools/generate.py --dry-run       validate inputs only

Exit codes: 0 ok, 1 failed, 2 missing prerequisite.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

ROOT = Path(os.environ.get("TARZAN_ROOT") or Path(__file__).resolve().parents[1])
DEFAULT_CONFIG = ROOT / "config/SLES-50350.symbolmap-entry.toml"
DEFAULT_COMPILER = ROOT / "build/game-metal/ps2recomp/ps2xRecomp/ps2_recomp"
INVENTORY = ROOT / "build/generate-inventory.json"


class Missing(Exception):
    """Missing prerequisite (exit 2)."""


def inside(path: Path, root: Path) -> bool:
    """Containment check that ignores case (macOS paths differ only by case)."""
    p, r = str(path).casefold(), str(root).casefold()
    return p != r and p.startswith(r.rstrip("/") + "/")


# ---- experiment hooks (formerly patch-generated-experiments.py) -------------
FILES = {
    "cheats": "Behavior_Loop__9CAIGlobalFv_0x2da7c0.cpp",
    "logo": "GAM_InitEngineState_StartProgram__Fv_0x35dff0.cpp",
    "camera": "CAM_vAdjustCameraToViewport_0x3699c0.cpp",
    "init": "GAM_InitEngineState_Playing__Fv_0x35ec20.cpp",
    "sync": "osrSyncCallback__Fi_0x3f8cf0.cpp",
    "clock": "fn_vActualizeEngineClock_0x360140.cpp",
    "language_start": "Start__5CMenuFPP5CMenub25MNU_eActionOnPreviousMenu_0x34fbc0.cpp",
    "language_menu": "Process__5CMenuFv_0x350350.cpp",
    "language_requested": "ProcessMenuMessage_SetRequestedLanguage__14CAIMenuManagerFP77ubiList_P21CMenuMessagePar_0x2cd410.cpp",
    "language_accepted": "ProcessMenuMessage_SetLanguage__14CAIMenuManagerFP77ubiList_P21CMenuMessageParameter_41_0x2c0d60.cpp",
}
INCLUDE = '#include "experimental-settings.hpp"'
ZOOM_MARK = "// TARZAN_EXPERIMENT_HD_ZOOM"
FOV_MARK = "// TARZAN_EXPERIMENT_HD_FOV"
INIT_MARK = "// TARZAN_EXPERIMENT_HIGH_FPS_INIT"
SYNC_MARK = "// TARZAN_EXPERIMENT_HIGH_FPS_SYNC"
CLOCK_MARK = "// TARZAN_EXPERIMENT_CLOCK_TRACE"
CAMERA_TRACE_MARK = "// TARZAN_EXPERIMENT_CAMERA_CONTEXT_TRACE"
CHEATS_MARK = "// TARZAN_EXPERIMENT_BUILTIN_CHEATS"
LOGO_MARK = "// TARZAN_EXPERIMENT_SKIP_STARTUP_LOGO"
LANGUAGE_START_MARK = "// TARZAN_EXPERIMENT_LANGUAGE_START"
LANGUAGE_MENU_MARK = "// TARZAN_EXPERIMENT_LANGUAGE_MENU"
LANGUAGE_REQUESTED_MARK = "// TARZAN_EXPERIMENT_LANGUAGE_REQUESTED"
LANGUAGE_ACCEPTED_MARK = "// TARZAN_EXPERIMENT_LANGUAGE_ACCEPTED"


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
        0x2DAC2C: 0x3C01004D,
        0x2DADA4: 0x924300A8,
        0x2DADD4: 0xA02285F8,
        0x2DAE60: 0x24050063,
        0x2DAE64: 0x0C0B804C,
        0x2DAE68: 0x24060001,
        0x35E2B0: 0x3C02004B,
        0x35E2B4: 0x24446460,
        0x35E2C4: 0x0C0D8EAC,
        0x35E2C8: 0x70004628,
        0x4B6460: 0x6F676F4C,
        0x34FCE0: 0x0C0D5B88,
        0x350350: 0x27BDFFB0,
        0x2CD48C: 0x7BBF0010,
        0x2C0E40: 0x24020001,
    }
    for address, sequence in ((0x4B3210, b"LURDDLURRDLUURDL"),
                              (0x4B3230, b"RLUDDURLLRDUUDLR")):
        for offset in range(0, 16, 4):
            expected[address + offset] = int.from_bytes(sequence[offset:offset + 4], "little")
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
    { uint32_t divFsBits; uint32_t divFtBits; std::memcpy(&divFsBits, &ctx->f[21], sizeof(divFsBits)); std::memcpy(&divFtBits, &ctx->f[20], sizeof(divFtBits)); if ((divFtBits & 0x7f800000u) == 0) ctx->fcr31 |= ((divFsBits & 0x7f800000u) == 0) ? 0x00020040u : 0x00010020u; ctx->f[1] = FPU_DIV_S(ctx->f[21], ctx->f[20]); }"""
        new = """    // 0x369a7c: 0x4614a843  div.s       $f1, $f21, $f20
    ctx->pc = 0x369a7cu;
    // TARZAN_EXPERIMENT_HD_FOV: upstream PAL-M Y-FOV stub, opt-in.
    if (tarzan_experiments_hd_widescreen()) {
        { uint32_t divFsBits; uint32_t divFtBits; std::memcpy(&divFsBits, &ctx->f[21], sizeof(divFsBits)); std::memcpy(&divFtBits, &ctx->f[20], sizeof(divFtBits)); if ((divFtBits & 0x7f800000u) == 0) ctx->fcr31 |= ((divFsBits & 0x7f800000u) == 0) ? 0x00020040u : 0x00010020u; ctx->f[1] = FPU_DIV_S(ctx->f[21], ctx->f[20]); }
        SET_GPR_U32(ctx, 1, 0x3f400000u);
        { const uint32_t bits = GPR_U32(ctx, 1); std::memcpy(&ctx->f[30], &bits, sizeof(bits)); }
        ctx->f[1] = FPU_MUL_S(ctx->f[1], ctx->f[30]);
    } else {
        { uint32_t divFsBits; uint32_t divFtBits; std::memcpy(&divFsBits, &ctx->f[21], sizeof(divFsBits)); std::memcpy(&divFtBits, &ctx->f[20], sizeof(divFtBits)); if ((divFtBits & 0x7f800000u) == 0) ctx->fcr31 |= ((divFsBits & 0x7f800000u) == 0) ? 0x00020040u : 0x00010020u; ctx->f[1] = FPU_DIV_S(ctx->f[21], ctx->f[20]); }
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
    # Current recompiler lowers this store to a constant-address traced FAST_WRITE8.
    original_const = """    do { uint8_t _value = static_cast<uint8_t>((uint8_t)GPR_U32(ctx, 2)); ps2TraceGuestWrite(rdram, 0x6B00B8u, 1u, _value, 0u, "WRITE8", ctx); FAST_WRITE8(0x6B00B8u, _value); } while (0);"""
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
        forms = (legacy, original, original_const)
        matches = sum(text.count(form) for form in forms)
        if matches != 1:
            raise PatchError(f"init FPS hook at PC 0x35ec74: expected one known source form, found {matches}")
        text = text.replace(next(form for form in forms if form in text), new, 1)
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
    # Current recompiler lowers this load to a constant-address FAST_READ8.
    original_const = """    SET_GPR_ZE32(ctx, 2, (uint8_t)FAST_READ8(0x6B00B8u));"""
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
        forms = (legacy, original, original_const)
        matches = sum(text.count(form) for form in forms)
        if matches != 1:
            raise PatchError(f"sync FPS hook at PC 0x3f8df4: expected one known source form, found {matches}")
        text = text.replace(next(form for form in forms if form in text), new, 1)
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


def patch_language_start(text: str) -> str:
    code = """    // TARZAN_EXPERIMENT_LANGUAGE_START: select before the guest focuses its startup item.
    {
        const uint32_t original = READ32(ADD32(GPR_U32(ctx, 16), 0xA4u));
        const uint32_t chosen = tarzan_experiments_boot_language_item(
            rdram, GPR_U32(ctx, 16), original);
        if (chosen != original) WRITE32(ADD32(GPR_U32(ctx, 16), 0xA4u), chosen);
    }
"""
    anchor = """label_34fce0:
    // 0x34fce0: 0xc0d5b88  jal         func_356E20
"""
    if text.count(LANGUAGE_START_MARK):
        if text.count(LANGUAGE_START_MARK) != 1:
            raise PatchError("language startup item: multiple experiment markers")
    else:
        text = replace_once(text, anchor, "label_34fce0:\n" + code + anchor.split("\n", 1)[1],
                            "language startup item before PC 0x34fce0")
    return ensure_include(text, "language startup item")


def patch_language_menu(text: str) -> str:
    code = """    // TARZAN_EXPERIMENT_LANGUAGE_MENU: advance saved-language startup state once per menu entry.
    tarzan_experiments_boot_language_menu(rdram, GPR_U32(ctx, 4));
"""
    anchor = """label_350350:
    // 0x350350: 0x27bdffb0  addiu       $sp, $sp, -0x50
"""
    if text.count(LANGUAGE_MENU_MARK):
        if text.count(LANGUAGE_MENU_MARK) != 1:
            raise PatchError("language menu entry: multiple experiment markers")
    else:
        text = replace_once(text, anchor, "label_350350:\n" + code + anchor.split("\n", 1)[1],
                            "language menu entry at PC 0x350350")
    return ensure_include(text, "language menu entry")


def patch_language_requested(text: str) -> str:
    code = """    // TARZAN_EXPERIMENT_LANGUAGE_REQUESTED: the request handler returned success.
    tarzan_experiments_language_accepted(GPR_U32(ctx, 16));
"""
    anchor = """label_2cd48c:
    // 0x2cd48c: 0x7bbf0010  lq          $ra, 0x10($sp)
"""
    if text.count(LANGUAGE_REQUESTED_MARK):
        if text.count(LANGUAGE_REQUESTED_MARK) != 1:
            raise PatchError("language request acceptance: multiple experiment markers")
    else:
        text = replace_once(text, anchor, "label_2cd48c:\n" + code + anchor.split("\n", 1)[1],
                            "language request acceptance at PC 0x2cd48c")
    return ensure_include(text, "language request acceptance")


def patch_language_accepted(text: str) -> str:
    code = """    // TARZAN_EXPERIMENT_LANGUAGE_ACCEPTED: successful language change only.
    tarzan_experiments_language_accepted(GPR_U32(ctx, 16));
"""
    anchor = """label_2c0e40:
    // 0x2c0e40: 0x24020001  addiu       $v0, $zero, 0x1
"""
    if text.count(LANGUAGE_ACCEPTED_MARK):
        if text.count(LANGUAGE_ACCEPTED_MARK) != 1:
            raise PatchError("language acceptance: multiple experiment markers")
    else:
        text = replace_once(text, anchor, "label_2c0e40:\n" + code + anchor.split("\n", 1)[1],
                            "successful language change at PC 0x2c0e40")
    return ensure_include(text, "language acceptance")


def patch_cheats(text: str) -> str:
    code = """    // TARZAN_EXPERIMENT_BUILTIN_CHEATS: the guest already matched its configured cheat menu.
    {
        const uint32_t owner = GPR_U32(ctx, 18);
        const int cheat = tarzan_experiments_builtin_cheat(rdram, owner);
        if (cheat) {
            const char* sequence = cheat == 1 ? "RLUDDURLLRDUUDLR" : "LURDDLURRDLUURDL";
            for (uint32_t i = 0; i < 16u; ++i) WRITE8(ADD32(owner, 0x98u + i), sequence[i]);
            WRITE8(ADD32(owner, 0xA8u), 16u);
            WRITE8(ADD32(owner, 0xA9u), 1u);
            ctx->pc = 0x2DADA4u;
            goto label_2dada4;
        }
    }
"""
    anchor = """label_2dac2c:
    // 0x2dac2c: 0x3c01004d  lui         $at, 0x4D
"""
    if text.count(CHEATS_MARK):
        if text.count(CHEATS_MARK) != 1:
            raise PatchError("built-in cheats: multiple experiment markers")
    else:
        text = replace_once(text, anchor, "label_2dac2c:\n" + code + anchor.split("\n", 1)[1],
                            "built-in cheat menu at PC 0x2dac2c")
    return ensure_include(text, "built-in cheats")


def patch_logo(text: str) -> str:
    old = """    if (!runtime->dispatchGuestBranch(rdram, ctx, 0x363AB0u, 0x35E2C4u, 0x35E2CCu, PS2Runtime::GuestBranchKind::DirectCall, "JAL")) {
        return;
    }
"""
    new = """    // TARZAN_EXPERIMENT_SKIP_STARTUP_LOGO: exact startup call, after its delay slot.
    if (!tarzan_experiments_skip_startup_logo(rdram, GPR_U32(ctx, 4))) {
        if (!runtime->dispatchGuestBranch(rdram, ctx, 0x363AB0u, 0x35E2C4u, 0x35E2CCu, PS2Runtime::GuestBranchKind::DirectCall, "JAL")) {
            return;
        }
    }
"""
    if text.count(LOGO_MARK):
        if text.count(LOGO_MARK) != 1:
            raise PatchError("startup logo: multiple experiment markers")
    else:
        text = replace_once(text, old, new, "startup logo at PC 0x35e2c4")
    return ensure_include(text, "startup logo")



def apply_experiments(generated_dir: Path, elf: Path) -> str:
    verify_elf(elf)
    paths = {key: generated_dir / name for key, name in FILES.items()}
    contents = {key: path.read_text() for key, path in paths.items()}
    updated = {
        "cheats": patch_cheats(contents["cheats"]),
        "logo": patch_logo(contents["logo"]),
        "camera": patch_camera(contents["camera"]),
        "init": patch_init(contents["init"]),
        "sync": patch_sync(contents["sync"]),
        "clock": patch_clock(contents["clock"]),
        "language_start": patch_language_start(contents["language_start"]),
        "language_menu": patch_language_menu(contents["language_menu"]),
        "language_requested": patch_language_requested(contents["language_requested"]),
        "language_accepted": patch_language_accepted(contents["language_accepted"]),
    }
    changed = []
    for key, path in paths.items():
        if updated[key] != contents[key]:
            path.write_text(updated[key])
            changed.append(path.name)
    return ", ".join(changed)


# ---- generation -------------------------------------------------------------
def fail(message: str) -> None:
    raise RuntimeError(message)


def get_general_string(config_text: str, key: str) -> Tuple[str, Tuple[int, int]]:
    """Return a simple quoted [general] string and its source span."""
    section = re.search(r"(?ms)^\[general\]\s*$.*?(?=^\[|\Z)", config_text)
    if not section:
        fail("config has no [general] section")
    matches = list(re.finditer(
        rf"(?m)^{re.escape(key)}\s*=\s*(['\"])(.*?)\1\s*(?:#.*)?$",
        section.group(0),
    ))
    if len(matches) != 1:
        fail(f"expected exactly one [general].{key} string, found {len(matches)}")
    m = matches[0]
    absolute_start = section.start() + m.start(2)
    absolute_end = section.start() + m.end(2)
    return m.group(2), (absolute_start, absolute_end)


def replace_output(config_text: str, output_dir: Path) -> str:
    _old, (start, end) = get_general_string(config_text, "output")
    # JSON string escaping is compatible with TOML basic strings for paths.
    return config_text[:start] + json.dumps(str(output_dir))[1:-1] + config_text[end:]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_summary(log_text: str) -> Dict[str, int]:
    patterns = {
        "discovered": r"Functions discovered:\s*(\d+)",
        "processed": r"Functions processed:\s*(\d+)",
        "recompiled": r"Functions processed:.*?recompiled:\s*(\d+)",
        "stubs": r"Functions processed:.*?stubs:\s*(\d+)",
        "skipped": r"skipped:\s*(\d+)",
        "decode_failures": r"decode failures:\s*(\d+)",
        "unhandled": r"Unhandled instructions:\s*(\d+)",
        "errors": r"Warnings:\s*\d+\s*,\s*errors:\s*(\d+)",
    }
    summary: Dict[str, int] = {}
    for name, pattern in patterns.items():
        matches = re.findall(pattern, log_text, flags=re.IGNORECASE)
        if not matches:
            fail(f"compiler log lacks required `{name}` summary")
        summary[name] = int(matches[-1])
    return summary


def symbol_map_info(path: Path) -> Tuple[int, int]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or not {"Name", "Start"}.issubset(rows[0]):
        fail(f"symbol map is empty or lacks Name/Start columns: {path}")
    starts = []
    for row in rows:
        try:
            starts.append(int(row["Start"], 0))
        except (TypeError, ValueError):
            fail(f"invalid Start address in symbol map row: {row}")
    return len(rows), starts[0]  # canonical map's first row is the ELF entry


def startup_artifact_names(stage: Path, entry: int) -> Tuple[str, str]:
    register_name = "register_functions.cpp"
    suffix = f"_0x{entry:x}.cpp"
    matches = [name for name in regular_files(stage) if name.endswith(suffix)]
    if len(matches) != 1:
        fail(f"expected one generated startup entry ending {suffix}, found {len(matches)}")
    return register_name, matches[0]


def validate_required_artifacts(output: Path, entry: int) -> None:
    register_name = output / "register_functions.cpp"
    if not register_name.is_file():
        fail(f"required registration artifact missing: {register_name}")
    suffix = f"_0x{entry:x}.cpp"
    matches = [p for p in output.iterdir() if p.is_file() and p.name.endswith(suffix)]
    if len(matches) != 1:
        fail(f"expected one startup entry artifact ending {suffix} in {output}, found {len(matches)}")
    if matches[0].stem not in register_name.read_text(encoding="utf-8", errors="replace"):
        fail(f"startup entry {matches[0].name} is absent from register_functions.cpp")


def regular_files(root: Path) -> Dict[str, Path]:
    result: Dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            fail(f"staged output contains symlink: {path.relative_to(root)}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path
    return result


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    temp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)



def mapped_executable_bytes(elf: Path, symbol_map: Path) -> Tuple[int, int]:
    """(mapped executable bytes, executable section bytes); coverage is not correctness."""
    def merged(ranges):
        total = end = 0
        for lo, hi in sorted(ranges):
            total += max(0, hi - max(lo, end))
            end = max(end, hi)
        return total
    with symbol_map.open(newline="") as stream:
        ranges = [(int(r["Start"], 0), int(r["End"], 0)) for r in csv.DictReader(stream)]
    data = elf.read_bytes()
    shoff = struct.unpack_from("<I", data, 32)[0]
    shsize, shcount = struct.unpack_from("<HH", data, 46)
    executable = []
    for i in range(shcount):
        _, _, flags, address, _, size = struct.unpack_from("<6I", data, shoff + i * shsize)
        if flags & 4 and size:
            executable.append((address, address + size))
    inter = [(max(a, lo), min(b, hi)) for a, b in ranges for lo, hi in executable if max(a, lo) < min(b, hi)]
    return merged(inter), merged(executable)


def stub_count(stage: Path) -> int:
    path = stage / "ps2_recompiled_stubs.h"
    return len(set(re.findall(r"void \w+_0x([0-9a-f]+)\(", path.read_text(errors="replace")))) if path.is_file() else 0


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    parser.add_argument("--out", type=Path, help="output directory (default: [general].output of the config)")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/regenerate-game.json")
    parser.add_argument("--dry-run", action="store_true", help="validate inputs without running the compiler")
    args = parser.parse_args()

    config = args.config.resolve()
    compiler = args.compiler.resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = ROOT / "build/logs" / f"generate-compiler-{timestamp}.log"
    report = {"config": str(config), "compiler": str(compiler), "log": str(log_path),
              "changed_count": 0, "changed_files": [], "status": "validation_failed"}
    try:
        if not config.is_file():
            raise Missing(f"config not found: {config} -> ./tz disc")
        if not compiler.is_file() or not os.access(compiler, os.X_OK):
            raise Missing(f"recompiler missing: {compiler} -> ./tz build")
        original_text = config.read_text(encoding="utf-8")
        input_value, _ = get_general_string(original_text, "input")
        output_value, _ = get_general_string(original_text, "output")
        map_value, _ = get_general_string(original_text, "ghidra_output")
        elf = Path(input_value)
        if not elf.is_absolute():
            elf = config.parent / elf
        elf = elf.resolve()
        if not elf.is_file():
            raise Missing(f"ELF input not found: {elf} -> ./tz disc <iso>")
        symbol_map = Path(map_value)
        if not symbol_map.is_absolute():
            symbol_map = config.parent / symbol_map
        symbol_map = symbol_map.resolve()
        if not symbol_map.is_file():
            raise Missing(f"symbol map not found: {symbol_map} -> ./tz disc")
        map_rows, map_entry = symbol_map_info(symbol_map)
        elf_head = elf.open("rb").read(28)
        if len(elf_head) < 28 or elf_head[:6] != b"\x7fELF\x01\x01":
            fail("expected a little-endian ELF32 input")
        elf_entry = struct.unpack_from("<I", elf_head, 24)[0]
        if map_entry != elf_entry:
            fail(f"symbol map first entry 0x{map_entry:x} != ELF e_entry 0x{elf_entry:x}")
        canonical_output = args.out if args.out else Path(output_value)
        if not canonical_output.is_absolute():
            canonical_output = (config.parent if not args.out else Path.cwd()) / canonical_output
        canonical_output = canonical_output.resolve()
        if not (inside(canonical_output, ROOT) or args.out):
            fail(f"refusing output directory outside project: {canonical_output}")
        report.update(output=str(canonical_output), elf_sha256=sha256_file(elf),
                      config_sha256=sha256_file(config), compiler_sha256=sha256_file(compiler))
        if args.dry_run:
            report["status"] = "dry_run_validated"
            print("dry run OK: inputs valid")
            return 0

        lock = ROOT / "build/regenerate-game.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            fail(f"another regeneration appears active (lock exists): {lock}")
        os.write(lock_fd, f"pid={os.getpid()}\n".encode())
        os.close(lock_fd)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="tarzan-game-regen-") as temp_name:
                temp_root = Path(temp_name)
                stage = temp_root / "generated"
                stage.mkdir()
                staged_config = temp_root / "config.toml"
                staged_config.write_text(replace_output(original_text, stage), encoding="utf-8")
                with log_path.open("w", encoding="utf-8") as log:
                    result = subprocess.run([str(compiler), str(staged_config)], cwd=ROOT,
                                            stdout=log, stderr=subprocess.STDOUT, check=False)
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
                if result.returncode != 0:
                    fail(f"compiler exited {result.returncode}; see {log_path}")
                if "Recompilation completed successfully" not in log_text:
                    fail(f"compiler log lacks completion marker; see {log_path}")
                if re.search(r"\[error\]|UnknownMMI|Unhandled (?:instruction(?!s: \d)|opcode|MMI|SPECIAL|COP2)",
                             log_text, flags=re.IGNORECASE):
                    fail(f"compiler log contains an error or UnknownMMI diagnostic; see {log_path}")
                summary = parse_summary(log_text)
                bad = {k: summary[k] for k in ("skipped", "decode_failures", "unhandled", "errors") if summary[k] != 0}
                if summary["processed"] != summary["recompiled"] + summary["stubs"]:
                    bad["processed_partition"] = summary["processed"]
                if summary["processed"] != map_rows or summary["discovered"] != map_rows:
                    bad["symbol_map_row_count"] = summary["processed"]
                if bad:
                    fail(f"compiler quality summary is not clean: {bad}; see {log_path}")
                apply_experiments(stage, elf)
                staged = regular_files(stage)
                if not staged:
                    fail("compiler succeeded but staged no output files")
                register_name, startup_name = startup_artifact_names(stage, elf_entry)
                if register_name not in staged or Path(startup_name).stem not in staged[register_name].read_text(encoding="utf-8", errors="replace"):
                    fail(f"staged output lacks required registration/startup artifacts ({register_name}, {startup_name})")

                mapped_bytes, exec_bytes = mapped_executable_bytes(elf, symbol_map)
                inventory = {"elf_sha256": report["elf_sha256"], "mapped": map_rows,
                             "translated": summary["recompiled"], "hle_bound_stubs": summary["stubs"],
                             "stub_declarations": stub_count(stage), "skipped": summary["skipped"],
                             "decode_failures": summary["decode_failures"],
                             "mapped_executable_bytes": mapped_bytes, "executable_section_bytes": exec_bytes}
                report["inventory"] = inventory
                previous = None
                if INVENTORY.is_file():
                    try:
                        previous = json.loads(INVENTORY.read_text())
                    except ValueError:
                        previous = None
                if previous and previous.get("elf_sha256") == inventory["elf_sha256"]:
                    if inventory["translated"] < previous["translated"]:
                        fail(f"translated count dropped {previous['translated']} -> {inventory['translated']} "
                             f"(delete {INVENTORY} only if intentional)")
                    if inventory["skipped"] > previous.get("skipped", 0):
                        fail(f"skipped functions appeared: {inventory['skipped']}")

                existing = regular_files(canonical_output) if canonical_output.exists() else {}
                changed = [n for n, p in staged.items()
                           if n not in existing or p.read_bytes() != existing[n].read_bytes()]
                report["changed_files"] = sorted(changed)
                report["changed_count"] = len(changed)
                report["stale_existing_files_retained"] = sorted(set(existing) - set(staged))
                # Prepare every replacement beside its target, then rename them all.
                prepared = []
                for name in changed:
                    target = canonical_output / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, temp_path = tempfile.mkstemp(prefix=target.name + ".regen-", dir=target.parent)
                    os.close(fd)
                    shutil.copyfile(staged[name], temp_path)
                    os.chmod(temp_path, stat.S_IMODE(staged[name].stat().st_mode))
                    prepared.append((Path(temp_path), target))
                for temp_path, target in prepared:
                    os.replace(temp_path, target)
                if not args.out:
                    write_json(INVENTORY, inventory)
                report["status"] = "success"
                write_json(args.report.resolve(), report)
                print(f"generated: mapped {map_rows}, translated {summary['recompiled']}, HLE-bound stubs {summary['stubs']}, "
                      f"skipped {summary['skipped']}, errors {summary['errors']}; "
                      f"mapped exec bytes {100 * mapped_bytes / exec_bytes:.1f}%; {len(changed)} files changed")
                return 0
        finally:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
    except Missing as exc:
        print(exc, file=sys.stderr)
        return 2
    except (RuntimeError, PatchError, OSError, subprocess.CalledProcessError) as exc:
        report["error"] = str(exc)
        try:
            write_json(args.report.resolve(), report)
        except Exception as report_exc:
            print(f"could not write report: {report_exc}", file=sys.stderr)
        print(f"generate failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Apply the guarded Tarzan generated-code experiments to generated functions.

This is a BYOD post-generation step. It verifies the supplied SLES-50350 ELF
before editing and only writes its named generated functions.
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
    { const float divFs = ctx->f[21]; const float divFt = ctx->f[20]; uint32_t divFsBits; uint32_t divFtBits; std::memcpy(&divFsBits, &divFs, sizeof(divFsBits)); std::memcpy(&divFtBits, &divFt, sizeof(divFtBits)); if ((divFtBits & 0x7f800000u) == 0) { ctx->fcr31 |= ((divFsBits & 0x7f800000u) == 0) ? 0x00020040u : 0x00010020u; const uint32_t divResultBits = ((divFsBits ^ divFtBits) & 0x80000000u) | 0x7f7fffffu; std::memcpy(&ctx->f[1], &divResultBits, sizeof(divResultBits)); } else ctx->f[1] = divFs / divFt; }"""
        new = """    // 0x369a7c: 0x4614a843  div.s       $f1, $f21, $f20
    ctx->pc = 0x369a7cu;
    // TARZAN_EXPERIMENT_HD_FOV: upstream PAL-M Y-FOV stub, opt-in.
    if (tarzan_experiments_hd_widescreen()) {
        { const float divFs = ctx->f[21]; const float divFt = ctx->f[20]; uint32_t divFsBits; uint32_t divFtBits; std::memcpy(&divFsBits, &divFs, sizeof(divFsBits)); std::memcpy(&divFtBits, &divFt, sizeof(divFtBits)); if ((divFtBits & 0x7f800000u) == 0) { ctx->fcr31 |= ((divFsBits & 0x7f800000u) == 0) ? 0x00020040u : 0x00010020u; const uint32_t divResultBits = ((divFsBits ^ divFtBits) & 0x80000000u) | 0x7f7fffffu; std::memcpy(&ctx->f[1], &divResultBits, sizeof(divResultBits)); } else ctx->f[1] = divFs / divFt; }
        SET_GPR_U32(ctx, 1, 0x3f400000u);
        { const uint32_t bits = GPR_U32(ctx, 1); std::memcpy(&ctx->f[30], &bits, sizeof(bits)); }
        ctx->f[1] = FPU_MUL_S(ctx->f[1], ctx->f[30]);
    } else {
        { const float divFs = ctx->f[21]; const float divFt = ctx->f[20]; uint32_t divFsBits; uint32_t divFtBits; std::memcpy(&divFsBits, &divFs, sizeof(divFsBits)); std::memcpy(&divFtBits, &divFt, sizeof(divFtBits)); if ((divFtBits & 0x7f800000u) == 0) { ctx->fcr31 |= ((divFsBits & 0x7f800000u) == 0) ? 0x00020040u : 0x00010020u; const uint32_t divResultBits = ((divFsBits ^ divFtBits) & 0x80000000u) | 0x7f7fffffu; std::memcpy(&ctx->f[1], &divResultBits, sizeof(divResultBits)); } else ctx->f[1] = divFs / divFt; }
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

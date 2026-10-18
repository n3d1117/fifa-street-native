// SPDX-License-Identifier: GPL-3.0-or-later
// Tarzan game hooks: host code the recompiler calls at configured instruction
// addresses (config/SLES-50350.template.toml, [[hooks]]). Each hook is gated by
// the experimental settings and does nothing when its setting is off, so the
// recompiled guest code runs unmodified. Hooks are installed only when every
// guest instruction word they depend on matches the verified retail ELF.
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "game_overrides.h"
#include "experimental-settings.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sys/stat.h>

namespace {

// Hook ABI: (rdram, ctx, runtime). Registers follow the MIPS names used by the
// original text patches: $at=1 $v0=2 $a0=4 $s0=16 $s1=17 $s2=18 $sp=29 $ra=31.

// Camera: PAL-M zoom constant, applied after `lui $v0, 0x3F00` at 0x369a3c.
uint32_t cameraZoom(uint8_t *rdram, R5900Context *ctx, PS2Runtime *) {
    if (tarzan_experiments_widescreen_camera(rdram, GPR_U32(ctx, 29)))
        SET_GPR_U32(ctx, 2, 0x3f210000u);
    return 0;
}

// Camera: PAL-M Y-FOV factor, applied after `div.s $f1, $f21, $f20` at 0x369a7c.
uint32_t cameraFov(uint8_t *rdram, R5900Context *ctx, PS2Runtime *) {
    if (!tarzan_experiments_widescreen_camera(rdram, GPR_U32(ctx, 29))) return 0;
    SET_GPR_U32(ctx, 1, 0x3f400000u);
    { const uint32_t bits = GPR_U32(ctx, 1); std::memcpy(&ctx->f[30], &bits, sizeof(bits)); }
    ctx->f[1] = FPU_MUL_S(ctx->f[1], ctx->f[30]);
    return 0;
}

// Diagnostic: camera/dialog tuple sampling at CAM_vAdjustCameraToViewport entry.
uint32_t cameraTrace(uint8_t *rdram, R5900Context *ctx, PS2Runtime *) {
    if (GPR_U32(ctx, 31) == 0x338BE0u) {
        const uint32_t worldDialog = GPR_U32(ctx, 17);
        uint32_t dialogId = 0, worldObject = 0;
        if (worldDialog <= 0x01FFFF08u) {
            std::memcpy(&dialogId, rdram + worldDialog + 4u, 4);
            std::memcpy(&worldObject, rdram + worldDialog + 0xF4u, 4);
        }
        tarzan_experiments_trace_camera_context(GPR_U32(ctx, 31), GPR_U32(ctx, 4),
                                                worldDialog, dialogId, worldObject);
    } else {
        tarzan_experiments_trace_camera_context(GPR_U32(ctx, 31), GPR_U32(ctx, 4), 0, 0, 0);
    }
    return 0;
}

// FPS gate init: after `sb $v0, 0xB8($at)` stores the min-sync byte at 0x6B00B8.
uint32_t fpsInit(uint8_t *rdram, R5900Context *ctx, PS2Runtime *) {
    const char *legacy = std::getenv("TARZAN_EXPERIMENT_50FPS");
    const bool enhanced = tarzan_experiments_high_fps() != 0 ||
                          (legacy && legacy[0] == '1' && legacy[1] == '\0');
    const uint8_t original = static_cast<uint8_t>(GPR_U32(ctx, 2));
    const uint8_t value = enhanced ? 0u : original;
    if (enhanced) {
        ps2TraceGuestWrite(rdram, 0x6B00B8u, 1u, value, 0u, "WRITE8", ctx);
        FAST_WRITE8(0x6B00B8u, value);
    }
    std::fprintf(stderr, "[tarzan-frame-mode] originalMinSync=%u effectiveMinSync=%u experimental50=%u\n",
                 original, value, enhanced ? 1u : 0u);
    return 0;
}

// FPS gate sync: after `lbu $v0, 0xB8($at)` in osrSyncCallback at 0x3f8df4.
uint32_t fpsSync(uint8_t *, R5900Context *ctx, PS2Runtime *) {
    static const char *marker = std::getenv("TARZAN_EXPERIMENT_50FPS_FILE");
    bool zero = tarzan_experiments_high_fps() != 0;
    if (marker && *marker) {
        struct stat info{};
        if (stat(marker, &info) == 0) zero = true;
    }
    if (zero) SET_GPR_ZE32(ctx, 2, 0u);
    return 0;
}

// Clock trace: guest clock pair after its original store at 0x360388.
uint32_t clockTrace(uint8_t *, R5900Context *ctx, PS2Runtime *) {
    tarzan_experiments_trace_clock(GPR_U64(ctx, 4), GPR_U64(ctx, 16));
    return 0;
}

// Language: select the saved item before CMenu::Start focuses its startup item.
uint32_t languageStart(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime) {
    const uint32_t original = READ32(ADD32(GPR_U32(ctx, 16), 0xA4u));
    const uint32_t chosen = tarzan_experiments_boot_language_item(rdram, GPR_U32(ctx, 16), original);
    if (chosen != original) WRITE32(ADD32(GPR_U32(ctx, 16), 0xA4u), chosen);
    return 0;
}

// Language: advance saved-language startup state once per CMenu::Process entry.
uint32_t languageMenu(uint8_t *rdram, R5900Context *ctx, PS2Runtime *) {
    tarzan_experiments_boot_language_menu(rdram, GPR_U32(ctx, 4));
    return 0;
}

// Language: request handler returned success (0x2cd48c) / language change accepted (0x2c0e40).
uint32_t languageAccepted(uint8_t *, R5900Context *ctx, PS2Runtime *) {
    tarzan_experiments_language_accepted(GPR_U32(ctx, 16));
    return 0;
}

// Cheats: the guest matched its cheat menu entry; inject the original code, then
// continue at 0x2dada4 (redirect) exactly as if the player had typed it.
uint32_t cheats(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime) {
    const uint32_t owner = GPR_U32(ctx, 18);
    const int cheat = tarzan_experiments_builtin_cheat(rdram, owner);
    if (!cheat) return 0;
    const char *sequence = cheat == 1 ? "RLUDDURLLRDUUDLR" : "LURDDLURRDLUURDL";
    for (uint32_t i = 0; i < 16u; ++i) WRITE8(ADD32(owner, 0x98u + i), sequence[i]);
    WRITE8(ADD32(owner, 0xA8u), 16u);
    WRITE8(ADD32(owner, 0xA9u), 1u);
    ctx->pc = 0x2DADA4u;
    return 1;
}

// Logo skip: at the startup `jal 0x363AB0` (0x35e2c4). When skipping, do what the
// call site would have done around the call (delay slot, $ra) and continue at 0x35e2cc.
uint32_t skipLogo(uint8_t *rdram, R5900Context *ctx, PS2Runtime *) {
    if (!tarzan_experiments_skip_startup_logo(rdram, GPR_U32(ctx, 4))) return 0;
    ctx->r[8] = _mm_setzero_si128();      // delay slot: paddub $t0, $zero, $zero
    SET_GPR_U32(ctx, 31, 0x35E2CCu);
    ctx->pc = 0x35E2CCu;
    return 1;
}

struct Hook {
    uint32_t address;
    bool after;
    PS2Runtime::InstructionHookFn fn;
};

const Hook kHooks[] = {
    {0x3699C0u, false, cameraTrace}, {0x369A3Cu, true, cameraZoom}, {0x369A7Cu, true, cameraFov},
    {0x35EC74u, true, fpsInit},      {0x3F8DF4u, true, fpsSync},    {0x360388u, true, clockTrace},
    {0x34FCE0u, false, languageStart}, {0x350350u, false, languageMenu},
    {0x2CD48Cu, false, languageAccepted}, {0x2C0E40u, false, languageAccepted},
    {0x2DAC2Cu, false, cheats},      {0x35E2C4u, false, skipLogo},
};

struct Word {
    uint32_t address;
    uint32_t value;
};

// Guest instruction words (and the two cheat-code tables) the hooks depend on.
const Word kExpected[] = {
    {0x369A3C, 0x3C023F00}, {0x369A7C, 0x4614A843}, {0x3699C0, 0x27BDFFB0},
    {0x3699C4, 0x7FBF0030}, {0x3699C8, 0x7FB10020}, {0x338BD8, 0x0C0DA670},
    {0x33B324, 0xAC850004}, {0x360388, 0xFC241A10}, {0x35EC74, 0xA02200B8},
    {0x3F8DF4, 0x902200B8}, {0x2DAC2C, 0x3C01004D}, {0x2DADA4, 0x924300A8},
    {0x2DADD4, 0xA02285F8}, {0x2DAE60, 0x24050063}, {0x2DAE64, 0x0C0B804C},
    {0x2DAE68, 0x24060001}, {0x35E2B0, 0x3C02004B}, {0x35E2B4, 0x24446460},
    {0x35E2C4, 0x0C0D8EAC}, {0x35E2C8, 0x70004628}, {0x4B6460, 0x6F676F4C},
    {0x34FCE0, 0x0C0D5B88}, {0x350350, 0x27BDFFB0}, {0x2CD48C, 0x7BBF0010},
    {0x2C0E40, 0x24020001},
};

// The guest's two cheat-code strings (what the cheats hook injects).
const struct { uint32_t address; const char *text; } kCheatStrings[] = {
    {0x4B3210, "LURDDLURRDLUURDL"},
    {0x4B3230, "RLUDDURLLRDUUDLR"},
};

bool readWord(const uint8_t *rdram, uint32_t address, uint32_t &value) {
    if (address > 0x02000000u - 4u) return false;
    std::memcpy(&value, rdram + address, sizeof(value));
    return true;
}

bool mismatch(const uint8_t *rdram, uint32_t address, uint32_t expected) {
    uint32_t actual = 0;
    if (readWord(rdram, address, actual) && actual == expected) return false;
    std::fprintf(stderr,
                 "[game-hooks] unsupported ELF variant at 0x%x: expected 0x%08x, found 0x%08x; game hooks not installed\n",
                 address, expected, actual);
    return true;
}

void install(PS2Runtime &runtime) {
    const uint8_t *rdram = runtime.memory().getRDRAM();
    for (const Word &word : kExpected)
        if (mismatch(rdram, word.address, word.value)) return;
    for (const auto &cheat : kCheatStrings)
        for (uint32_t offset = 0; offset < 16u; offset += 4u) {
            uint32_t value;
            std::memcpy(&value, cheat.text + offset, sizeof(value));
            if (mismatch(rdram, cheat.address + offset, value)) return;
        }
    unsigned installed = 0;
    for (const Hook &hook : kHooks) {
        if (runtime.registerInstructionHook(hook.address, hook.after, hook.fn)) ++installed;
        else std::fprintf(stderr, "[game-hooks] no generated hook site at 0x%x (%s)\n",
                          hook.address, hook.after ? "after" : "before");
    }
    std::fprintf(stderr, "[game-hooks] installed %u/%zu hooks\n", installed, sizeof(kHooks) / sizeof(kHooks[0]));
}

}  // namespace

PS2_REGISTER_GAME_OVERRIDE("tarzan-game-hooks", "SLES_503.50", 0, 0, install)

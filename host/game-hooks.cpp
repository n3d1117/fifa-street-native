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

#include <atomic>
#include <cstdio>
#include <cstring>

namespace {

// Hook ABI: (rdram, ctx, runtime). Registers follow the MIPS names used by the
// original text patches: $at=1 $v0=2 $a0=4 $s0=16 $s1=17 $s2=18 $sp=29 $ra=31.

void missionMenuTick(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime);

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

// FPS gate init: after `sb $v0, 0xB8($at)` stores the min-sync byte at 0x6B00B8.
uint32_t fpsInit(uint8_t *rdram, R5900Context *ctx, PS2Runtime *) {
    const bool enhanced = tarzan_experiments_high_fps() != 0;
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
    if (tarzan_experiments_high_fps() != 0) SET_GPR_ZE32(ctx, 2, 0u);
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
uint32_t languageMenu(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime) {
    tarzan_experiments_boot_language_menu(rdram, GPR_U32(ctx, 4));
    missionMenuTick(rdram, ctx, runtime);
    return 0;
}

// Boot mission (diagnostic, TARZAN_BOOT_MISSION): after the menu level is up and the language is settled,
// call the game's own mission start, CLevelManager::SetCurrentLevel(this, level, mode, player), exactly as the
// map's ChangeMap handler does after its lock check and story movie. Saves are never touched.
constexpr uint32_t kSetCurrentLevel = 0x2DBFB0u;
constexpr uint32_t kLevelManagerInstance = 0x281E00u;
constexpr uint32_t kLevelNameBuffer = 0x4D19AEu;
constexpr uint32_t kMenuTicksBeforeStart = 25u;
std::atomic<bool> g_languageDone{false};
std::atomic<uint32_t> g_menuTicks{0}, g_levelLoads{0};
std::atomic<bool> g_missionRequested{false};

uint32_t callGuest(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime, uint32_t entry,
                   uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3) {
    PS2Runtime::RecompiledFunction fn = runtime->lookupFunction(entry);
    if (!fn) return 0;
    const R5900Context saved = *ctx;
    SET_GPR_U32(ctx, 29, (GPR_U32(ctx, 29) - 0x100u) & ~0xFu);
    SET_GPR_U32(ctx, 31, saved.pc);
    SET_GPR_U32(ctx, 4, a0);
    SET_GPR_U32(ctx, 5, a1);
    SET_GPR_U32(ctx, 6, a2);
    SET_GPR_U32(ctx, 7, a3);
    ctx->in_delay_slot = false;
    ctx->pc = entry;
    fn(rdram, ctx, runtime);
    const uint32_t result = GPR_U32(ctx, 2);
    *ctx = saved;
    return result;
}

void missionMenuTick(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime) {
    int mode = 0, player = 0;
    const int level = tarzan_experiments_boot_mission(&mode, &player);
    if (level < 0 || g_missionRequested.load() || !g_languageDone.load()) return;
    if (g_menuTicks.fetch_add(1) + 1 < kMenuTicksBeforeStart) return;
    g_missionRequested.store(true);
    const uint32_t manager = callGuest(rdram, ctx, runtime, kLevelManagerInstance, 0, 0, 0, 0);
    std::fprintf(stderr, "[tarzan-mission] start level=%d mode=%d player=%d manager=0x%x inLevelMode=%u loads=%u\n", level, mode, player, manager, (unsigned)READ8(0x4D1748u), g_levelLoads.load());
    if (!manager) return;
    callGuest(rdram, ctx, runtime, kSetCurrentLevel, manager, static_cast<uint32_t>(level),
              static_cast<uint32_t>(mode), static_cast<uint32_t>(player));
}

bool missionActive() {
    int mode, player;
    return tarzan_experiments_boot_mission(&mode, &player) >= 0;
}

uint32_t missionLevelRequested(uint8_t *, R5900Context *ctx, PS2Runtime *) {
    if (!missionActive()) return 0;
    std::fprintf(stderr, "[tarzan-mission] SetCurrentLevel a1=0x%x a2=0x%x a3=0x%x\n",
                 GPR_U32(ctx, 5), GPR_U32(ctx, 6), GPR_U32(ctx, 7));
    return 0;
}

uint32_t missionLevelLoaded(uint8_t *rdram, R5900Context *, PS2Runtime *) {
    const uint32_t count = g_levelLoads.fetch_add(1) + 1;
    if (!missionActive()) return 0;
    char name[32] = {};
    std::memcpy(name, rdram + kLevelNameBuffer, sizeof(name) - 1);
    std::fprintf(stderr, "[tarzan-mission] level-loaded n=%u name=%.31s\n", count, name);
    return 0;
}

// Language: request handler returned success (0x2cd48c) / language change accepted (0x2c0e40).
uint32_t languageAccepted(uint8_t *, R5900Context *ctx, PS2Runtime *) {
    tarzan_experiments_language_accepted(GPR_U32(ctx, 16));
    g_languageDone.store(true);
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
    {0x369A3Cu, true, cameraZoom}, {0x369A7Cu, true, cameraFov},
    {0x35EC74u, true, fpsInit},      {0x3F8DF4u, true, fpsSync},    {0x360388u, true, clockTrace},
    {0x34FCE0u, false, languageStart}, {0x350350u, false, languageMenu},
    {0x2CD48Cu, false, languageAccepted}, {0x2C0E40u, false, languageAccepted},
    {0x2DAC2Cu, false, cheats},      {0x2DBFB0u, false, missionLevelRequested}, {0x345590u, false, missionLevelLoaded},      {0x35E2C4u, false, skipLogo},
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
    {0x2C0E40, 0x24020001}, {0x2DBFB0, 0x27BDFF30}, {0x281E00, 0x27BDFFD0}, {0x345590, 0x27BDFFD0}, {0x2DB810, 0x27BDFFC0}, {0x2C3E90, 0x27BDFED0},
    {0x2C41DC, 0x0C0B6E04}, {0x2C43A4, 0x0C0B6E04}, {0x2C441C, 0x0C0B6E04},
    {0x2C448C, 0x0C0B6E04}, {0x2C4634, 0x0C0B6E04}, {0x2C46CC, 0x0C0B6E04},
};

// The guest's two cheat-code strings (what the cheats hook injects).
const struct { uint32_t address; const char *text; } kCheatStrings[] = {
    {0x4B3210, "LURDDLURRDLUURDL"},
    {0x4B3230, "RLUDDURLLRDUUDLR"},
};

// Unlock all missions: CItemsInfos::IsItemActive (0x2db810) reports an item as
// done. The map lock handler (ProcessMenuMessage_Locking, 0x2c3e90) calls it from
// the six sites below to decide which map entries are shown and selectable. Only
// those calls report true; every other caller (saving, completion, game flow) and
// all stored progress are untouched.
constexpr uint32_t kIsItemActive = 0x2DB810u;
constexpr uint32_t kLockingReturns[] = {0x2C41E4u, 0x2C43ACu, 0x2C4424u, 0x2C4494u, 0x2C463Cu, 0x2C46D4u};
PS2Runtime::RecompiledFunction g_isItemActive = nullptr;

void isItemActiveUnlocked(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime) {
    const uint32_t ra = GPR_U32(ctx, 31);
    const uint32_t found = GPR_U32(ctx, 8);
    g_isItemActive(rdram, ctx, runtime);
    for (const uint32_t site : kLockingReturns)
        if (ra == site) {
            SET_GPR_U32(ctx, 2, 1u);
            WRITE8(found, 0u);
            return;
        }
}

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
    if (tarzan_experiments_unlock_all_missions()) {
        g_isItemActive = runtime.lookupFunction(kIsItemActive);
        if (g_isItemActive && runtime.replaceFunction(kIsItemActive, isItemActiveUnlocked))
            std::fprintf(stderr, "[game-hooks] unlock-all-missions active\n");
        else
            std::fprintf(stderr, "[game-hooks] unlock-all-missions: no IsItemActive function\n");
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

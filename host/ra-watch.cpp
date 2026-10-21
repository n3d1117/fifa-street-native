// SPDX-License-Identifier: GPL-3.0-or-later
#include "elf-words.hpp"
#include "ps2_runtime.h"
#include "runtime/ee_scheduler.h"

#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>

namespace {
struct Frame {
    uint32_t sp = 0;
    uint64_t ra = 0;
    int thread = 0;
    bool active = false, reported = false;
};
thread_local std::array<Frame, 32> frames{};
thread_local uint32_t activeCount = 0;

void report(Frame &frame, const char *reason, const R5900Context *ctx, uint64_t actual,
            uint32_t address = 0, uint32_t size = 0) {
    if (frame.reported) return;
    frame.reported = true;
    std::fprintf(stderr, "[ra-stomp] reason=%s thread=%d pc=0x%x branch=0x%x delay=%u slot=0x%x entry_sp=0x%x expected_sp=0x%x sp=0x%x expected_ra=0x%llx slot_ra=0x%llx ra=0x%x write_addr=0x%x write_size=%u a0=0x%x a1=0x%x a2=0x%x s0=0x%x s1=0x%x s2=0x%x\n",
                 reason, frame.thread, ctx->pc, ctx->branch_pc, static_cast<unsigned>(ctx->in_delay_slot),
                 frame.sp, frame.sp + 0x40u, frame.sp, getRegU32(ctx, 29),
                 static_cast<unsigned long long>(frame.ra), static_cast<unsigned long long>(actual),
                 getRegU32(ctx, 31), address, size, getRegU32(ctx, 4), getRegU32(ctx, 5),
                 getRegU32(ctx, 6), getRegU32(ctx, 16), getRegU32(ctx, 17), getRegU32(ctx, 18));
}

void observeWrite(uint8_t *rdram, uint32_t address, uint32_t size, uint64_t lo, uint64_t hi,
                  const char *op, const R5900Context *ctx) {
    if (activeCount == 0) return;
    const uint32_t physical = address & 0x1fffffffu;
    const std::array<uint64_t, 2> value{lo, hi};
    for (Frame &frame : frames) {
        if (!frame.active || frame.reported || physical >= frame.sp + 8u ||
            static_cast<uint64_t>(physical) + size <= frame.sp) continue;
        uint64_t after;
        std::memcpy(&after, rdram + frame.sp, 8);
        for (uint32_t i = 0; i < 8u; ++i) {
            const uint32_t at = frame.sp + i;
            if (at >= physical && at - physical < size && at - physical < sizeof(value))
                reinterpret_cast<uint8_t *>(&after)[i] = reinterpret_cast<const uint8_t *>(value.data())[at - physical];
        }
        if (after != frame.ra) report(frame, op, ctx, after, address, size);
    }
}

uint32_t watch(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime) {
    const int thread = runtime->eeScheduler().currentThreadId();
    if (ctx->pc == 0x1e83c4u) {
        const uint32_t sp = getRegU32(ctx, 29);
        if (sp > PS2_RAM_SIZE - 8u) return 0;
        for (Frame &frame : frames) {
            if (frame.active) continue;
            ++activeCount;
            frame = {sp, static_cast<uint64_t>(_mm_extract_epi64(ctx->r[31], 0)), thread, true, false};
            uint64_t actual;
            std::memcpy(&actual, rdram + sp, 8);
            if (actual != frame.ra) report(frame, "entry-save", ctx, actual);
            return 0;
        }
        std::fprintf(stderr, "[ra-stomp] watch capacity exceeded thread=%d pc=0x%x\n", thread, ctx->pc);
        return 0;
    }
    // The newest frame belongs to the innermost monitored call on this EE thread.
    Frame *found = nullptr;
    for (Frame &frame : frames)
        if (frame.active && frame.thread == thread && (!found || frame.sp < found->sp)) found = &frame;
    if (!found) return 0;
    uint64_t actual;
    std::memcpy(&actual, rdram + found->sp, 8);
    if (actual != found->ra || getRegU32(ctx, 29) != found->sp ||
        (ctx->pc == 0x1e83fcu && getRegU32(ctx, 31) != static_cast<uint32_t>(found->ra))) {
        const char *reason = ctx->pc == 0x1e83ccu ? "return-186238" :
                             ctx->pc == 0x1e83e4u ? "return-1e8410" : "epilogue";
        report(*found, reason, ctx, actual);
        runtime->eeScheduler().dumpContextHistory();
    }
    if (ctx->pc == 0x1e83fcu) {
        found->active = false;
        --activeCount;
    }
    return 0;
}
}

bool ps2x_ra_watch_install(PS2Runtime &runtime, const std::filesystem::path &elfPath) {
    const char *enabled = std::getenv("PS2X_WATCH_RA_STOMP");
    if (!enabled || std::strcmp(enabled, "1") != 0) return false;
    std::ifstream file(elfPath, std::ios::binary);
    const std::vector<uint8_t> elf((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    constexpr std::array<std::pair<uint32_t, uint32_t>, 9> words{{
        {0x1e83a8u, 0x27bdffc0u}, {0x1e83c0u, 0xffbf0000u}, {0x1e83c4u, 0x0c06188eu},
        {0x1e83ccu, 0x0040802du}, {0x1e83dcu, 0x0c07a104u}, {0x1e83e4u, 0xae420000u},
        {0x1e83f8u, 0xdfbf0000u}, {0x1e83fcu, 0x03e00008u}, {0x1e8400u, 0x27bd0040u}
    }};
    for (const auto &[address, expected] : words) {
        uint32_t actual = 0;
        if (!host::elfWord(elf, address, actual) || actual != expected) {
            std::fprintf(stderr, "[ra-stomp] refused: retail word differs at 0x%x\n", address);
            return false;
        }
    }
    for (uint32_t address : {0x1e83c4u, 0x1e83ccu, 0x1e83e4u, 0x1e83fcu}) {
        if (!runtime.registerInstructionHook(address, false, &watch)) {
            std::fprintf(stderr, "[ra-stomp] refused: missing hook at 0x%x\n", address);
            return false;
        }
    }
    g_ps2GuestWriteObserver = &observeWrite;
    std::fprintf(stderr, "[ra-stomp] armed: sub_001E83A8 saved RA and callee SP checks\n");
    return true;
}

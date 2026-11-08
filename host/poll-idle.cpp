// SPDX-License-Identifier: GPL-3.0-or-later
// Sleep through the guest's vblank poll (sub_003C18D0).
// The hook at 0x3C1994 runs before the back-edge `bnel` once the poll word (s1+0xBC) is loaded.
// While it is non-zero and no callback is registered the loop only re-reads RAM, so instead of
// spinning the EE waits for the earliest scheduler event, EE timer or IOP/SPU2 deadline
// (EeScheduler::idlePoll), then re-enters the loop exactly where the taken back-edge would.
// The VBlank-start interrupt that clears the word is such a deadline. Installed only when every
// guest word it depends on matches the retail ELF; the guest code itself is never changed.
#include "elf-words.hpp"
#include "poll-idle.hpp"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "runtime/ee_scheduler.h"
#include "runtime/ps2_host_arena.h"

#include <cstdio>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <vector>

namespace {

// Registers and pc as the taken back-edge leaves them: the delay slot loaded the callback into
// $v0 and the loop continues at kLoop. Restored unless idlePoll leaves for the dispatcher.
struct BackEdge {
    R5900Context *ctx;
    uint64_t v0;
    uint32_t pc, branchPc;
    int uncaught = std::uncaught_exceptions();
    BackEdge(R5900Context *c, uint32_t callback) : ctx(c), v0(GPR_U64(c, 2)), pc(c->pc), branchPc(c->branch_pc) {
        SET_GPR_S32(ctx, 2, static_cast<int32_t>(callback));
        ctx->pc = host::pollidle::kLoop;
        ctx->branch_pc = host::pollidle::kHook;
    }
    ~BackEdge() {
        if (std::uncaught_exceptions() != uncaught) return;
        SET_GPR_U64(ctx, 2, v0);
        ctx->pc = pc;
        ctx->branch_pc = branchPc;
    }
};

uint32_t pollIdle(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime) {
    using namespace host::pollidle;
    const uint32_t s1 = GPR_U32(ctx, 17);
    const uint32_t pollWord = FAST_READ32(s1 + kPollOffset);
    const uint32_t callback = FAST_READ32(s1 + kCallbackOffset);
    if (!shouldSleep(ps2HostConfig().wallClock, pollWord, callback)) return 0;
    BackEdge backEdge(ctx, callback);
    runtime->eeScheduler().idlePoll();
    return 0;
}

}  // namespace

// Returns true when the hook is installed.
bool ps2x_poll_idle_install(PS2Runtime &runtime, const std::filesystem::path &elfPath) {
    std::ifstream file(elfPath, std::ios::binary);
    const std::vector<uint8_t> elf((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    std::string why;
    if (!host::pollidle::verify([&](uint32_t a, uint32_t &v) { return host::elfWord(elf, a, v); }, &why)) {
        std::fprintf(stderr, "[poll-idle] refused: %s\n", why.c_str());
        return false;
    }
    if (!runtime.registerInstructionHook(host::pollidle::kHook, false, &pollIdle)) {
        std::fprintf(stderr, "[poll-idle] refused: no generated hook at 0x%x\n", host::pollidle::kHook);
        return false;
    }
    return true;
}

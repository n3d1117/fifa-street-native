#include "runtime/ps2_vu0_recomp.h"
#include "runtime/ps2_memory.h"
#include <array>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace {
auto& registry() { static std::vector<Vu0RecImage> images; return images; }
bool enabled(const char* name) { const char* p = std::getenv(name); return p && p[0] == '1' && p[1] == 0; }
uint64_t hashCode(const uint8_t* code) {
    uint64_t h = 1469598103934665603ull;
    for (uint32_t i = 0; i < PS2_VU0_CODE_SIZE; ++i) { h ^= code[i]; h *= 1099511628211ull; }
    return h;
}
void prepare(VU1State& state, uint32_t pc, uint32_t itop) {
    state.pc = pc & (PS2_VU0_CODE_SIZE - 1u);
    state.ebit = state.haltAfterDelaySlot = false;
    state.stoppedByD = state.stoppedByT = false;
    state.top = 0; state.itop = itop;
    state.branchPending = false; state.branchTarget = state.branchDelay = 0;
    state.vf[0][0] = state.vf[0][1] = state.vf[0][2] = 0;
    state.vf[0][3] = 1;
}
}
void vu0recRegister(const Vu0RecImage& image) { registry().push_back(image); }
const Vu0RecImage* vu0recFind(uint64_t hash) {
    for (const auto& image : registry()) if (image.hash == hash) return &image;
    return nullptr;
}
bool vu0recExecute(VU1Interpreter& vu, uint8_t* code, uint8_t* data,
                   GS& gs, PS2Memory& memory, uint32_t pc, uint32_t itop) {
    static const bool forced = enabled("PS2X_VU0_INTERPRETER");
    static const bool diff = enabled("PS2X_VU0_DIFF");
    static const bool commitDiff = enabled("PS2X_VU0_DIFF_COMMIT");
    if (forced || registry().empty()) return false;
    // Compare actual bytes, including untracked direct MicroMem writes. Hash
    // only on a change; no pointer/generation shortcut can accept stale code.
    struct Cache { std::array<uint8_t, PS2_VU0_CODE_SIZE> code; bool valid = false; uint64_t hash = 0; };
    static thread_local Cache cache;
    if (!cache.valid || std::memcmp(cache.code.data(), code, cache.code.size()) != 0) {
        std::memcpy(cache.code.data(), code, cache.code.size());
        cache.hash = hashCode(code); cache.valid = true;
        static thread_local unsigned imageTraces = 0;
        if (imageTraces++ < 16)
            std::fprintf(stderr, "[vu0rec] image=%016llx native=%u\n",
                (unsigned long long)cache.hash, vu0recFind(cache.hash) != nullptr);
    }
    const Vu0RecImage* image = vu0recFind(cache.hash);
    if (!image || !image->full || std::find(image->entries, image->entries + image->entryCount, pc) == image->entries + image->entryCount)
        return false;
    VU1State candidate = vu.state();
    prepare(candidate, pc, itop);
    std::array<uint8_t, PS2_VU0_DATA_SIZE> staged;
    // Use a private copy even outside diff mode: failure has no guest effects.
    std::memcpy(staged.data(), data, staged.size());
    Vu0RecContext context;
    context.state = &candidate; context.data = staged.data(); context.itop = itop;
    image->full(context, pc);
    candidate.cycles = context.cycle;
    static thread_local uint64_t calls = 0, mismatches = 0, failures = 0;
    ++calls;
    if (context.failed) { ++failures; return false; }
    if (diff) {
        vu.execute(code, PS2_VU0_CODE_SIZE, data, PS2_VU0_DATA_SIZE,
                   gs, &memory, pc, 0, itop, 4096);
        const VU1State& expected = vu.state();
        const bool equal = std::memcmp(&candidate, &expected, sizeof(expected)) == 0 &&
            std::memcmp(staged.data(), data, staged.size()) == 0;
        if (!equal) {
            ++mismatches;
            if (mismatches <= 8)
                std::fprintf(stderr, "[vu0rec] mismatch call=%llu pc=%x cycles=%llu/%llu finalPC=%x/%x mac=%x/%x status=%x/%x\n",
                    (unsigned long long)calls, pc, (unsigned long long)candidate.cycles,
                    (unsigned long long)expected.cycles, candidate.pc, expected.pc,
                    candidate.mac, expected.mac, candidate.status, expected.status);
        }
        // Diagnostic: retain oracle cost but adopt native completion, separating
        // hidden interpreter state from a host-speed-sensitive DMA failure.
        if (equal && commitDiff) {
            vu.state() = candidate;
            std::memcpy(data, staged.data(), staged.size());
            vu.adoptNativeCompletion(context.cycle, context.backupReg, context.backupValue);
        }
    } else {
        vu.state() = candidate;
        std::memcpy(data, staged.data(), staged.size());
        vu.adoptNativeCompletion(context.cycle, context.backupReg, context.backupValue);
    }
    static thread_local auto last = std::chrono::steady_clock::now();
    if ((calls & 4095u) == 0u) {
        const auto now = std::chrono::steady_clock::now();
        if (now - last >= std::chrono::seconds(5)) {
            std::fprintf(stderr, "[vu0rec] calls=%llu diff=%u mismatches=%llu failures=%llu\n",
                (unsigned long long)calls, diff, (unsigned long long)mismatches, (unsigned long long)failures);
            last = now;
        }
    }
    return true;
}

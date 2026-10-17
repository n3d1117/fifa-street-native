// SPDX-License-Identifier: GPL-3.0-or-later
#include "experimental-settings.hpp"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
extern "C" void tarzan_experiments_trace_clock(uint64_t total, uint64_t delta) {
    static const bool enabled = [] {
        const char* value = std::getenv("TARZAN_TRACE_GAME_CLOCK");
        return value && std::strcmp(value, "1") == 0;
    }();
    if (!enabled) return;
    static thread_local auto start = std::chrono::steady_clock::now();
    static thread_local uint64_t base = total, updates = 0;
    ++updates;
    const auto now = std::chrono::steady_clock::now();
    const double ms = std::chrono::duration<double, std::milli>(now - start).count();
    if (total < base) { base = total; start = now; updates = 0; return; }
    if (ms >= 5000.0) {
        std::fprintf(stderr, "[game-clock] host_ms=%.3f game_ticks=%llu delta=%llu updates=%llu target=%d enabled=%d\n",
            ms, (unsigned long long)(total - base), (unsigned long long)delta,
            (unsigned long long)updates, tarzan_experiments_target_fps(), tarzan_experiments_high_fps());
        start = now; base = total; updates = 0;
    }
}

extern "C" void tarzan_experiments_trace_camera_context(uint32_t returnAddress,
                                                          uint32_t camera,
                                                          uint32_t worldDialog,
                                                          uint32_t dialogId,
                                                          uint32_t worldObject) {
    static const bool enabled = [] {
        const char* value = std::getenv("TARZAN_TRACE_CAMERA_CONTEXT");
        return value && std::strcmp(value, "1") == 0;
    }();
    if (!enabled) return;

    struct Key {
        uint32_t ra, camera, world, id, object;
        bool operator==(const Key& other) const {
            return ra == other.ra && camera == other.camera && world == other.world &&
                   id == other.id && object == other.object;
        }
    };
    static thread_local Key seen[64]{};
    static thread_local unsigned emitted = 0;
    const Key current{returnAddress, camera, worldDialog, dialogId, worldObject};
    for (unsigned i = 0; i < emitted; ++i) {
        if (current == seen[i]) return;
    }
    if (emitted >= 64) return;
    seen[emitted++] = current;
    std::fprintf(stderr,
        "[camera-context] n=%u ra=%08x camera=%08x world=%08x id=%08x object=%08x\n",
        emitted, current.ra, current.camera, current.world, current.id, current.object);
}

extern "C" int tarzan_experiments_widescreen_camera(const uint8_t* rdram,
                                                      uint32_t guestSP) {
    if (!tarzan_experiments_hd_widescreen()) return 0;
    if (!rdram) return 1;

    constexpr uint32_t kRamSize = 32u * 1024u * 1024u;
    constexpr uint32_t kPhysicalMask = 0x1FFFFFFFu;
    auto readGuest32 = [rdram](uint32_t base, uint32_t offset, uint32_t& value) {
        const uint64_t guestAddress = static_cast<uint64_t>(base) + offset;
        if (guestAddress > UINT32_MAX) return false;
        const uint32_t guest = static_cast<uint32_t>(guestAddress);
        uint32_t address = guest;
        if (guest >= kRamSize) {
            const uint32_t segment = guest & 0xE0000000u;
            if (segment != 0x80000000u && segment != 0xA0000000u) return false;
            address = guest & kPhysicalMask;
        }
        if (address > kRamSize - sizeof(uint32_t)) return false;
        // RDRAM and the R5900 guest are little-endian; memcpy avoids alignment UB.
        std::memcpy(&value, rdram + address, sizeof(value));
        return true;
    };

    uint32_t savedRA = 0, savedS1 = 0, dialogID = 0;
    if (!readGuest32(guestSP, 0x30u, savedRA) || savedRA != 0x338BE0u) return 1;
    if (!readGuest32(guestSP, 0x20u, savedS1) ||
        !readGuest32(savedS1, 4u, dialogID)) return 1;
    // The title menu's 3D backdrop is this C3DWorldDlg instance. Gameplay's
    // C3DWorldDlg instances use different IDs and retain widescreen FOV/zoom.
    return dialogID == 0x107003B9u ? 0 : 1;
}

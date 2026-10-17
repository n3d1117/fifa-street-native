// SPDX-License-Identifier: GPL-3.0-or-later
#include "experimental-settings.hpp"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <atomic>

namespace {
constexpr uint32_t kLanguageMenuID = 0x107005B7u;
constexpr uint32_t kLanguageMenuStartup = 1u;
constexpr uint32_t kLanguageMenuActive = 2u;
constexpr uint32_t kGuestRamSize = 32u * 1024u * 1024u;
constexpr uint32_t kGuestPhysicalMask = 0x1FFFFFFFu;

bool readGuestLanguage32(const uint8_t* ram, uint32_t base, uint32_t offset, uint32_t& value) {
    const uint64_t guestAddress = static_cast<uint64_t>(base) + offset;
    if (!ram || guestAddress > UINT32_MAX) return false;
    const uint32_t guest = static_cast<uint32_t>(guestAddress);
    uint32_t address = guest;
    if (guest >= kGuestRamSize) {
        const uint32_t segment = guest & 0xE0000000u;
        if (segment != 0x80000000u && segment != 0xA0000000u) return false;
        address = guest & kGuestPhysicalMask;
    }
    if (address > kGuestRamSize - sizeof(value)) return false;
    std::memcpy(&value, ram + address, sizeof(value));
    return true;
}

std::atomic<bool> s_languageCross{false};
std::atomic<int64_t> s_languageHideDeadlineNs{0};
std::atomic<int64_t> s_languageCrossDeadlineNs{0};
bool s_languageAttempted = false; // Game-thread only.
bool s_languagePending = false;   // Game-thread only.
bool s_languageConfirmArmed = false; // Game-thread only.
uint32_t s_languageMenu = 0;
uint32_t s_languageItem = 0;

void clearLanguageCross() {
    s_languageCross.store(false, std::memory_order_release);
}
} // namespace

extern "C" int tarzan_experiments_skip_startup_logo(const uint8_t* ram, uint32_t name) {
    if (!tarzan_experiments_skip_logos() || name != 0x4B6460u) return 0;
    uint32_t word = 0, tail = 0;
    if (!readGuestLanguage32(ram, name, 0, word) || word != 0x6F676F4Cu ||
        !readGuestLanguage32(ram, name, 4, tail) || (tail & 0xFFu)) return 0;
    std::fprintf(stderr, "[tarzan-boot] skipped startup LOGO.PSS\n");
    return 1;
}

extern "C" uint32_t tarzan_experiments_boot_language_item(const uint8_t* ram,
                                                            uint32_t menu,
                                                            uint32_t originalItem) {
    if (s_languageAttempted || s_languagePending || tarzan_experiments_saved_language() < 0) return originalItem;
    uint32_t menuID = 0, state = 0, count = 0, vector = 0;
    if (!readGuestLanguage32(ram, menu, 4u, menuID) || menuID != kLanguageMenuID ||
        !readGuestLanguage32(ram, menu, 0x98u, state) || state != kLanguageMenuStartup ||
        !readGuestLanguage32(ram, menu, 0x80u, count) || count != 5u ||
        !readGuestLanguage32(ram, menu, 0x84u, vector)) return originalItem;

    uint32_t items[5]{};
    for (uint32_t i = 0; i < 5u; ++i) {
        uint32_t active = 0;
        if (!readGuestLanguage32(ram, vector, i * 4u, items[i]) || !items[i] ||
            !readGuestLanguage32(ram, items[i], 4u, active) || active != 1u) return originalItem;
        for (uint32_t j = 0; j < i; ++j) if (items[j] == items[i]) return originalItem;
    }
    const int saved = tarzan_experiments_saved_language();
    if (saved < 0 || saved > 4) return originalItem;
    s_languageAttempted = true;
    s_languagePending = true;
    s_languageMenu = menu;
    s_languageItem = items[static_cast<uint32_t>(saved)];
    // Hide only this automatic boot transition, with a bounded fallback.
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    s_languageHideDeadlineNs.store(std::chrono::duration_cast<std::chrono::nanoseconds>(
        deadline.time_since_epoch()).count(), std::memory_order_release);
    return s_languageItem;
}

extern "C" void tarzan_experiments_boot_language_menu(const uint8_t* ram, uint32_t menu) {
    uint32_t state = 0, selected = 0, menuID = 0;
    if (s_languageHideDeadlineNs.load(std::memory_order_acquire) &&
        readGuestLanguage32(ram, menu, 4u, menuID) && menuID == 0x10700311u &&
        readGuestLanguage32(ram, menu, 0x98u, state) && state == kLanguageMenuActive)
        s_languageHideDeadlineNs.store(0, std::memory_order_release);
    if (menu != s_languageMenu) return;
    if (!readGuestLanguage32(ram, menu, 4u, menuID) || menuID != kLanguageMenuID) {
        s_languagePending = s_languageConfirmArmed = false;
        clearLanguageCross();
        return;
    }
    if (!s_languagePending && s_languageConfirmArmed) {
        if (!readGuestLanguage32(ram, menu, 0x98u, state) || state != kLanguageMenuActive) {
            s_languageConfirmArmed = false;
            clearLanguageCross();
        }
        return;
    }
    if (!s_languagePending) return;
    if (!readGuestLanguage32(ram, menu, 0x98u, state) ||
        !readGuestLanguage32(ram, menu, 0xA4u, selected)) {
        s_languagePending = false;
        clearLanguageCross();
        return;
    }
    if (state == kLanguageMenuActive && selected == s_languageItem) {
        using Clock = std::chrono::steady_clock;
        const auto deadline = Clock::now() + std::chrono::milliseconds(250);
        const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(deadline.time_since_epoch()).count();
        s_languageCrossDeadlineNs.store(ns, std::memory_order_relaxed);
        s_languageCross.store(true, std::memory_order_release);
        s_languagePending = false;
        s_languageConfirmArmed = true;
    } else if (state != kLanguageMenuStartup && state != 5u) { // Initial item-focus transition also precedes activation.
        s_languagePending = false;
        clearLanguageCross();
    }
}

extern "C" int tarzan_experiments_boot_language_hidden() {
    const int64_t deadline = s_languageHideDeadlineNs.load(std::memory_order_acquire);
    if (!deadline) return 0;
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return std::chrono::duration_cast<std::chrono::nanoseconds>(now).count() < deadline;
}

extern "C" int tarzan_experiments_boot_language_confirm() {
    if (!s_languageCross.load(std::memory_order_acquire)) return 0;
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
    if (ns < s_languageCrossDeadlineNs.load(std::memory_order_relaxed)) return 1;
    s_languageCross.store(false, std::memory_order_release);
    return 0;
}

extern "C" void tarzan_experiments_language_accepted(uint32_t id) {
    if (id >= 5u) return;
    clearLanguageCross();
    s_languagePending = false;
    s_languageConfirmArmed = false;
    s_languageAttempted = true;
    tarzan_experiments_accept_language(id);
}

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

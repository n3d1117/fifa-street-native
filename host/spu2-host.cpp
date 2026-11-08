// SPDX-License-Identifier: GPL-3.0-or-later
#include "spu2-host.hpp"
#include "ps2x/iop/iop_host_spu2.h"
#include "rrv_pcsx2_spu2.h"
#include <SDL3/SDL.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <iterator>

namespace {
constexpr uint32_t kSpu2Base = 0x1F900000u;
constexpr uint32_t kSpu2End = 0x1F900800u;
constexpr uint64_t kMaxIdleCycles = 768u * 32u;
constexpr uint32_t kMaxDmaHalfwords = 0x80000u;
constexpr size_t kAudioChunkFrames = 1024;
struct DmaState {
    uint32_t start_madr{};
    uint32_t end_madr{};
    bool active{};
};
rrv_spu2* g_spu2{};
SDL_AudioStream* g_audio_stream{};
bool g_audio_subsystem_initialized{};
DmaState g_dma[2]{};
std::atomic<uint64_t> g_last_cycle{0};

void SDLCALL audio_callback(void*, SDL_AudioStream* stream, int additional_amount, int) {
    if (!g_spu2 || additional_amount <= 0) return;
    constexpr size_t bytes_per_frame = sizeof(float) * 2;
    size_t remaining = static_cast<size_t>(additional_amount) / bytes_per_frame;
    std::array<float, kAudioChunkFrames * 2> samples{};
    while (remaining) {
        const size_t frames = std::min(remaining, kAudioChunkFrames);
        rrv_spu2_pull_output(g_spu2, samples.data(), frames);
        if (!SDL_PutAudioStreamData(stream, samples.data(), static_cast<int>(frames * bytes_per_frame)))
            return;
        remaining -= frames;
    }
}

uint16_t read16(uint32_t phys_addr, uint64_t iop_cycle) {
    if (!g_spu2 || phys_addr < kSpu2Base || phys_addr >= kSpu2End)
        return 0;
    g_last_cycle.store(iop_cycle, std::memory_order_relaxed);
    return rrv_spu2_read16(g_spu2, iop_cycle, phys_addr);
}

void write16(uint32_t phys_addr, uint16_t value, uint64_t iop_cycle) {
    if (!g_spu2 || phys_addr < kSpu2Base || phys_addr >= kSpu2End)
        return;
    g_last_cycle.store(iop_cycle, std::memory_order_relaxed);

    rrv_spu2_write16(g_spu2, iop_cycle, phys_addr, value);
}

void dma_start(int core, uint16_t* ram, uint32_t madr, uint32_t halfwords,
               bool to_spu2, uint64_t iop_cycle) {
    if (!g_spu2 || core < 0 || core > 1 || !ram || halfwords == 0)
        return;
    g_last_cycle.store(iop_cycle, std::memory_order_relaxed);

    if (halfwords > kMaxDmaHalfwords) {
        std::fprintf(stderr,
                     "[spu2] DMA core %d length %u halfwords exceeds backend limit; truncating to %u\n",
                     core, halfwords, kMaxDmaHalfwords);
        halfwords = kMaxDmaHalfwords;
    }

    g_dma[core] = {madr, madr + halfwords * 2u, true};
    if (to_spu2) {
        rrv_spu2_dma_write(g_spu2, iop_cycle, core, RRV_SPU2_KEEP_TSA, ram, halfwords);
    } else {
        rrv_spu2_dma_read(g_spu2, iop_cycle, core, RRV_SPU2_KEEP_TSA, ram, halfwords);
    }
}

uint32_t dma_madr(int core) {
    if (core < 0 || core > 1)
        return 0;
    // The bridge ABI exposes completion deadlines, not intermediate DMA
    // progress. Keep the original MADR while busy; on its completion event,
    // report the exact clamped end MADR expected by the IOP channel.
    return g_dma[core].active ? g_dma[core].start_madr : g_dma[core].end_madr;
}

void advance(uint64_t iop_cycle) {
    if (!g_spu2)
        return;
    g_last_cycle.store(iop_cycle, std::memory_order_relaxed);

    std::array<rrv_spu2_event, 32> events{};
    size_t count = 0;
    do {
        count = rrv_spu2_sync(g_spu2, iop_cycle, events.data(), events.size());
        for (size_t index = 0; index < count; ++index) {
            const rrv_spu2_event& event = events[index];
            switch (event.kind) {
            case RRV_SPU2_EVENT_SPU_IRQ:
                ps2x::iop::iopSpu2RaiseIrq();
                break;
            case RRV_SPU2_EVENT_DMA4_DONE:
                g_dma[0].active = false;
                ps2x::iop::iopSpu2DmaComplete(0);
                break;
            case RRV_SPU2_EVENT_DMA7_DONE:
                g_dma[1].active = false;
                ps2x::iop::iopSpu2DmaComplete(1);
                break;
            default:
                std::fprintf(stderr, "[spu2] unknown backend event kind %u\n", event.kind);
                break;
            }
        }
    } while (count == events.size());
    // The barrier sync mixes through this cycle, harvests events, and replaces
    // the redundant same-cycle advance call.
}

uint64_t next_event() {
    const uint64_t now = g_last_cycle.load(std::memory_order_relaxed);
    return now > std::numeric_limits<uint64_t>::max() - kMaxIdleCycles
        ? std::numeric_limits<uint64_t>::max()
        : now + kMaxIdleCycles;
}

bool open_audio_device() {
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO)) {
        std::fprintf(stderr, "[spu2] SDL audio initialization failed; emulation will continue silently: %s\n",
                     SDL_GetError());
        return false;
    }
    g_audio_subsystem_initialized = true;
    const SDL_AudioSpec spec{SDL_AUDIO_F32, 2, 48000};
    g_audio_stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec,
                                                audio_callback, nullptr);
    if (!g_audio_stream) {
        std::fprintf(stderr, "[spu2] no SDL playback device; emulation will continue silently: %s\n",
                     SDL_GetError());
        SDL_QuitSubSystem(SDL_INIT_AUDIO);
        g_audio_subsystem_initialized = false;
        return false;
    }
    if (!SDL_ResumeAudioStreamDevice(g_audio_stream)) {
        std::fprintf(stderr, "[spu2] SDL playback resume failed; emulation will continue silently: %s\n",
                     SDL_GetError());
        SDL_DestroyAudioStream(g_audio_stream);
        g_audio_stream = nullptr;
        SDL_QuitSubSystem(SDL_INIT_AUDIO);
        g_audio_subsystem_initialized = false;
        return false;
    }
    return true;
}
} // namespace

bool ps2x_spu2_host_start() {
    if (g_spu2) return true;
    g_spu2 = rrv_spu2_create();
    if (!g_spu2) {
        std::fprintf(stderr, "SPU2 core initialization failed\n");
        return false;
    }
    g_last_cycle.store(0, std::memory_order_relaxed);
    std::fill(std::begin(g_dma), std::end(g_dma), DmaState{});
    ps2x::iop::IopHostSpu2 hooks{};
    hooks.read16 = read16;
    hooks.write16 = write16;
    hooks.dmaStart = dma_start;
    hooks.dmaMadr = dma_madr;
    hooks.advance = advance;
    hooks.nextEvent = next_event;
    hooks.maxIdleStep = kMaxIdleCycles;
    hooks.realTime = open_audio_device();
    ps2x::iop::iopSetHostSpu2(hooks);
    return true;
}

void ps2x_spu2_host_pause(bool paused) {
    if (!g_audio_stream) return;
    if (paused) SDL_PauseAudioStreamDevice(g_audio_stream);
    SDL_ClearAudioStream(g_audio_stream);
    if (!paused) SDL_ResumeAudioStreamDevice(g_audio_stream);
}

void ps2x_spu2_host_stop() {
    if (!g_spu2) return;
    ps2x::iop::iopSetHostSpu2({});
    if (g_audio_stream) {
        SDL_PauseAudioStreamDevice(g_audio_stream);
        SDL_DestroyAudioStream(g_audio_stream);
        g_audio_stream = nullptr;
    }
    if (g_audio_subsystem_initialized) {
        SDL_QuitSubSystem(SDL_INIT_AUDIO);
        g_audio_subsystem_initialized = false;
    }
    rrv_spu2_destroy(g_spu2);
    g_spu2 = nullptr;
}

// SPDX-License-Identifier: GPL-3.0-or-later
#include "spu2-host.hpp"
#include <mutex>

#include "ps2x/iop/iop_host_spu2.h"
#include "rrv_pcsx2_spu2.h"

#include <SDL3/SDL.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iterator>
#include <limits>
#include <sys/stat.h>

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
std::mutex g_perfStatsMutex;
uint64_t g_finalUnderruns = 0;
SDL_AudioStream* g_audio_stream{};
bool g_audio_subsystem_initialized{};
DmaState g_dma[2]{};
std::atomic<uint64_t> g_last_cycle{0};
std::atomic<uint64_t> g_audio_callback_errors{0};
std::atomic<uint64_t> g_audio_requested_frames{0};
std::atomic<uint64_t> g_audio_real_frames{0};
std::atomic<uint64_t> g_audio_real_total_frames{0};
std::atomic<uint64_t> g_audio_energy_scaled{0};
std::atomic<uint64_t> g_audio_sample_count{0};
std::atomic<uint64_t> g_audio_clipped_samples{0};
std::atomic<uint64_t> g_audio_nonfinite_samples{0};
std::atomic<uint32_t> g_audio_peak_scaled{0};
std::atomic<uint32_t> g_audio_queued_high_water{0};
std::atomic<bool> g_audio_noisy_window{false};
std::atomic<uint64_t> g_advanceGapNs{0};
uint64_t g_previousAdvanceNs = 0;
uint64_t monotonic_ns();

bool audio_trace_enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("PS2X_TRACE_SPU2");
        return value && value[0] == '1' && value[1] == '\0';
    }();
    return enabled;
}

bool audio_perf_enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("PS2X_PERF_LOG");
        return value && value[0] == '1' && value[1] == '\0';
    }();
    return enabled;
}

bool audio_ring_trace_enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("PS2X_TRACE_AUDIO_RING");
        return value && value[0] == '1' && value[1] == '\0';
    }();
    return enabled;
}

uint64_t monotonic_ns() {
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}

void record_audio_samples(const float* samples, size_t frames) {
    uint64_t energy = 0;
    uint64_t clipped = 0;
    uint64_t nonfinite = 0;
    uint32_t peak = 0;
    for (size_t i = 0; i < frames * 2u; ++i) {
        const float sample = samples[i];
        if (!std::isfinite(sample)) {
            ++nonfinite;
            continue;
        }
        const float magnitude = std::abs(sample);
        if (magnitude > 1.0f) {
            ++clipped;
            g_audio_noisy_window.store(true, std::memory_order_relaxed);
        }
        const uint32_t scaled = static_cast<uint32_t>(std::min(magnitude, 10.0f) * 10000.0f);
        peak = std::max(peak, scaled);
        energy += static_cast<uint64_t>(scaled) * scaled;
    }
    g_audio_energy_scaled.fetch_add(energy, std::memory_order_relaxed);
    g_audio_sample_count.fetch_add(frames * 2u, std::memory_order_relaxed);
    g_audio_clipped_samples.fetch_add(clipped, std::memory_order_relaxed);
    g_audio_nonfinite_samples.fetch_add(nonfinite, std::memory_order_relaxed);
    uint32_t oldPeak = g_audio_peak_scaled.load(std::memory_order_relaxed);
    while (oldPeak < peak && !g_audio_peak_scaled.compare_exchange_weak(
               oldPeak, peak, std::memory_order_relaxed))
    {
    }
}

void report_audio_health(uint64_t iop_cycle) {
    const bool traceAudio = audio_trace_enabled();
    if ((!traceAudio && !audio_ring_trace_enabled()) || !g_spu2)
        return;
    static uint64_t previousWallNs = 0;
    static uint64_t previousIopCycle = 0;
    static uint64_t previousFramesMixed = 0;
    static uint64_t previousUnderrunFrames = 0;
    static uint64_t previousOverrunFrames = 0;
    const uint64_t now = monotonic_ns();
    if (previousWallNs == 0) {
        previousWallNs = now;
        previousIopCycle = iop_cycle;
        return;
    }
    const uint64_t elapsedNs = now - previousWallNs;
    if (elapsedNs < 1'000'000'000ull)
        return;

    rrv_spu2_stats stats{};
    rrv_spu2_get_stats(g_spu2, &stats);
    if (stats.frames_mixed < previousFramesMixed ||
        stats.underrun_frames < previousUnderrunFrames ||
        stats.overrun_frames < previousOverrunFrames || iop_cycle < previousIopCycle) {
        previousWallNs = now;
        previousIopCycle = iop_cycle;
        previousFramesMixed = stats.frames_mixed;
        previousUnderrunFrames = stats.underrun_frames;
        previousOverrunFrames = stats.overrun_frames;
        return;
    }
    const uint64_t elapsedCycles = iop_cycle >= previousIopCycle ? iop_cycle - previousIopCycle : 0u;
    const uint64_t mixedFrames = stats.frames_mixed - previousFramesMixed;
    const uint64_t underruns = stats.underrun_frames - previousUnderrunFrames;
    const uint64_t overruns = stats.overrun_frames - previousOverrunFrames;
    const uint64_t requested = g_audio_requested_frames.exchange(0u, std::memory_order_relaxed);
    const uint64_t real = g_audio_real_frames.exchange(0u, std::memory_order_relaxed);
    const uint64_t sampleCount = g_audio_sample_count.exchange(0u, std::memory_order_relaxed);
    const uint64_t energy = g_audio_energy_scaled.exchange(0u, std::memory_order_relaxed);
    const uint64_t clipped = g_audio_clipped_samples.exchange(0u, std::memory_order_relaxed);
    const uint64_t nonfinite = g_audio_nonfinite_samples.exchange(0u, std::memory_order_relaxed);
    const uint32_t peak = g_audio_peak_scaled.exchange(0u, std::memory_order_relaxed);
    const uint32_t queuedHighWater = g_audio_queued_high_water.exchange(0u, std::memory_order_relaxed);
    const uint64_t errors = g_audio_callback_errors.exchange(0u, std::memory_order_relaxed);
    const uint64_t realTotal = g_audio_real_total_frames.load(std::memory_order_relaxed);
    const uint64_t ringFrames = stats.frames_enqueued >= realTotal ? stats.frames_enqueued - realTotal : 0u;
    const double seconds = static_cast<double>(elapsedNs) / 1.0e9;
    const double rms = sampleCount != 0u
        ? std::sqrt(static_cast<double>(energy) / static_cast<double>(sampleCount)) / 10000.0
        : 0.0;
    if (traceAudio) {
        std::fprintf(stderr,
                     "[spu2-health] iopMcps=%.2f mixedKfps=%.2f underrun=%llu overrun=%llu "
                     "pull=%llu/%llu ringFrames=%llu rms=%.4f peak=%.4f clipped=%llu nonfinite=%llu queuedMaxB=%u errors=%llu\n",
                     static_cast<double>(elapsedCycles) / seconds / 1.0e6,
                     static_cast<double>(mixedFrames) / seconds / 1000.0,
                     static_cast<unsigned long long>(underruns),
                     static_cast<unsigned long long>(overruns),
                     static_cast<unsigned long long>(real),
                     static_cast<unsigned long long>(requested),
                     static_cast<unsigned long long>(ringFrames), rms,
                     static_cast<double>(peak) / 10000.0,
                     static_cast<unsigned long long>(clipped),
                     static_cast<unsigned long long>(nonfinite), queuedHighWater,
                     static_cast<unsigned long long>(errors));
    } else {
        std::fprintf(stderr,
                     "[spu2-health] iopMcps=%.2f mixedKfps=%.2f underrun=%llu overrun=%llu "
                     "pull=%llu/%llu ringFrames=%llu\n",
                     static_cast<double>(elapsedCycles) / seconds / 1.0e6,
                     static_cast<double>(mixedFrames) / seconds / 1000.0,
                     static_cast<unsigned long long>(underruns),
                     static_cast<unsigned long long>(overruns),
                     static_cast<unsigned long long>(real),
                     static_cast<unsigned long long>(requested),
                     static_cast<unsigned long long>(ringFrames));
    }

    previousWallNs = now;
    previousIopCycle = iop_cycle;
    previousFramesMixed = stats.frames_mixed;
    previousUnderrunFrames = stats.underrun_frames;
    previousOverrunFrames = stats.overrun_frames;
}

void SDLCALL audio_callback(void*, SDL_AudioStream* stream, int additional_amount, int) {
    if (!g_spu2 || additional_amount <= 0)
        return;

    const size_t bytes_per_frame = sizeof(float) * 2;
    size_t remaining_frames = static_cast<size_t>(additional_amount) / bytes_per_frame;
    std::array<float, kAudioChunkFrames * 2> samples{};
    while (remaining_frames != 0) {
        const size_t frames = std::min(remaining_frames, kAudioChunkFrames);
        const size_t realFrames = rrv_spu2_pull_output(g_spu2, samples.data(), frames);
        const bool traceAudio = audio_trace_enabled();
        if (traceAudio || audio_ring_trace_enabled() || audio_perf_enabled()) {
            g_audio_requested_frames.fetch_add(frames, std::memory_order_relaxed);
            g_audio_real_frames.fetch_add(realFrames, std::memory_order_relaxed);
            g_audio_real_total_frames.fetch_add(realFrames, std::memory_order_relaxed);
        }
        if (traceAudio) {
            record_audio_samples(samples.data(), frames);
            const int queued = SDL_GetAudioStreamQueued(stream);
            if (queued > 0) {
                uint32_t oldQueued = g_audio_queued_high_water.load(std::memory_order_relaxed);
                while (oldQueued < static_cast<uint32_t>(queued) && !g_audio_queued_high_water.compare_exchange_weak(
                           oldQueued, static_cast<uint32_t>(queued), std::memory_order_relaxed))
                {
                }
            }
        }
        if (!SDL_PutAudioStreamData(stream, samples.data(), static_cast<int>(frames * bytes_per_frame))) {
            g_audio_callback_errors.fetch_add(1, std::memory_order_relaxed);
            return;
        }
        remaining_frames -= frames;
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
    if (audio_trace_enabled())
        std::fprintf(stderr, "[spu2-dma] start core=%d madr=%08x halfwords=%u %s cycle=%llu\n", core, madr, halfwords,
                     to_spu2 ? "write" : "read", static_cast<unsigned long long>(iop_cycle));

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
    if (audio_perf_enabled()) {
        const uint64_t now = monotonic_ns();
        if (g_previousAdvanceNs != 0) {
            const uint64_t gap = now - g_previousAdvanceNs;
            uint64_t previous = g_advanceGapNs.load(std::memory_order_relaxed);
            while (previous < gap && !g_advanceGapNs.compare_exchange_weak(
                       previous, gap, std::memory_order_relaxed)) {}
        }
        g_previousAdvanceNs = now;
    }
    g_last_cycle.store(iop_cycle, std::memory_order_relaxed);

    std::array<rrv_spu2_event, 32> events{};
    size_t count = 0;
    do {
        count = rrv_spu2_sync(g_spu2, iop_cycle, events.data(), events.size());
        for (size_t index = 0; index < count; ++index) {
            const rrv_spu2_event& event = events[index];
            if (audio_trace_enabled())
                std::fprintf(stderr, "[spu2-event] kind=%u cycle=%llu\n", static_cast<unsigned>(event.kind),
                             static_cast<unsigned long long>(iop_cycle));
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
    report_audio_health(iop_cycle);
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
    SDL_AudioSpec streamInput{}, streamOutput{}, deviceFormat{};
    int deviceBufferFrames = 0;
    const SDL_AudioDeviceID device = SDL_GetAudioStreamDevice(g_audio_stream);
    if (SDL_GetAudioStreamFormat(g_audio_stream, &streamInput, &streamOutput) &&
        SDL_GetAudioDeviceFormat(device, &deviceFormat, &deviceBufferFrames)) {
        std::fprintf(stderr,
                     "[spu2] SDL playback input=%s/%d/%dHz output=%s/%d/%dHz device=%s/%d/%dHz buffer=%d ratio=%.4f\n",
                     SDL_GetAudioFormatName(streamInput.format), streamInput.channels, streamInput.freq,
                     SDL_GetAudioFormatName(streamOutput.format), streamOutput.channels, streamOutput.freq,
                     SDL_GetAudioFormatName(deviceFormat.format), deviceFormat.channels, deviceFormat.freq,
                     deviceBufferFrames, SDL_GetAudioStreamFrequencyRatio(g_audio_stream));
    } else {
        std::fprintf(stderr, "[spu2] SDL playback started (48 kHz stereo float); format query failed: %s\n",
                     SDL_GetError());
    }
    return true;
}
} // namespace

bool ps2x_spu2_host_start() {
    if (g_spu2)
        return true;

    // The IOP already owns this core serially. Inline mode keeps register reads
    // and event barriers from handing work between threads at every checkpoint.
    uint32_t outputRingFrames = 16384;
    if (const char* value = std::getenv("PS2X_SPU2_RING_FRAMES")) {
        char* end = nullptr;
        const unsigned long requestedFrames = std::strtoul(value, &end, 10);
        if (end != value && *end == '\0' && requestedFrames >= 4096ul && requestedFrames <= 65536ul &&
            (requestedFrames & (requestedFrames - 1ul)) == 0ul) {
            outputRingFrames = static_cast<uint32_t>(requestedFrames);
        }
    }
    const rrv_spu2_config config{0, outputRingFrames};
    {
        std::lock_guard lock(g_perfStatsMutex);
        g_finalUnderruns = 0;
        g_spu2 = rrv_spu2_create(&config);
    }
    if (!g_spu2) {
        std::fprintf(stderr, "[spu2] PCSX2 SPU2 core creation failed\n");
        return false;
    }
    std::fprintf(stderr, "[spu2] output ring capacity=%u frames\n", outputRingFrames);

    g_last_cycle.store(0, std::memory_order_relaxed);
    g_advanceGapNs.store(0, std::memory_order_relaxed);
    g_previousAdvanceNs = 0;
    g_audio_callback_errors.store(0, std::memory_order_relaxed);
    g_audio_requested_frames.store(0, std::memory_order_relaxed);
    g_audio_real_frames.store(0, std::memory_order_relaxed);
    g_audio_real_total_frames.store(0, std::memory_order_relaxed);
    g_audio_energy_scaled.store(0, std::memory_order_relaxed);
    g_audio_sample_count.store(0, std::memory_order_relaxed);
    g_audio_clipped_samples.store(0, std::memory_order_relaxed);
    g_audio_nonfinite_samples.store(0, std::memory_order_relaxed);
    g_audio_peak_scaled.store(0, std::memory_order_relaxed);
    g_audio_queued_high_water.store(0, std::memory_order_relaxed);
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

    std::fprintf(stderr, "[spu2] PCSX2 2.8.2 SPU2 core installed; output ring is 48 kHz stereo float\n");
    return true;
}

void ps2x_spu2_host_pause(bool paused) {
    if (!g_audio_stream)
        return;
    if (paused) {
        SDL_PauseAudioStreamDevice(g_audio_stream);
        SDL_ClearAudioStream(g_audio_stream);
    } else {
        SDL_ClearAudioStream(g_audio_stream);
        SDL_ResumeAudioStreamDevice(g_audio_stream);
    }
}

void ps2x_spu2_host_stop() {
    if (!g_spu2)
        return;
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
    if (const uint64_t errors = g_audio_callback_errors.load(std::memory_order_relaxed); errors != 0) {
        std::fprintf(stderr, "[spu2] SDL audio stream callback write errors: %llu\n",
                     static_cast<unsigned long long>(errors));
    }
    std::lock_guard lock(g_perfStatsMutex);
    rrv_spu2_stats stats{};
    rrv_spu2_get_stats(g_spu2, &stats);
    g_finalUnderruns = stats.underrun_frames;
    rrv_spu2_destroy(g_spu2);
    g_spu2 = nullptr;
}

uint64_t ps2x_spu2_host_underruns() {
    std::lock_guard lock(g_perfStatsMutex);
    rrv_spu2_stats stats{};
    if (g_spu2) rrv_spu2_get_stats(g_spu2, &stats);
    return g_spu2 ? stats.underrun_frames : g_finalUnderruns;
}

Spu2HostPerf ps2x_spu2_host_perf() {
    std::lock_guard lock(g_perfStatsMutex);
    rrv_spu2_stats stats{};
    if (g_spu2) rrv_spu2_get_stats(g_spu2, &stats);
    const uint64_t consumed = g_audio_real_total_frames.load(std::memory_order_relaxed);
    return {g_spu2 ? stats.underrun_frames : g_finalUnderruns,
            stats.frames_enqueued >= consumed ? stats.frames_enqueued - consumed : 0u,
            g_advanceGapNs.exchange(0u, std::memory_order_relaxed) * 1e-6};
}

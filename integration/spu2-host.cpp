// SPDX-License-Identifier: GPL-3.0-or-later
#include "spu2-host.hpp"

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
constexpr uint32_t kAutoDmaCore0 = 0x01B0u;
constexpr uint32_t kAutoDmaCore1 = 0x05B0u;
constexpr uint64_t kMaxIdleCycles = 768u * 32u;
constexpr uint32_t kMaxDmaHalfwords = 0x80000u;
constexpr size_t kAudioChunkFrames = 1024;
constexpr size_t kPcmCaptureFrames = 8u * 48000u;

struct DmaState {
    uint32_t start_madr{};
    uint32_t end_madr{};
    bool active{};
};

rrv_spu2* g_spu2{};
SDL_AudioStream* g_audio_stream{};
bool g_audio_subsystem_initialized{};
bool g_auto_dma_enabled[2]{};
bool g_unsupported_dma_reported[2]{};
uint32_t g_dma_trace_count[2]{};
uint32_t g_register_trace_count{};
uint32_t g_noisy_dma_trace_count[2]{};
FILE* g_dma_dump_file{};
uint64_t g_dma_dump_bytes{};
constexpr uint64_t kMaxDmaDumpBytes = 4u * 1024u * 1024u;
DmaState g_dma[2]{};
std::atomic<uint64_t> g_last_cycle{0};
std::atomic<uint64_t> g_audio_callback_errors{0};
std::atomic<uint64_t> g_audio_requested_frames{0};
std::atomic<uint64_t> g_audio_real_frames{0};
std::atomic<uint64_t> g_audio_energy_scaled{0};
std::atomic<uint64_t> g_audio_sample_count{0};
std::atomic<uint64_t> g_audio_clipped_samples{0};
std::atomic<uint64_t> g_audio_nonfinite_samples{0};
std::atomic<uint32_t> g_audio_peak_scaled{0};
std::atomic<uint32_t> g_audio_queued_high_water{0};
std::atomic<bool> g_audio_noisy_window{false};
std::array<float, kPcmCaptureFrames * 2u> g_pcm_capture{};
std::atomic<bool> g_pcm_capture_armed{false};
std::atomic<bool> g_pcm_capture_complete{false};
std::atomic<bool> g_pcm_capture_written{false};
size_t g_pcm_capture_frames{}; // Owned by the SDL audio callback.
bool g_pcm_capture_active{};   // Owned by the SDL audio callback.
uint64_t monotonic_ns();

const char* pcm_capture_path() {
    static const char* path = std::getenv("TARZAN_TRACE_PCM");
    return path;
}

void poll_pcm_capture_trigger() {
    if (!pcm_capture_path() || !*pcm_capture_path() ||
        g_pcm_capture_armed.load(std::memory_order_relaxed))
        return;
    const char* marker = std::getenv("TARZAN_TRACE_PCM_TRIGGER");
    if (!marker || !*marker)
        return;
    static uint64_t lastPollNs = 0;
    const uint64_t now = monotonic_ns();
    if (now - lastPollNs < 100'000'000ull)
        return;
    lastPollNs = now;
    struct stat info{};
    if (stat(marker, &info) == 0) {
        g_pcm_capture_armed.store(true, std::memory_order_release);
        std::fprintf(stderr, "[tarzan-spu2-pcm] marker seen; capturing up to 8 seconds to %s\n",
                     pcm_capture_path());
    }
}

void write_pcm_capture() {
    if (!g_pcm_capture_complete.load(std::memory_order_acquire) ||
        g_pcm_capture_written.exchange(true, std::memory_order_acq_rel))
        return;

    const char* path = pcm_capture_path();
    FILE* file = path && *path ? std::fopen(path, "wb") : nullptr;
    if (!file) {
        std::fprintf(stderr, "[tarzan-spu2-pcm] failed to open capture WAV path\n");
        return;
    }
    const uint32_t dataBytes = static_cast<uint32_t>(g_pcm_capture_frames * 2u * sizeof(float));
    const uint32_t riffBytes = 36u + dataBytes;
    const uint32_t byteRate = 48000u * 2u * sizeof(float);
    const uint16_t format = 3u; // IEEE float WAV.
    const uint16_t channels = 2u;
    const uint32_t sampleRate = 48000u;
    const uint16_t blockAlign = 2u * sizeof(float);
    const uint16_t bitsPerSample = 32u;
    const uint32_t fmtBytes = 16u;
    const auto put16 = [file](uint16_t value) {
        const uint8_t bytes[] = {static_cast<uint8_t>(value), static_cast<uint8_t>(value >> 8)};
        return std::fwrite(bytes, sizeof(bytes), 1u, file) == 1u;
    };
    const auto put32 = [file](uint32_t value) {
        const uint8_t bytes[] = {static_cast<uint8_t>(value), static_cast<uint8_t>(value >> 8),
                                 static_cast<uint8_t>(value >> 16), static_cast<uint8_t>(value >> 24)};
        return std::fwrite(bytes, sizeof(bytes), 1u, file) == 1u;
    };
    const bool headerOk = std::fwrite("RIFF", 4u, 1u, file) == 1u && put32(riffBytes) &&
        std::fwrite("WAVEfmt ", 8u, 1u, file) == 1u && put32(fmtBytes) && put16(format) &&
        put16(channels) && put32(sampleRate) && put32(byteRate) && put16(blockAlign) &&
        put16(bitsPerSample) && std::fwrite("data", 4u, 1u, file) == 1u && put32(dataBytes);
    const bool dataOk = headerOk &&
        std::fwrite(g_pcm_capture.data(), sizeof(float), g_pcm_capture_frames * 2u, file) ==
            g_pcm_capture_frames * 2u;
    std::fclose(file);
    std::fprintf(stderr, "[tarzan-spu2-pcm] %s %zu frames to %s\n",
                 dataOk ? "wrote" : "failed writing", g_pcm_capture_frames, path);
}

bool audio_trace_enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("TARZAN_TRACE_SPU2");
        return value && value[0] == '1' && value[1] == '\0';
    }();
    return enabled;
}

bool dma_trace_enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("TARZAN_TRACE_SPU2_DMA");
        return value && value[0] == '1' && value[1] == '\0';
    }();
    return enabled;
}

void dump_dma_payload(int core, uint16_t* ram, uint32_t madr, uint32_t halfwords,
                      bool to_spu2, uint64_t iop_cycle) {
    if (!g_dma_dump_file || !ram || halfwords == 0u)
        return;
    const uint64_t payloadBytes = static_cast<uint64_t>(halfwords) * sizeof(uint16_t);
    constexpr uint32_t kRecordMagic = 0x32414D44u; // "DMA2" in little-endian files.
    const uint32_t direction = to_spu2 ? 1u : 0u;
    const uint16_t attr = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base +
                                          (core == 0 ? 0x19Au : 0x59Au));
    const uint16_t spdifOut = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + 0x7C0u);
    const uint16_t spdifMode = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + 0x7C6u);
    const uint16_t tsa = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base +
                                         (core == 0 ? 0x1AAu : 0x5AAu));
    const uint32_t fields32[] = {kRecordMagic, static_cast<uint32_t>(core), direction,
                                 madr, halfwords};
    const uint64_t cycle = iop_cycle;
    const uint16_t fields16[] = {attr, spdifOut, spdifMode, tsa};
    const uint64_t recordBytes = sizeof(fields32) + sizeof(cycle) + sizeof(fields16) + payloadBytes;
    if (recordBytes > kMaxDmaDumpBytes - g_dma_dump_bytes)
        return;
    if (std::fwrite(fields32, sizeof(fields32), 1u, g_dma_dump_file) != 1u ||
        std::fwrite(&cycle, sizeof(cycle), 1u, g_dma_dump_file) != 1u ||
        std::fwrite(fields16, sizeof(fields16), 1u, g_dma_dump_file) != 1u ||
        std::fwrite(ram, sizeof(uint16_t), halfwords, g_dma_dump_file) != halfwords) {
        std::fclose(g_dma_dump_file);
        g_dma_dump_file = nullptr;
        std::fprintf(stderr, "[tarzan-spu2-dma] binary capture stopped after a write error\n");
        return;
    }
    g_dma_dump_bytes += recordBytes;
    std::fflush(g_dma_dump_file);
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
    if (!audio_trace_enabled() || !g_spu2)
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
    const double seconds = static_cast<double>(elapsedNs) / 1.0e9;
    const double rms = sampleCount != 0u
        ? std::sqrt(static_cast<double>(energy) / static_cast<double>(sampleCount)) / 10000.0
        : 0.0;
    std::fprintf(stderr,
                 "[tarzan-spu2-health] iopMcps=%.2f mixedKfps=%.2f underrun=%llu overrun=%llu "
                 "pull=%llu/%llu rms=%.4f peak=%.4f clipped=%llu nonfinite=%llu queuedMaxB=%u errors=%llu\n",
                 static_cast<double>(elapsedCycles) / seconds / 1.0e6,
                 static_cast<double>(mixedFrames) / seconds / 1000.0,
                 static_cast<unsigned long long>(underruns),
                 static_cast<unsigned long long>(overruns),
                 static_cast<unsigned long long>(real),
                 static_cast<unsigned long long>(requested), rms,
                 static_cast<double>(peak) / 10000.0,
                 static_cast<unsigned long long>(clipped),
                 static_cast<unsigned long long>(nonfinite), queuedHighWater,
                 static_cast<unsigned long long>(errors));

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
        if (audio_trace_enabled()) {
            g_audio_requested_frames.fetch_add(frames, std::memory_order_relaxed);
            g_audio_real_frames.fetch_add(realFrames, std::memory_order_relaxed);
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
        if (!g_pcm_capture_active && g_pcm_capture_armed.load(std::memory_order_acquire))
            g_pcm_capture_active = true;
        if (g_pcm_capture_active) {
            const size_t captureFrames = std::min(frames, kPcmCaptureFrames - g_pcm_capture_frames);
            std::copy_n(samples.data(), captureFrames * 2u,
                        g_pcm_capture.data() + g_pcm_capture_frames * 2u);
            g_pcm_capture_frames += captureFrames;
            if (g_pcm_capture_frames == kPcmCaptureFrames) {
                g_pcm_capture_active = false;
                g_pcm_capture_complete.store(true, std::memory_order_release);
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

    const uint32_t offset = phys_addr - kSpu2Base;
    if (dma_trace_enabled() && g_register_trace_count < 256u) {
        const uint32_t coreOffset = offset >= 0x400u ? offset - 0x400u : offset;
        const bool modeWrite = offset == 0x7C0u || offset == 0x7C6u;
        const bool coreAttrWrite = coreOffset == 0x19Au;
        const bool keyWrite = coreOffset == 0x1A0u || coreOffset == 0x1A2u ||
                              coreOffset == 0x1A4u || coreOffset == 0x1A6u;
        if (modeWrite || coreAttrWrite || keyWrite) {
            const uint32_t core = offset >= 0x400u && offset < 0x800u ? 1u : 0u;
            std::fprintf(stderr,
                         "[tarzan-spu2-reg] n=%u cycle=%llu core=%u offset=0x%04x value=0x%04x\n",
                         g_register_trace_count++, static_cast<unsigned long long>(iop_cycle),
                         core, offset, value);
            const bool keyOnWrite = coreOffset == 0x1A0u || coreOffset == 0x1A2u;
            if (keyOnWrite && value != 0u) {
                std::fprintf(stderr, "[tarzan-spu2-key] cycle=%llu core=%u mask=%04x voices=",
                             static_cast<unsigned long long>(iop_cycle), core, value);
                bool first = true;
                const uint32_t voiceHalf = (coreOffset & 2u) != 0u ? 1u : 0u;
                for (uint32_t bit = 0; bit < 16u; ++bit) {
                    if ((value & (1u << bit)) == 0u)
                        continue;
                    const uint32_t voice = voiceHalf * 16u + bit;
                    const uint32_t coreBase = core == 0u ? 0u : 0x400u;
                    const uint32_t paramBase = coreBase + voice * 16u;
                    const uint32_t addrBase = coreBase + 0x1C0u + voice * 12u;
                    const uint16_t pitch = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + paramBase + 4u);
                    const uint16_t adsr1 = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + paramBase + 6u);
                    const uint16_t adsr2 = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + paramBase + 8u);
                    const uint16_t ssa = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + addrBase + 2u);
                    const uint16_t nax = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + addrBase + 10u);
                    std::fprintf(stderr, "%s%u:%04x/%04x/%04x/%04x/%04x",
                                 first ? "" : ",", voice, pitch, adsr1, adsr2, ssa, nax);
                    first = false;
                }
                std::fputc('\n', stderr);
            }
        }
    }
    if (offset == kAutoDmaCore0)
        g_auto_dma_enabled[0] = (value & 1u) != 0;
    else if (offset == kAutoDmaCore1)
        g_auto_dma_enabled[1] = (value & 2u) != 0;

    rrv_spu2_write16(g_spu2, iop_cycle, phys_addr, value);
}

void dma_start(int core, uint16_t* ram, uint32_t madr, uint32_t halfwords,
               bool to_spu2, uint64_t iop_cycle) {
    if (!g_spu2 || core < 0 || core > 1 || !ram || halfwords == 0)
        return;
    g_last_cycle.store(iop_cycle, std::memory_order_relaxed);

    if (g_auto_dma_enabled[core]) {
        if (!g_unsupported_dma_reported[core]) {
            std::fprintf(stderr,
                         "[tarzan-spu2] unsupported AutoDMA requested on core %d; "
                         "leaving IOP DMA channel busy instead of faking completion\n",
                         core);
            g_unsupported_dma_reported[core] = true;
        }
        return;
    }

    if (halfwords > kMaxDmaHalfwords) {
        std::fprintf(stderr,
                     "[tarzan-spu2] DMA core %d length %u halfwords exceeds backend limit; truncating to %u\n",
                     core, halfwords, kMaxDmaHalfwords);
        halfwords = kMaxDmaHalfwords;
    }

    // Bounded diagnostic for determining whether movie audio reaches the SPU2
    // as IEC-61937/SPDIF or as ordinary ADPCM/PCM. Capture happens before the
    // backend copies the DMA payload, and is opt-in because initialization can
    // issue many transfers.
    const bool noisyFollowup = g_audio_noisy_window.load(std::memory_order_relaxed) &&
                               g_noisy_dma_trace_count[core] < 16u;
    if (dma_trace_enabled() &&
        (g_dma_trace_count[core] < 64u || noisyFollowup)) {
        const uint32_t traceIndex = g_dma_trace_count[core]++;
        if (noisyFollowup)
            ++g_noisy_dma_trace_count[core];
        const uint16_t spdifOut = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + 0x7C0u);
        const uint16_t spdifMode = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + 0x7C6u);
        const uint16_t coreAttr = rrv_spu2_read16(g_spu2, iop_cycle,
                                                   kSpu2Base + (core == 0 ? 0x19Au : 0x59Au));
        std::fprintf(stderr,
                     "[tarzan-spu2-dma] n=%u noisy=%u core=%d dir=%s cycle=%llu madr=0x%08x halfwords=%u "
                     "tsa=%04x attr=%04x spdifOut=%04x spdifMode=%04x data=",
                     traceIndex, noisyFollowup ? 1u : 0u, core, to_spu2 ? "iop2spu" : "spu2iop",
                     static_cast<unsigned long long>(iop_cycle), madr, halfwords,
                     static_cast<unsigned>(rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base +
                         (core == 0 ? 0x1AAu : 0x5AAu))), coreAttr, spdifOut, spdifMode);
        const uint32_t sampleCount = std::min<uint32_t>(halfwords, 32u);
        for (uint32_t index = 0; index < sampleCount; ++index)
            std::fprintf(stderr, "%s%04x", index == 0 ? "" : ",", ram[index]);
        const uint16_t konLow = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base +
                                                (core == 0 ? 0x1A0u : 0x5A0u));
        const uint16_t konHigh = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base +
                                                 (core == 0 ? 0x1A2u : 0x5A2u));
        std::fprintf(stderr, " kon=%04x:%04x voices=", konHigh, konLow);
        for (uint32_t voice = 0; voice < 4u; ++voice) {
            const uint32_t voiceBase = (core == 0 ? 0u : 0x400u) + voice * 16u;
            const uint32_t addrBase = (core == 0 ? 0u : 0x400u) + voice * 12u;
            const uint16_t pitch = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + voiceBase + 4u);
            const uint16_t adsr1 = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + voiceBase + 6u);
            const uint16_t ssa = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + 0x1C0u + addrBase);
            const uint16_t nax = rrv_spu2_read16(g_spu2, iop_cycle, kSpu2Base + 0x1C8u + addrBase);
            std::fprintf(stderr, "%s%u:%04x/%04x/%04x/%04x",
                         voice == 0 ? "" : ",", voice, pitch, adsr1, ssa, nax);
        }
        std::fputc('\n', stderr);
    }

    if (g_dma_dump_file)
        dump_dma_payload(core, ram, madr, halfwords, to_spu2, iop_cycle);

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
    poll_pcm_capture_trigger();
    write_pcm_capture();
    g_last_cycle.store(iop_cycle, std::memory_order_relaxed);
    rrv_spu2_advance(g_spu2, iop_cycle);
    report_audio_health(iop_cycle);

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
                std::fprintf(stderr, "[tarzan-spu2] unknown backend event kind %u\n", event.kind);
                break;
            }
        }
    } while (count == events.size());
}

uint64_t next_event() {
    const uint64_t now = g_last_cycle.load(std::memory_order_relaxed);
    return now > std::numeric_limits<uint64_t>::max() - kMaxIdleCycles
        ? std::numeric_limits<uint64_t>::max()
        : now + kMaxIdleCycles;
}

bool open_audio_device() {
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO)) {
        std::fprintf(stderr, "[tarzan-spu2] SDL audio initialization failed; emulation will continue silently: %s\n",
                     SDL_GetError());
        return false;
    }
    g_audio_subsystem_initialized = true;
    const SDL_AudioSpec spec{SDL_AUDIO_F32, 2, 48000};
    g_audio_stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec,
                                                audio_callback, nullptr);
    if (!g_audio_stream) {
        std::fprintf(stderr, "[tarzan-spu2] no SDL playback device; emulation will continue silently: %s\n",
                     SDL_GetError());
        SDL_QuitSubSystem(SDL_INIT_AUDIO);
        g_audio_subsystem_initialized = false;
        return false;
    }
    if (!SDL_ResumeAudioStreamDevice(g_audio_stream)) {
        std::fprintf(stderr, "[tarzan-spu2] SDL playback resume failed; emulation will continue silently: %s\n",
                     SDL_GetError());
        SDL_DestroyAudioStream(g_audio_stream);
        g_audio_stream = nullptr;
        SDL_QuitSubSystem(SDL_INIT_AUDIO);
        g_audio_subsystem_initialized = false;
        return false;
    }
    std::fprintf(stderr, "[tarzan-spu2] SDL playback started (48 kHz stereo float)\n");
    return true;
}
} // namespace

bool tarzan_spu2_host_start() {
    if (g_spu2)
        return true;

    // The IOP already owns this core serially. Inline mode keeps register reads
    // and event barriers from handing work between threads at every checkpoint.
    const rrv_spu2_config config{0, 8192};
    g_spu2 = rrv_spu2_create(&config);
    if (!g_spu2) {
        std::fprintf(stderr, "[tarzan-spu2] PCSX2 SPU2 core creation failed\n");
        return false;
    }

    g_last_cycle.store(0, std::memory_order_relaxed);
    g_pcm_capture_frames = 0;
    g_pcm_capture_active = false;
    g_pcm_capture_armed.store(false, std::memory_order_relaxed);
    g_pcm_capture_complete.store(false, std::memory_order_relaxed);
    g_pcm_capture_written.store(false, std::memory_order_relaxed);
    g_dma_dump_bytes = 0;
    if (const char* dumpPath = std::getenv("TARZAN_TRACE_SPU2_DUMP"); dumpPath && dumpPath[0] != '\0') {
        g_dma_dump_file = std::fopen(dumpPath, "wb");
        if (g_dma_dump_file)
            std::fprintf(stderr, "[tarzan-spu2-dma] writing bounded DMA payload capture to %s (max 4 MiB)\n", dumpPath);
        else
            std::fprintf(stderr, "[tarzan-spu2-dma] failed to open payload capture path %s\n", dumpPath);
    }
    g_audio_callback_errors.store(0, std::memory_order_relaxed);
    g_audio_requested_frames.store(0, std::memory_order_relaxed);
    g_audio_real_frames.store(0, std::memory_order_relaxed);
    g_audio_energy_scaled.store(0, std::memory_order_relaxed);
    g_audio_sample_count.store(0, std::memory_order_relaxed);
    g_audio_clipped_samples.store(0, std::memory_order_relaxed);
    g_audio_nonfinite_samples.store(0, std::memory_order_relaxed);
    g_audio_peak_scaled.store(0, std::memory_order_relaxed);
    g_audio_queued_high_water.store(0, std::memory_order_relaxed);
    std::fill(std::begin(g_auto_dma_enabled), std::end(g_auto_dma_enabled), false);
    std::fill(std::begin(g_unsupported_dma_reported), std::end(g_unsupported_dma_reported), false);
    std::fill(std::begin(g_dma), std::end(g_dma), DmaState{});

    ps2x::iop::IopHostSpu2 hooks{};
    hooks.read16 = read16;
    hooks.write16 = write16;
    hooks.dmaStart = dma_start;
    hooks.dmaMadr = dma_madr;
    hooks.advance = advance;
    hooks.nextEvent = next_event;
    hooks.maxIdleStep = kMaxIdleCycles;
    ps2x::iop::iopSetHostSpu2(hooks);

    (void)open_audio_device(); // Missing audio hardware must not stop IOP emulation.
    std::fprintf(stderr, "[tarzan-spu2] PCSX2 2.8.2 SPU2 core installed; output ring is 48 kHz stereo float\n");
    return true;
}

void tarzan_spu2_host_stop() {
    if (!g_spu2)
        return;
    write_pcm_capture();
    ps2x::iop::iopSetHostSpu2({});

    if (g_dma_dump_file) {
        std::fclose(g_dma_dump_file);
        g_dma_dump_file = nullptr;
    }

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
        std::fprintf(stderr, "[tarzan-spu2] SDL audio stream callback write errors: %llu\n",
                     static_cast<unsigned long long>(errors));
    }
    rrv_spu2_destroy(g_spu2);
    g_spu2 = nullptr;
}

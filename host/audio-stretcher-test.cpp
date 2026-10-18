// SPDX-License-Identifier: GPL-3.0-or-later
#include "rrv_audio_stretcher.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <vector>

namespace {
constexpr size_t kCapacity = 8192;
constexpr size_t kDeviceFramesPerTick = 1024;
constexpr size_t kTicks = 1200;

struct Ring {
    std::array<float, kCapacity * 2> data{};
    size_t head{};
    size_t tail{};
    uint64_t dropped{};
    size_t maximum{};
    uint64_t underruns{};
    uint64_t phase{};

    size_t size() const { return head - tail; }

    static bool push(void* context, float left, float right) {
        auto& ring = *static_cast<Ring*>(context);
        if (ring.size() >= kCapacity) {
            ++ring.dropped;
            return false;
        }
        const size_t index = (ring.head % kCapacity) * 2;
        ring.data[index] = left;
        ring.data[index + 1] = right;
        ++ring.head;
        ring.maximum = std::max(ring.maximum, ring.size());
        return true;
    }

    void consume(float* output, size_t frames) {
        const size_t count = std::min(frames, size());
        for (size_t i = 0; i < count; ++i) {
            const size_t index = ((tail + i) % kCapacity) * 2;
            output[2 * i] = data[index];
            output[2 * i + 1] = data[index + 1];
        }
        std::fill(output + count * 2, output + frames * 2, 0.0f);
        tail += count;
        underruns += frames - count;
    }
};
}

int main() {
    rrv_audio_stretcher stretcher;
    Ring ring;
    std::array<float, 1195 * 2> input{};
    std::array<float, 64 * 2> pending{};
    size_t pending_frames = 0;
    std::array<float, kDeviceFramesPerTick * 2> output{};
    uint64_t phase = 0;
    uint64_t stable_underruns = 0;
    uint64_t analyzed_frames = 0;
    uint64_t positive_crossings = 0;
    std::vector<size_t> steady_queues;
    float previous = 0.0f;

    for (size_t tick = 0; tick < kTicks; ++tick) {
        const size_t produced_frames = tick < 60 ? 1195u   // 56 kHz startup burst.
            : tick < 120 ? 1109u                           // 52 kHz sustained burst.
            : 1024u;                                       // Stable 48 kHz operation.
        for (size_t i = 0; i < produced_frames; ++i) {
            const float sample = static_cast<float>(std::sin(
                2.0 * 3.14159265358979323846 * 440.0 * phase / 48000.0));
            input[2 * i] = sample;
            input[2 * i + 1] = sample;
            ++phase;
        }

        for (size_t offset = 0; offset < produced_frames; ++offset) {
            pending[2 * pending_frames] = input[2 * offset];
            pending[2 * pending_frames + 1] = input[2 * offset + 1];
            if (++pending_frames == 64) {
                stretcher.process(pending.data(), pending_frames, ring.size(), ring.underruns,
                                  Ring::push, &ring);
                pending_frames = 0;
            }
        }

        const uint64_t underruns_before = ring.underruns;
        ring.consume(output.data(), kDeviceFramesPerTick);
        if (tick >= 400) {
            stable_underruns += ring.underruns - underruns_before;
            steady_queues.push_back(ring.size());
            for (size_t i = 0; i < kDeviceFramesPerTick; ++i) {
                const float sample = output[2 * i];
                if (previous <= 0.0f && sample > 0.0f)
                    ++positive_crossings;
                previous = sample;
                ++analyzed_frames;
            }
        } else {
            previous = output.back();
        }
    }

    const double measured_hz = static_cast<double>(positive_crossings) * 48000.0 /
                               static_cast<double>(analyzed_frames);
    std::sort(steady_queues.begin(), steady_queues.end());
    uint64_t queue_sum = 0;
    for (size_t queue : steady_queues)
        queue_sum += queue;
    const double mean_steady_queue = static_cast<double>(queue_sum) /
                                     static_cast<double>(steady_queues.size());
    const size_t p95_steady_queue = steady_queues[(steady_queues.size() * 95u) / 100u];
    std::printf("tempo=%.4f queue=%zu maxQueue=%zu dropped=%llu startupUnderrun=%llu "
                "steadyUnderrun=%llu meanSteadyQueue=%.1f p95SteadyQueue=%zu pitch=%.2fHz\n",
                stretcher.tempo(), ring.size(), ring.maximum,
                static_cast<unsigned long long>(ring.dropped),
                static_cast<unsigned long long>(ring.underruns - stable_underruns),
                static_cast<unsigned long long>(stable_underruns), mean_steady_queue,
                p95_steady_queue, measured_hz);
    if (ring.dropped != 0 || stable_underruns != 0 || p95_steady_queue > 4096 ||
        ring.maximum > 8192 ||
        stretcher.tempo() < 0.98f || stretcher.tempo() > 1.02f ||
        measured_hz < 430.0 || measured_hz > 450.0)
        return 1;
    return 0;
}

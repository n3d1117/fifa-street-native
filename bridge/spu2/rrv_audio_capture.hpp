// SPDX-License-Identifier: GPL-3.0+
#pragma once
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

// Diagnostic-only: each track has one writer. The worker reads its buffer only
// after acquiring done; shutdown joins the owners before stopping the worker.
struct rrv_audio_capture
{
    struct Track {
        std::vector<float> pcm;
        size_t frames = 0;
        uint64_t first_ns = 0, mixed_frame = 0, ring_head = 0, ring_tail = 0;
        std::atomic<bool> done{false};
        explicit Track(size_t capacity) : pcm(capacity * 2) {}
    };
    struct Pull { size_t offset, frames, real; uint64_t ring_tail; };
    Track producer, callback;
    std::array<Pull, 32768> pulls{};
    size_t pull_count = 0, lost_pulls = 0;
    const std::string prefix;
    const uint64_t target_ns;
    std::atomic<bool> armed{false}, stopping{false};
    std::thread writer;

    static uint64_t now_ns() {
        return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
    }
    rrv_audio_capture(const char* path, unsigned delay, unsigned seconds)
        : producer(size_t(seconds) * 48000), callback(size_t(seconds) * 48000),
          prefix(path), target_ns(now_ns() + uint64_t(delay) * 1000000000ull),
          writer([this] { work(); }) {}
    ~rrv_audio_capture() {
        stopping.store(true, std::memory_order_release);
        if (writer.joinable()) writer.join();
    }
    static void anchor(Track& track, uint64_t mixed, uint64_t head, uint64_t tail) {
        if (track.frames != 0) return;
        track.first_ns = now_ns();
        track.mixed_frame = mixed;
        track.ring_head = head;
        track.ring_tail = tail;
    }
    void mixed(float left, float right, uint64_t mixed_frame, uint64_t head, uint64_t tail) {
        if (!armed.load(std::memory_order_acquire) || producer.done.load(std::memory_order_relaxed)) return;
        anchor(producer, mixed_frame, head, tail);
        producer.pcm[2 * producer.frames] = left;
        producer.pcm[2 * producer.frames + 1] = right;
        if (++producer.frames == producer.pcm.size() / 2)
            producer.done.store(true, std::memory_order_release);
    }
    void output(const float* samples, size_t frames, size_t real,
                uint64_t mixed_frame, uint64_t head, uint64_t tail) {
        if (!armed.load(std::memory_order_acquire) || callback.done.load(std::memory_order_relaxed)) return;
        anchor(callback, mixed_frame, head, tail);
        const size_t count = std::min(frames, callback.pcm.size() / 2 - callback.frames);
        if (pull_count < pulls.size())
            pulls[pull_count++] = {callback.frames, count, std::min(count, real), tail};
        else ++lost_pulls;
        std::memcpy(callback.pcm.data() + 2 * callback.frames, samples, count * 2 * sizeof(float));
        callback.frames += count;
        if (callback.frames == callback.pcm.size() / 2)
            callback.done.store(true, std::memory_order_release);
    }
    static bool wav(const std::string& path, const Track& track) {
        // IEEE float32, stereo, 48 kHz; all supported Apple hosts are little endian.
        const uint32_t bytes = static_cast<uint32_t>(track.frames * 2 * sizeof(float));
        const std::array<uint32_t, 14> header{{
            0x46464952, 48u + bytes, 0x45564157, 0x20746d66, 16,
            0x00020003, 48000, 48000 * 8, 0x00200008,
            0x74636166, 4, static_cast<uint32_t>(track.frames), 0x61746164, bytes}};
        FILE* file = std::fopen(path.c_str(), "wb");
        if (!file) return false;
        bool ok = std::fwrite(header.data(), sizeof(header), 1, file) == 1;
        ok = std::fwrite(track.pcm.data(), sizeof(float) * 2, track.frames, file) == track.frames && ok;
        return std::fclose(file) == 0 && ok;
    }
    void work() {
        while (!stopping.load(std::memory_order_acquire) && now_ns() < target_ns)
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        armed.store(true, std::memory_order_release);
        while (!stopping.load(std::memory_order_acquire) &&
               !(producer.done.load(std::memory_order_acquire) && callback.done.load(std::memory_order_acquire)))
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        // Stop is set only after producer and callback threads are quiescent.
        const bool producer_ok = wav(prefix + "-producer.wav", producer);
        const bool callback_ok = wav(prefix + "-callback.wav", callback);
        FILE* json = std::fopen((prefix + "-anchors.json").c_str(), "wb");
        bool metadata_ok = json != nullptr;
        if (json) {
            std::fprintf(json, "{\"rate\":48000,\"channels\":2,\"target_monotonic_ns\":%llu,", (unsigned long long)target_ns);
            auto track = [json](const char* name, const Track& t) {
                std::fprintf(json, "\"%s\":{\"frames\":%zu,\"first_monotonic_ns\":%llu,\"mixed_frame\":%llu,\"ring_head\":%llu,\"ring_tail\":%llu,\"complete\":%s},",
                    name, t.frames, (unsigned long long)t.first_ns, (unsigned long long)t.mixed_frame,
                    (unsigned long long)t.ring_head, (unsigned long long)t.ring_tail,
                    t.done.load(std::memory_order_acquire) ? "true" : "false");
            };
            track("producer", producer); track("callback", callback);
            std::fprintf(json, "\"lost_pull_records\":%zu,\"pulls\":[", lost_pulls);
            for (size_t i = 0; i < pull_count; ++i) {
                const auto& p = pulls[i];
                std::fprintf(json, "%s{\"offset\":%zu,\"frames\":%zu,\"real\":%zu,\"ring_tail\":%llu}",
                    i ? "," : "", p.offset, p.frames, p.real, (unsigned long long)p.ring_tail);
            }
            std::fprintf(json, "]}\n");
            metadata_ok = !std::ferror(json);
            metadata_ok = std::fclose(json) == 0 && metadata_ok;
        }
        std::fprintf(stderr, "[spu2-capture] prefix=%s producer=%zu callback=%zu complete=%d files_ok=%d\n",
            prefix.c_str(), producer.frames, callback.frames,
            int(producer.done.load() && callback.done.load()), int(producer_ok && callback_ok && metadata_ok));
    }
};

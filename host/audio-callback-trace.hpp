// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <pthread.h>
#include <string>
#include <thread>
#if defined(__APPLE__)
#include <pthread/qos.h>
#endif

namespace host {
enum class AudioCallbackStatus : uint32_t { NoData, Supplied, WriteError, NoBackend };
struct AudioCallbackRecord {
    uint64_t sequence, entry_ns, exit_ns, frames_fed, pthread_handle;
    int additional_amount, total_amount;
    uint32_t write_errors;
    AudioCallbackStatus status;
    bool thread_changed;
    int policy_result, policy, priority, qos_result;
    unsigned qos;
    int relative_priority;
};

// SDL serializes calls to a stream's callback even if its physical device changes.
// The callback owns push; one background writer owns pop.
template<size_t Capacity> class AudioCallbackRing {
    static_assert(Capacity > 0);
    static_assert(std::atomic<uint64_t>::is_always_lock_free);
    std::array<AudioCallbackRecord, Capacity> records_{};
    alignas(64) std::atomic<uint64_t> head_{0};
    alignas(64) std::atomic<uint64_t> tail_{0};
    std::atomic<uint64_t> lost_{0};
public:
    bool push(const AudioCallbackRecord& record) noexcept {
        const uint64_t head = head_.load(std::memory_order_relaxed);
        if (head - tail_.load(std::memory_order_acquire) == Capacity) {
            lost_.fetch_add(1, std::memory_order_relaxed);
            return false;
        }
        records_[head % Capacity] = record;
        head_.store(head + 1, std::memory_order_release);
        return true;
    }
    bool pop(AudioCallbackRecord& record) noexcept {
        const uint64_t tail = tail_.load(std::memory_order_relaxed);
        if (tail == head_.load(std::memory_order_acquire)) return false;
        record = records_[tail % Capacity];
        tail_.store(tail + 1, std::memory_order_release);
        return true;
    }
    uint64_t lost() const noexcept { return lost_.load(std::memory_order_relaxed); }
};

class AudioCallbackTrace {
public:
    static constexpr size_t kCapacity = 4096;
    class Invocation {
        AudioCallbackTrace* trace_;
        AudioCallbackRecord record_;
    public:
        Invocation(AudioCallbackTrace* trace, int additional, int total) noexcept : trace_(trace) {
            if (!trace_ || trace_->stopping_.load(std::memory_order_relaxed)) { trace_ = nullptr; return; }
            record_ = {};
            record_.sequence = trace_->next_sequence_++;
            record_.entry_ns = now_ns();
            record_.additional_amount = additional;
            record_.total_amount = total;
            record_.status = AudioCallbackStatus::NoData;
            trace_->thread_snapshot(record_);
        }
        ~Invocation() {
            if (!trace_) return;
            record_.exit_ns = now_ns();
            trace_->ring_.push(record_);
        }
        Invocation(const Invocation&) = delete;
        Invocation& operator=(const Invocation&) = delete;
        void fed(size_t frames) noexcept {
            if (!trace_) return;
            record_.frames_fed += frames;
            record_.status = AudioCallbackStatus::Supplied;
        }
        void write_error() noexcept {
            if (!trace_) return;
            ++record_.write_errors;
            record_.status = AudioCallbackStatus::WriteError;
        }
        void no_backend() noexcept {
            if (trace_) record_.status = AudioCallbackStatus::NoBackend;
        }
    };
    explicit AudioCallbackTrace(const std::string& prefix)
        : path_(prefix + ".jsonl"), writer_([this] { work(); }) {}
    ~AudioCallbackTrace() { stop(); }
    // The caller must quiesce the callback before stopping or destroying a trace.
    void stop() noexcept {
        stopping_.store(true, std::memory_order_release);
        if (writer_.joinable()) writer_.join();
    }
private:
    AudioCallbackRing<kCapacity> ring_;
    uint64_t next_sequence_ = 0;
    pthread_t producer_thread_{};
    bool have_producer_thread_ = false;
    std::atomic<bool> stopping_{false};
    const std::string path_;
    std::thread writer_;
    static uint64_t now_ns() noexcept {
        return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
    }
    void thread_snapshot(AudioCallbackRecord& record) noexcept {
        const pthread_t self = pthread_self();
        static_assert(sizeof(self) <= sizeof(record.pthread_handle));
        std::memcpy(&record.pthread_handle, &self, sizeof(self));
        if (have_producer_thread_ && pthread_equal(self, producer_thread_)) return;
        producer_thread_ = self;
        have_producer_thread_ = true;
        record.thread_changed = true;
        // Query this live thread only; retaining a pthread for a background query
        // is unsafe if a device is replaced and its callback thread is destroyed.
        sched_param scheduling{};
        record.policy_result = pthread_getschedparam(self, &record.policy, &scheduling);
        record.priority = scheduling.sched_priority;
#if defined(__APPLE__)
        qos_class_t qos = QOS_CLASS_UNSPECIFIED;
        record.qos_result = pthread_get_qos_class_np(self, &qos, &record.relative_priority);
        record.qos = static_cast<unsigned>(qos);
#else
        record.qos_result = -1;
#endif
    }
    static const char* status_name(AudioCallbackStatus status) noexcept {
        switch (status) {
        case AudioCallbackStatus::NoData: return "no_data";
        case AudioCallbackStatus::Supplied: return "supplied";
        case AudioCallbackStatus::WriteError: return "write_error";
        case AudioCallbackStatus::NoBackend: return "no_backend";
        }
        return "unknown";
    }
    void work() noexcept {
        FILE* file = std::fopen(path_.c_str(), "wb");
        if (!file) {
            std::fprintf(stderr, "[audio-timing] cannot open %s\n", path_.c_str());
            return;
        }
        std::fprintf(file, "{\"type\":\"config\",\"clock\":\"steady_clock_ns\",\"capacity\":%zu}\n", kCapacity);
        uint64_t emitted = 0, reported_lost = 0;
        bool failed = false;
        for (;;) {
            const bool stopping = stopping_.load(std::memory_order_acquire);
            AudioCallbackRecord record{};
            bool wrote = false;
            while (ring_.pop(record)) {
                if (!failed) {
                    std::fprintf(file,
                        "{\"type\":\"callback\",\"sequence\":%llu,\"entry_ns\":%llu,\"exit_ns\":%llu,"
                        "\"additional_amount\":%d,\"total_amount\":%d,\"frames_fed\":%llu,"
                        "\"write_errors\":%u,\"status\":\"%s\",\"pthread_handle\":%llu,\"thread_changed\":%s,"
                        "\"policy_result\":%d,\"policy\":%d,\"priority\":%d,\"qos_result\":%d,\"qos\":%u,\"relative_priority\":%d}\n",
                        (unsigned long long)record.sequence, (unsigned long long)record.entry_ns,
                        (unsigned long long)record.exit_ns, record.additional_amount, record.total_amount,
                        (unsigned long long)record.frames_fed, record.write_errors, status_name(record.status),
                        (unsigned long long)record.pthread_handle, record.thread_changed ? "true" : "false",
                        record.policy_result, record.policy, record.priority, record.qos_result, record.qos, record.relative_priority);
                    wrote = true;
                }
                ++emitted;
            }
            const uint64_t lost = ring_.lost();
            if (!failed && lost != reported_lost) {
                std::fprintf(file, "{\"type\":\"overflow\",\"lost_records\":%llu}\n", (unsigned long long)lost);
                reported_lost = lost;
                wrote = true;
            }
            if (!failed && (wrote || emitted == 0)) {
                if (std::fflush(file) != 0 || std::ferror(file)) {
                    std::fprintf(stderr, "[audio-timing] write failed for %s\n", path_.c_str());
                    failed = true;
                }
            }
            if (stopping) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
        if (!failed)
            std::fprintf(file, "{\"type\":\"stopped\",\"records\":%llu,\"lost_records\":%llu}\n",
                (unsigned long long)emitted, (unsigned long long)ring_.lost());
        if (std::fclose(file) != 0)
            std::fprintf(stderr, "[audio-timing] close failed for %s\n", path_.c_str());
    }
};
} // namespace host

// SPDX-License-Identifier: GPL-3.0-or-later
#include "audio-callback-trace.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
#include <unistd.h>

static void check(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
static void overflow_and_wrap() {
    host::AudioCallbackRing<4> ring;
    host::AudioCallbackRecord record{};
    for (uint64_t i = 0; i < 4; ++i) { record.sequence = i; check(ring.push(record), "queue filled early"); }
    check(!ring.push(record) && !ring.push(record) && ring.lost() == 2, "overflow count wrong");
    check(ring.pop(record) && record.sequence == 0, "overflow overwrote unread data");
    record.sequence = 6;
    check(ring.push(record), "wrapped slot unavailable");
    for (uint64_t expected : {1u, 2u, 3u, 6u})
        check(ring.pop(record) && record.sequence == expected, "wrapped queue order wrong");
    check(!ring.pop(record), "empty queue returned data");
}
static void concurrent_order() {
    host::AudioCallbackRing<64> ring;
    constexpr uint64_t count = 50000;
    std::atomic<bool> valid{true};
    std::thread producer([&] {
        for (uint64_t i = 0; i < count; ++i) {
            host::AudioCallbackRecord record{};
            record.sequence = i; record.entry_ns = i * 31; record.total_amount = -int(i % 17);
            while (!ring.push(record)) std::this_thread::yield();
        }
    });
    std::thread consumer([&] {
        for (uint64_t i = 0; i < count; ++i) {
            host::AudioCallbackRecord record{};
            while (!ring.pop(record)) std::this_thread::yield();
            if (record.sequence != i || record.entry_ns != i * 31 || record.total_amount != -int(i % 17))
                valid.store(false, std::memory_order_relaxed);
        }
    });
    producer.join(); consumer.join();
    check(valid.load(), "concurrent publication lost ordering or record contents");
}
static void fake_callback(host::AudioCallbackTrace* trace, int additional, int total, int fail_after) {
    host::AudioCallbackTrace::Invocation timing(trace, additional, total);
    if (additional <= 0) return;
    for (int i = 0; i < 3; ++i) {
        if (i == fail_after) { timing.write_error(); return; }
        timing.fed(128);
    }
}
static std::vector<std::string> callbacks(const std::filesystem::path& path) {
    std::ifstream file(path);
    std::vector<std::string> lines;
    for (std::string line; std::getline(file, line);)
        if (!line.empty() && line.back() == '}' && line.find("\"type\":\"callback\"") != std::string::npos) lines.push_back(line);
    return lines;
}
static uint64_t number(const std::string& line, const char* key) {
    const std::string needle = std::string("\"") + key + "\":";
    const size_t position = line.find(needle);
    check(position != std::string::npos, "missing JSON numeric field");
    return std::stoull(line.substr(position + needle.size()));
}
static void flush_and_returns(const std::filesystem::path& prefix) {
    host::AudioCallbackTrace trace(prefix.string());
    fake_callback(&trace, 0, 8192, -1);
    fake_callback(&trace, -8, -16, -1);
    fake_callback(&trace, 3072, 8192, 1);
    fake_callback(&trace, 3072, 3072, -1);
    { host::AudioCallbackTrace::Invocation timing(&trace, 8, 8); timing.no_backend(); }
    // The file must be complete enough to inspect while the writer is still live.
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    std::vector<std::string> lines;
    do {
        lines = callbacks(prefix.string() + ".jsonl");
        if (lines.size() == 5) break;
        std::this_thread::yield();
    } while (std::chrono::steady_clock::now() < deadline);
    check(lines.size() == 5, "background writer did not flush before stop/exit");
    for (size_t i = 0; i < lines.size(); ++i) {
        check(number(lines[i], "sequence") == i, "invocation grouping/sequence wrong");
        check(number(lines[i], "exit_ns") >= number(lines[i], "entry_ns"), "RAII exit missing");
    }
    check(lines[0].find("\"thread_changed\":true") != std::string::npos &&
          number(lines[0], "policy_result") == 0, "own-thread snapshot missing");
    check(lines[1].find("\"additional_amount\":-8,\"total_amount\":-16") != std::string::npos,
          "negative invocation disappeared");
    check(number(lines[2], "frames_fed") == 128 && number(lines[2], "write_errors") == 1 &&
          lines[2].find("\"status\":\"write_error\"") != std::string::npos, "early write failure not finalized");
    check(number(lines[3], "frames_fed") == 384 && number(lines[3], "write_errors") == 0,
          "multiple chunks split or miscounted invocation");
    check(lines[4].find("\"status\":\"no_backend\"") != std::string::npos, "backend early return missing");
    fake_callback(&trace, 0, 0, -1);
    trace.stop();
    check(callbacks(prefix.string() + ".jsonl").size() == 6, "stop did not drain final callback");
    fake_callback(&trace, 3072, 3072, -1);
    check(callbacks(prefix.string() + ".jsonl").size() == 6, "stopped trace accepted more data");
    fake_callback(nullptr, 3072, 3072, 0);
}
static void thread_replacement(const std::filesystem::path& prefix) {
    host::AudioCallbackTrace trace(prefix.string());
    std::atomic<bool> first_done{false}, second_done{false};
    std::thread first([&] {
        fake_callback(&trace, 0, 0, -1);
        first_done.store(true, std::memory_order_release);
        while (!second_done.load(std::memory_order_acquire)) std::this_thread::yield();
    });
    std::thread second([&] {
        while (!first_done.load(std::memory_order_acquire)) std::this_thread::yield();
        fake_callback(&trace, 0, 0, -1);
        second_done.store(true, std::memory_order_release);
    });
    first.join(); second.join();
    trace.stop();
    const auto lines = callbacks(prefix.string() + ".jsonl");
    check(lines.size() == 2, "replacement thread record missing");
    check(number(lines[0], "pthread_handle") != number(lines[1], "pthread_handle"), "test reused concurrent pthread handles");
    for (const auto& line : lines)
        check(line.find("\"thread_changed\":true") != std::string::npos && number(line, "policy_result") == 0,
              "replacement thread snapshot missing");
}
int main() {
    const auto root = std::filesystem::temp_directory_path() /
        ("fifa-callback-trace-" + std::to_string(getpid()));
    try {
        std::filesystem::create_directories(root);
        overflow_and_wrap(); concurrent_order(); flush_and_returns(root / "callbacks");
        thread_replacement(root / "replacement");
        std::filesystem::remove_all(root);
        std::cout << "PASS audio callback ordering, overflow, flush and RAII finalization\n";
    } catch (const std::exception& error) {
        std::cerr << "FAIL " << error.what() << " (artifacts: " << root << ")\n"; return 1;
    }
}

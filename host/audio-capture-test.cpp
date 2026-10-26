#include "bridge/spu2/rrv_audio_capture.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <iterator>
#include <unistd.h>

static std::filesystem::path capture_root;

static void check(bool value, const char* what) { if (!value) throw std::runtime_error(what); }
static void armed(rrv_audio_capture& capture) {
    const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (!capture.armed.load(std::memory_order_acquire) && std::chrono::steady_clock::now() < until)
        std::this_thread::yield();
    check(capture.armed.load(std::memory_order_acquire), "background worker did not arm");
}
static std::vector<char> file(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    check(input.good(), "expected capture file missing");
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}
static uint32_t word(const std::vector<char>& bytes, size_t at) {
    uint32_t value; std::memcpy(&value, bytes.data() + at, sizeof(value)); return value;
}
static void wav_matches(const std::string& path, const std::vector<float>& expected) {
    const auto bytes = file(path);
    check(bytes.size() == 56 + expected.size() * sizeof(float), "WAV size mismatch");
    check(word(bytes, 0) == 0x46464952 && word(bytes, 8) == 0x45564157, "RIFF/WAVE missing");
    check(word(bytes, 4) + 8 == bytes.size(), "RIFF length mismatch");
    check(word(bytes, 20) == 0x00020003 && word(bytes, 24) == 48000, "float stereo/rate wrong");
    check(word(bytes, 36) == 0x74636166 && word(bytes, 44) == expected.size() / 2, "fact sample count wrong");
    check(word(bytes, 48) == 0x61746164 && word(bytes, 52) == expected.size() * sizeof(float), "data chunk wrong");
    check(expected.empty() || std::memcmp(bytes.data() + 56, expected.data(), expected.size() * sizeof(float)) == 0,
          "PCM changed or sample order wrong");
}
static std::vector<float> pattern(size_t frames) {
    std::vector<float> pcm(frames * 2);
    for (size_t i = 0; i < frames; ++i) {
        pcm[2 * i] = float(int(i % 997) - 498) / 512.0f;
        pcm[2 * i + 1] = float(int(i % 991) - 495) / 1024.0f;
    }
    return pcm;
}
static void background_complete() {
    const std::string prefix = (capture_root / "full").string();
    const auto pcm = pattern(48000);
    rrv_audio_capture capture(prefix.c_str(), 0, 1);
    armed(capture);
    std::thread producer([&] {
        for (size_t i = 0; i < 48000; ++i)
            capture.mixed(pcm[2 * i], pcm[2 * i + 1], 1000 + i, 100 + i, 80);
    });
    std::thread consumer([&] {
        for (size_t offset = 0; offset < 48000;) {
            const size_t count = std::min<size_t>(317, 48000 - offset);
            capture.output(pcm.data() + 2 * offset, count, count, 2000 + offset, 500 + offset, 200 + offset);
            offset += count;
        }
    });
    producer.join(); consumer.join();
    capture.mixed(100, 100, 99999, 99999, 99999);
    capture.output(pcm.data(), 100, 100, 99999, 99999, 99999);
    // Exercise the background worker completion path while capture is still alive.
    const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (!std::filesystem::exists(prefix + "-anchors.json") && std::chrono::steady_clock::now() < until)
        std::this_thread::yield();
    capture.writer.join();
    wav_matches(prefix + "-producer.wav", pcm);
    wav_matches(prefix + "-callback.wav", pcm);
    const auto bytes = file(prefix + "-anchors.json");
    const std::string json(bytes.begin(), bytes.end());
    check(json.find("\"mixed_frame\":1000") != std::string::npos, "producer anchor wrong");
    check(json.find("\"mixed_frame\":2000") != std::string::npos, "callback anchor wrong");
    check(json.find("\"complete\":true") != std::string::npos, "complete flag missing");
    std::cout << "PASS concurrent known PCM, WAV headers/anchors, flush before destruction\n";
}
static void partial_asymmetric() {
    const std::string prefix = (capture_root / "partial").string();
    const auto pcm = pattern(23);
    std::vector<float> callback(22 * 2, 0);
    std::copy(pcm.begin(), pcm.begin() + 10, callback.begin());
    std::copy(pcm.begin() + 10, pcm.begin() + 24, callback.begin() + 30);
    {
        rrv_audio_capture capture(prefix.c_str(), 0, 1);
        armed(capture);
        // Callback begins before the producer and includes an underrun, an
        // entirely silent pull, then full output from a later ring position.
        capture.output(callback.data(), 10, 5, 10, 20, 15);
        capture.output(callback.data() + 20, 5, 0, 10, 20, 20);
        for (size_t i = 0; i < 23; ++i)
            capture.mixed(pcm[2 * i], pcm[2 * i + 1], 100 + i, 40 + i, 20);
        capture.output(callback.data() + 30, 7, 7, 123, 63, 20);
        // Both single owners have stopped before the destructor requests flush.
    }
    wav_matches(prefix + "-producer.wav", pcm);
    wav_matches(prefix + "-callback.wav", callback);
    const auto bytes = file(prefix + "-anchors.json");
    const std::string json(bytes.begin(), bytes.end());
    check(json.find("\"offset\":0,\"frames\":10,\"real\":5,\"ring_tail\":15") != std::string::npos,
          "partial pull annotation wrong");
    check(json.find("\"offset\":15,\"frames\":7,\"real\":7,\"ring_tail\":20") != std::string::npos,
          "later pull offset wrong");
    check(json.find("\"complete\":false") != std::string::npos, "partial flag missing");
    std::cout << "PASS asymmetric owners/start, short-pull silence, offsets, partial shutdown\n";
}
static void before_arm() {
    const std::string prefix = (capture_root / "not-armed").string();
    {
        rrv_audio_capture capture(prefix.c_str(), 60, 1);
        const float pcm[2] = {1, -1};
        capture.mixed(1, -1, 123, 456, 789);
        capture.output(pcm, 1, 1, 123, 456, 789);
        check(capture.producer.frames == 0 && capture.callback.frames == 0, "recorded before deadline");
    }
    wav_matches(prefix + "-producer.wav", {});
    wav_matches(prefix + "-callback.wav", {});
    std::cout << "PASS delayed arm excludes early samples, empty partial shutdown\n";
}
int main() {
    try {
        capture_root = std::filesystem::temp_directory_path() /
            ("fifa-audio-capture-test-" + std::to_string(getpid()));
        std::filesystem::create_directories(capture_root);
        background_complete(); partial_asymmetric(); before_arm();
        std::filesystem::remove_all(capture_root);
    } catch (const std::exception& error) {
        std::cerr << "FAIL " << error.what() << '\n'; return 1;
    }
}

// SPDX-License-Identifier: GPL-3.0-or-later
#include "diagnostic-input.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cstdint>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace {
constexpr uint32_t kValidKeyMask = (uint32_t{1} << 23) - 2u; // key bits 1 through 18; analog bits 19 through 22
constexpr size_t kMaxMessagesPerPoll = 32;
constexpr auto kMaskLifetime = std::chrono::milliseconds(2500);

std::atomic<uint32_t> s_keyMask{0};
int s_fifo = -1;
std::array<uint8_t, sizeof(uint32_t)> s_partial{};
size_t s_partialSize = 0;
std::chrono::steady_clock::time_point s_lastMessage{};
bool s_hasMessage = false;

void closeFIFO() {
    if (s_fifo >= 0) {
        close(s_fifo);
        s_fifo = -1;
    }
    s_partialSize = 0;
    s_hasMessage = false;
    s_keyMask.store(0, std::memory_order_release);
}

uint32_t decodeLittleEndianMask() {
    const uint32_t value = static_cast<uint32_t>(s_partial[0]) |
        (static_cast<uint32_t>(s_partial[1]) << 8) |
        (static_cast<uint32_t>(s_partial[2]) << 16) |
        (static_cast<uint32_t>(s_partial[3]) << 24);
    return value & kValidKeyMask;
}
}

extern "C" void ps2x_diagnostic_input_configure(const char* path) {
    closeFIFO();
    if (!path || path[0] == '\0') return;

    const int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return;
    struct stat info {};
    if (fstat(fd, &info) != 0 || !S_ISFIFO(info.st_mode)) {
        close(fd);
        return;
    }
    s_fifo = fd;
}

extern "C" void ps2x_diagnostic_input_poll() {
    if (s_fifo < 0) return;

    size_t completed = 0;
    while (completed < kMaxMessagesPerPoll) {
        const ssize_t count = read(s_fifo, s_partial.data() + s_partialSize,
                                   s_partial.size() - s_partialSize);
        if (count > 0) {
            s_partialSize += static_cast<size_t>(count);
            if (s_partialSize == s_partial.size()) {
                s_keyMask.store(decodeLittleEndianMask(), std::memory_order_release);
                s_lastMessage = std::chrono::steady_clock::now();
                s_hasMessage = true;
                s_partialSize = 0;
                ++completed;
            }
            continue;
        }
        if (count < 0 && errno == EINTR) continue;
        break; // EAGAIN, EOF, or another read error; a later poll may retry.
    }

    if (s_hasMessage && std::chrono::steady_clock::now() - s_lastMessage >= kMaskLifetime) {
        s_keyMask.store(0, std::memory_order_release);
        s_hasMessage = false;
    }
}

extern "C" int ps2x_diagnostic_input_key_down(uint32_t key) {
    if (key == 0 || key > 22) return 0;
    return (s_keyMask.load(std::memory_order_acquire) & (uint32_t{1} << key)) != 0;
}

extern "C" void ps2x_diagnostic_input_shutdown() {
    closeFIFO();
}

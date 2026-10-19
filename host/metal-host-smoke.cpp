// SPDX-License-Identifier: GPL-3.0-or-later
#include "metal-host.hpp"

#include <atomic>
#include <array>
#include <cstdint>
#include <iostream>
#include <thread>
#include <cstdio>
#include <cstring>

namespace {
constexpr uint32_t kMetal = 2;
constexpr uint32_t kHardwareGs = 1u << 0;
constexpr uint32_t kMetalBackend = 1u << 1;
constexpr uint32_t kDirectPresent = 1u << 2;
}

int main() {
    if (!ps2x_metal_host_start("PCSX2 Metal GS smoke", 960, 720)) {
        std::cerr << "Cocoa/Metal host surface initialization failed\n";
        return 1;
    }
    std::atomic_bool complete{false};
    std::atomic_int result{1};
    std::thread owner([&] {
        if (!ps2x_metal_gs_create()) result.store(2, std::memory_order_relaxed);
        else {
            uint32_t renderer = 0, flags = 0;
            if (!ps2x_metal_gs_capabilities(&renderer, &flags) || renderer != kMetal ||
                (flags & (kHardwareGs | kMetalBackend | kDirectPresent)) !=
                    (kHardwareGs | kMetalBackend | kDirectPresent)) {
                std::cerr << "PCSX2 GS renderer/capability check failed: kind=" << renderer
                          << " flags=0x" << std::hex << flags << '\n';
                result.store(3, std::memory_order_relaxed);
            }
            // Exercise guest-computed W1C mirroring: ack SIGNAL while FINISH
            // remains set, then ensure a real VSync doesn't restore SIGNAL.
            constexpr uint64_t csr_fixture = 0x00000000551b0003ull;
            if (!ps2x_metal_gs_write_csr(csr_fixture, 0, 0)) {
                std::cerr << "PCSX2 Metal GS rejected CSR fixture publication\n";
                result.store(4, std::memory_order_relaxed);
            }
            uint64_t observed_csr = 0, observed_siglblid = 0;
            if (!ps2x_metal_gs_readback(&observed_csr, &observed_siglblid) ||
                observed_csr != csr_fixture) {
                std::cerr << "PCSX2 Metal GS CSR fixture readback mismatch\n";
                result.store(4, std::memory_order_relaxed);
            }
            constexpr uint64_t csr_after_signal_ack = csr_fixture & ~uint64_t{1};
            if (!ps2x_metal_gs_write_csr(csr_after_signal_ack, 0, 0) ||
                !ps2x_metal_gs_readback(&observed_csr, &observed_siglblid) ||
                observed_csr != csr_after_signal_ack || (observed_csr & 2u) == 0u) {
                std::cerr << "PCSX2 Metal GS CSR W1C mirror did not preserve FINISH\n";
                result.store(4, std::memory_order_relaxed);
            }
            constexpr uint64_t csr_with_write_mask = (uint64_t{3} << 32) | csr_after_signal_ack;
            if (!ps2x_metal_gs_write_csr(csr_with_write_mask, 3, 1) ||
                !ps2x_metal_gs_readback(&observed_csr, &observed_siglblid) ||
                observed_csr != csr_with_write_mask) {
                std::cerr << "PCSX2 Metal GS CSR write-mask half did not reach GSwriteCSR\n";
                result.store(4, std::memory_order_relaxed);
            }
            // Exercise the real bridge's EE-owner VSync return path before
            // teardown; zeroed privileged registers are valid idle state.
            std::array<uint64_t, 19> idle_regs{};
            idle_regs[15] = csr_with_write_mask;
            if (!ps2x_metal_gs_vsync(idle_regs.data(), 1, 0)) {
                std::cerr << "PCSX2 Metal GS rejected the first idle VSync\n";
                result.store(4, std::memory_order_relaxed);
            } else if (!ps2x_metal_gs_readback(&observed_csr, &observed_siglblid) ||
                       observed_csr != csr_with_write_mask) {
                std::cerr << "PCSX2 Metal GS VSync resurrected acknowledged CSR status\n";
                result.store(4, std::memory_order_relaxed);
            }
            // Verify a VIF1 DIRECT split against the real staged PCSX2 GS: the
            // second submit continues the open IMAGE tag with raw payload only.
            constexpr uint64_t upload_bitbltbuf = uint64_t{1} << 48; // DBW=1, PSMCT32, DBP=0
            constexpr uint64_t upload_trxreg = uint64_t{8} | (uint64_t{1} << 32);
            constexpr uint64_t upload_trxpos = 0;
            const std::array<uint64_t, 10> upload_setup{
                uint64_t{4} | (uint64_t{1} << 15) | (uint64_t{1} << 60), 0xE,
                upload_bitbltbuf, 0x50, upload_trxpos, 0x51, upload_trxreg, 0x52,
                0, 0x53};
            const uint64_t image_tag = uint64_t{2} | (uint64_t{1} << 15) |
                                       (uint64_t{2} << 58);
            const std::array<uint64_t, 4> image_first{
                image_tag, 0,
                0x44332211u | (uint64_t{0x88776655u} << 32),
                0xCCBBAA99u | (uint64_t{0x00FFEEDD} << 32)};
            const std::array<uint64_t, 2> image_continuation{
                0x04030201u | (uint64_t{0x08070605u} << 32),
                0x0C0B0A09u | (uint64_t{0x100F0E0Du} << 32)};
            if (!ps2x_metal_gs_submit(3, reinterpret_cast<const uint8_t*>(upload_setup.data()),
                                        sizeof(upload_setup)) ||
                !ps2x_metal_gs_submit(3, reinterpret_cast<const uint8_t*>(image_first.data()),
                                        sizeof(image_first)) ||
                !ps2x_metal_gs_submit(3, reinterpret_cast<const uint8_t*>(image_continuation.data()),
                                        sizeof(image_continuation))) {
                std::cerr << "PCSX2 Metal GS rejected the split-IMAGE continuation fixture\n";
                result.store(7, std::memory_order_relaxed);
            } else {
                // Reset parser/cursor state without discarding GS local memory,
                // then read back the completed upload through the real FIFO.
                if (!ps2x_metal_gs_reset()) {
                    std::cerr << "PCSX2 Metal GS reset after split-IMAGE upload failed\n";
                    result.store(7, std::memory_order_relaxed);
                }
                const std::array<uint64_t, 10> download_setup{
                    uint64_t{4} | (uint64_t{1} << 15) | (uint64_t{1} << 60), 0xE,
                    uint64_t{1} << 16, 0x50, upload_trxpos, 0x51, upload_trxreg, 0x52,
                    1, 0x53};
                const std::array<uint32_t, 8> expected_pixels{
                    0x44332211u, 0x88776655u, 0xCCBBAA99u, 0x00FFEEDDu,
                    0x04030201u, 0x08070605u, 0x0C0B0A09u, 0x100F0E0Du};
                if (!ps2x_metal_gs_submit(3, reinterpret_cast<const uint8_t*>(download_setup.data()),
                                            sizeof(download_setup))) {
                    std::cerr << "PCSX2 Metal GS rejected split-IMAGE readback setup\n";
                    result.store(7, std::memory_order_relaxed);
                } else {
                    std::array<uint8_t, 32> pixels{};
                    if (!ps2x_metal_gs_read_local_memory(pixels.data(), pixels.size(),
                            uint64_t{1} << 16, upload_trxpos, upload_trxreg) ||
                        std::memcmp(pixels.data(), expected_pixels.data(), pixels.size()) != 0) {
                        std::cerr << "PCSX2 Metal GS split-IMAGE continuation pixels differ from fixture\n";
                        for (uint8_t byte : pixels)
                            std::cerr << std::hex << static_cast<unsigned>(byte) << ' ';
                        std::cerr << std::dec << '\n';
                        result.store(7, std::memory_order_relaxed);
                    }
                }
            }
            // Mirror sceGsExecStoreImage's packed A+D setup for a 1x1 CT32
            // local-to-host read, then consume the pending native GS FIFO via
            // the same ABI wrapper used by the guest syscall.
            constexpr uint64_t bitbltbuf = (uint64_t{1} << 16) | (uint64_t{1} << 48);
            constexpr uint64_t trxpos = 0;
            constexpr uint64_t trxreg = uint64_t{1} | (uint64_t{1} << 32);
            const std::array<uint64_t, 10> read_setup{
                uint64_t{4} | (uint64_t{1} << 15) | (uint64_t{1} << 60), 0xE,
                bitbltbuf, 0x50, trxpos, 0x51, trxreg, 0x52, 1, 0x53};
            if (!ps2x_metal_gs_submit(3, reinterpret_cast<const uint8_t*>(read_setup.data()),
                                        sizeof(read_setup))) {
                std::cerr << "PCSX2 Metal GS rejected local-to-host setup GIF\n";
                result.store(5, std::memory_order_relaxed);
            } else {
                std::array<uint8_t, 4> pixel{};
                if (!ps2x_metal_gs_read_local_memory(pixel.data(), pixel.size(),
                                                       bitbltbuf, trxpos, trxreg)) {
                    std::cerr << "PCSX2 Metal GS local-to-host FIFO read failed\n";
                    result.store(6, std::memory_order_relaxed);
                }
            }
            ps2x_metal_gs_destroy();
        }
        std::fprintf(stderr, "smoke: GS owner-thread destroy complete\n");
        complete.store(true, std::memory_order_release);
    });
    while (!complete.load(std::memory_order_acquire))
        ps2x_metal_host_poll();
    if (owner.joinable()) owner.join();
    std::fprintf(stderr, "smoke: owner thread joined\n");
    ps2x_metal_host_stop();
    std::fprintf(stderr, "smoke: Cocoa host stop complete\n");
    if (result.load(std::memory_order_relaxed) != 1) return result.load(std::memory_order_relaxed);
    std::cout << "Created the PCSX2 hardware Metal GS, accepted an owner-thread VSync and local-to-host transfer, and destroyed it cleanly.\n";
    return 0;
}

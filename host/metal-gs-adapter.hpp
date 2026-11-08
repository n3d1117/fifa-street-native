// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "rrv_pcsx2_gs_bridge.h"

#include <array>
#include <cstdint>
#include <span>
#include <string>

namespace host {

// Thin owner of a PCSX2 GS bridge handle. The NSView and CAMetalLayer are
// opaque, borrowed handles: the caller must keep both alive through close().
// Create/resize must run on the AppKit main thread. Serialize every operation
// on this object on one thread; the adapter intentionally adds no locks.
struct MetalGsSurface {
    void* ns_view{};
    void* metal_layer{};
    uint32_t width_pixels{};
    uint32_t height_pixels{};
    float backing_scale{1.0F};
};

class MetalGsAdapter final {
public:
    static MetalGsAdapter create(const MetalGsSurface& surface);
    ~MetalGsAdapter() noexcept;

    MetalGsAdapter(const MetalGsAdapter&) = delete;
    MetalGsAdapter& operator=(const MetalGsAdapter&) = delete;
    MetalGsAdapter(MetalGsAdapter&& other) noexcept;
    MetalGsAdapter& operator=(MetalGsAdapter&& other) noexcept;

    void submit(uint32_t original_gif_path, std::span<const uint8_t> packet);
    void reset();
    void write_csr(uint64_t authoritative_csr, uint32_t high_word_write_mask,
                   bool write_high_word);
    void vsync(const std::array<uint64_t, RRV_PCSX2_GS_BRIDGE_PRIV_REG_COUNT>& regs,
               uint64_t field_index, uint32_t field_parity);
    struct Readback { uint64_t csr; uint64_t siglblid; };
    Readback readback();
    uint32_t read_fifo(std::span<uint8_t> destination);
    void read_local_memory(std::span<uint8_t> destination, uint64_t bitbltbuf,
                           uint64_t trxpos, uint64_t trxreg);
    void resize(uint32_t width_pixels, uint32_t height_pixels, float backing_scale);
    const RrvPcsx2GsBridgeCapabilities& capabilities() const noexcept { return capabilities_; }
    void close() noexcept;

private:
    MetalGsAdapter(RrvPcsx2GsBridge* handle, RrvPcsx2GsBridgeCapabilities capabilities) noexcept;
    RrvPcsx2GsBridge* handle_{};
    RrvPcsx2GsBridgeCapabilities capabilities_{};
};

} // namespace host

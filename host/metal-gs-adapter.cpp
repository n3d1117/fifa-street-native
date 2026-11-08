// SPDX-License-Identifier: GPL-3.0-or-later
#include "metal-gs-adapter.hpp"
#include "runtime/ps2_host_present.h"

#include <cmath>
#include <stdexcept>
#include <string>

extern "C" int rrv_pcsx2_gs_bridge_write_csr(
    RrvPcsx2GsBridge*, uint64_t, uint32_t, int, char*, uint32_t);

namespace host {
namespace {
constexpr uint32_t kRequiredCapabilities = RRV_PCSX2_GS_CAP_HARDWARE_GS |
    RRV_PCSX2_GS_CAP_METAL_BACKEND | RRV_PCSX2_GS_CAP_DIRECT_PRESENT;

void host_present_changed(int active) {
    ps2x::hostPresentInProgress.store(active != 0, std::memory_order_release);
}

[[noreturn]] void fail(const char* operation, const char* detail) {
    throw std::runtime_error(std::string("PCSX2 Metal GS ") + operation + ": " +
                             (detail && *detail ? detail : "bridge call failed without detail"));
}
void check(int result, const char* operation, const char* error) {
    // The bridge C ABI uses boolean results: 1 succeeds, 0 fails.
    if (result == 0) fail(operation, error);
}
void validate_dimensions(uint32_t width, uint32_t height, float scale) {
    if (!width || !height || !std::isfinite(scale) || scale <= 0.0F)
        throw std::invalid_argument("PCSX2 Metal GS surface requires nonzero dimensions and positive finite backing scale");
}


}

MetalGsAdapter::MetalGsAdapter(RrvPcsx2GsBridge* handle,
                               RrvPcsx2GsBridgeCapabilities capabilities) noexcept
    : handle_(handle), capabilities_(capabilities) {}

MetalGsAdapter MetalGsAdapter::create(const MetalGsSurface& surface) {
    if (!surface.ns_view || !surface.metal_layer)
        throw std::invalid_argument("PCSX2 Metal GS requires borrowed NSView and CAMetalLayer handles");
    validate_dimensions(surface.width_pixels, surface.height_pixels, surface.backing_scale);

    RrvPcsx2GsBridgeConfig config{};
    config.struct_size = sizeof(config);
    config.presentation_mode = RRV_PCSX2_GS_PRESENTATION_DIRECT_GPU;
    config.renderer_kind = RRV_PCSX2_GS_RENDERER_METAL;
    config.surface.struct_size = sizeof(config.surface);
    config.surface.kind = RRV_PCSX2_GS_SURFACE_MACOS_VIEW_METAL_LAYER;
    config.surface.flags = RRV_PCSX2_GS_SURFACE_FLAG_CALLER_OWNS_HANDLES |
                           RRV_PCSX2_GS_SURFACE_FLAG_MAIN_THREAD_PREPARED;
    config.surface.native_view = surface.ns_view;
    config.surface.native_layer = surface.metal_layer;
    config.surface.width_pixels = surface.width_pixels;
    config.surface.height_pixels = surface.height_pixels;
    config.surface.backing_scale = surface.backing_scale;

    config.surface.flags |= RRV_PCSX2_GS_SURFACE_FLAG_OWNER_THREAD;
    char error[1024]{};
    rrv_pcsx2_gs_bridge_owner_thread_init();
    rrv_pcsx2_gs_bridge_set_host_present_callback(host_present_changed);

    RrvPcsx2GsBridgeCapabilities caps{};
    caps.struct_size = sizeof(caps);
    error[0] = '\0';
    auto* handle = rrv_pcsx2_gs_bridge_create(&config, &caps, error, sizeof(error));
    const auto flags = caps.capability_flags;
    if (!handle) fail("create", error);
    if (caps.renderer_kind != RRV_PCSX2_GS_RENDERER_METAL ||
        (flags & kRequiredCapabilities) != kRequiredCapabilities) {
        rrv_pcsx2_gs_bridge_destroy(handle);
        throw std::runtime_error("PCSX2 Metal GS create rejected backend: actual renderer/capabilities do not prove hardware GS, Metal, and direct GPU presentation");
    }
    return MetalGsAdapter(handle, caps);
}

MetalGsAdapter::~MetalGsAdapter() noexcept { close(); }
MetalGsAdapter::MetalGsAdapter(MetalGsAdapter&& other) noexcept
    : handle_(other.handle_), capabilities_(other.capabilities_) { other.handle_ = nullptr; }
MetalGsAdapter& MetalGsAdapter::operator=(MetalGsAdapter&& other) noexcept {
    if (this != &other) { close(); handle_ = other.handle_; capabilities_ = other.capabilities_; other.handle_ = nullptr; }
    return *this;
}
void MetalGsAdapter::close() noexcept {
    if (handle_) {
        rrv_pcsx2_gs_bridge_destroy(handle_);
        handle_ = nullptr;
        rrv_pcsx2_gs_bridge_set_host_present_callback(nullptr);
    }
}
void MetalGsAdapter::submit(uint32_t path, std::span<const uint8_t> packet) {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS submit after close");
    if (path < 1 || path > 3) throw std::invalid_argument("GIF path must be the original path id 1, 2, or 3");
    if (packet.size() > UINT32_MAX) throw std::length_error("GIF packet exceeds bridge ABI size limit");
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_submit(handle_, path, packet.data(),
                                     static_cast<uint32_t>(packet.size()), error, sizeof(error)),
          "submit", error);
}
void MetalGsAdapter::vsync(const std::array<uint64_t, RRV_PCSX2_GS_BRIDGE_PRIV_REG_COUNT>& regs,
                           uint64_t field_index, uint32_t parity) {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS vsync after close");
    if (parity > 1) throw std::invalid_argument("GS field parity must be 0 or 1");
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_vsync(handle_, regs.data(), field_index, parity, error, sizeof(error)),
          "vsync", error);
}

void MetalGsAdapter::reset() {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS reset after close");
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_reset_gs(handle_, error, sizeof(error)), "reset", error);
}
void MetalGsAdapter::write_csr(uint64_t authoritative_csr, uint32_t high_word_write_mask,
                               bool write_high_word) {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS CSR synchronization after close");
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_write_csr(handle_, authoritative_csr,
          high_word_write_mask, write_high_word ? 1 : 0, error, sizeof(error)),
          "CSR synchronization", error);
}
MetalGsAdapter::Readback MetalGsAdapter::readback() {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS readback after close");
    Readback result{};
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_readback(handle_, &result.csr, &result.siglblid, error, sizeof(error)),
          "readback", error);
    return result;
}
uint32_t MetalGsAdapter::read_fifo(std::span<uint8_t> dst) {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS FIFO read after close");
    if (dst.size() > UINT32_MAX) throw std::length_error("FIFO read exceeds bridge ABI size limit");
    return rrv_pcsx2_gs_bridge_read_fifo(handle_, dst.data(), static_cast<uint32_t>(dst.size()));
}
void MetalGsAdapter::read_local_memory(std::span<uint8_t> dst, uint64_t bitbltbuf,
                                       uint64_t trxpos, uint64_t trxreg) {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS local-memory read after close");
    if (dst.size() > UINT32_MAX) throw std::length_error("local-memory read exceeds bridge ABI size limit");
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_read_local_memory(handle_, dst.data(), static_cast<uint32_t>(dst.size()),
          bitbltbuf, trxpos, trxreg, error, sizeof(error)), "local-memory read", error);
}
void MetalGsAdapter::resize(uint32_t width, uint32_t height, float scale) {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS resize after close");
    validate_dimensions(width, height, scale);
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_resize(handle_, width, height, scale, error, sizeof(error)), "resize", error);
}

} // namespace host

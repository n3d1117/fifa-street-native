// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
// GS calls shared by metal-host.mm (AppKit) and ios-host.mm (UIKit). Included
// after the host's statics: s_view, s_layer, s_surface, s_lifecycle_pending and
// the s_resize_* atomics. Nothing here touches AppKit or UIKit types.

namespace {
thread_local std::unique_ptr<host::MetalGsAdapter> s_gs;

void apply_pending_surface_resize() {
    if (!s_gs) return;
    static thread_local uint64_t applied_generation = 0;
    const uint64_t generation = s_resize_generation.load(std::memory_order_acquire);
    if (generation == applied_generation) return;
    const uint32_t width = s_resize_width.load(std::memory_order_relaxed);
    const uint32_t height = s_resize_height.load(std::memory_order_relaxed);
    const float scale = s_resize_scale.load(std::memory_order_relaxed);
    try {
        s_gs->resize(width, height, scale);
        applied_generation = generation;
    } catch (const std::exception& e) {
        NSLog(@"Metal GS resize failed: %s", e.what());
    }
}
}

extern "C" int ps2x_metal_gs_create(void) {
    @autoreleasepool {
        if (!s_view || !s_layer || s_gs) return 0;
        host::MetalGsSurface surface{};
        surface.ns_view = (__bridge void*)s_view; surface.metal_layer = (__bridge void*)s_layer;
        surface.width_pixels = s_surface.width_pixels; surface.height_pixels = s_surface.height_pixels;
        surface.backing_scale = s_surface.backing_scale;
        s_lifecycle_pending.store(true, std::memory_order_release);
        try { s_gs = std::make_unique<host::MetalGsAdapter>(host::MetalGsAdapter::create(surface)); }
        catch (const std::exception& e) {
            s_lifecycle_pending.store(false, std::memory_order_release);
            NSLog(@"Metal GS create failed: %s", e.what()); return 0;
        }
        s_lifecycle_pending.store(false, std::memory_order_release);
        return 1;
    }
}
extern "C" void ps2x_metal_gs_destroy(void) {
    if (!s_gs) return;
    s_lifecycle_pending.store(true, std::memory_order_release);
    s_gs.reset();
    s_lifecycle_pending.store(false, std::memory_order_release);
}
extern "C" int ps2x_metal_gs_submit(uint32_t path, const uint8_t* bytes, uint32_t size) {
    if (!s_gs || (!bytes && size)) return 0;
    apply_pending_surface_resize();
    try { s_gs->submit(path, std::span<const uint8_t>(bytes, size)); return 1; }
    catch (const std::exception& e) { NSLog(@"Metal GIF submit failed: %s", e.what()); return 0; }
}
extern "C" int ps2x_metal_gs_reset(void) {
    if (!s_gs) return 0;
    apply_pending_surface_resize();
    try { s_gs->reset(); return 1; }
    catch (const std::exception& e) { NSLog(@"Metal GS reset failed: %s", e.what()); return 0; }
}
extern "C" int ps2x_metal_gs_write_csr(uint64_t authoritative_csr,
                                          uint32_t high_word_write_mask,
                                          int write_high_word) {
    if (!s_gs) return 0;
    apply_pending_surface_resize();
    try {
        s_gs->write_csr(authoritative_csr, high_word_write_mask, write_high_word != 0);
        return 1;
    } catch (const std::exception& e) {
        NSLog(@"Metal GS CSR synchronization failed: %s", e.what());
        return 0;
    }
}
extern "C" int ps2x_metal_gs_vsync(const uint64_t* regs, uint64_t tick, uint32_t parity) {
    if (!s_gs || !regs) return 0;
    apply_pending_surface_resize();
    std::array<uint64_t, RRV_PCSX2_GS_BRIDGE_PRIV_REG_COUNT> copy{};
    std::copy_n(regs, copy.size(), copy.begin());
    try { s_gs->vsync(copy, tick, parity); return 1; }
    catch (const std::exception& e) { NSLog(@"Metal GS vsync failed: %s", e.what()); return 0; }
}
extern "C" int ps2x_metal_gs_readback(uint64_t* csr, uint64_t* siglblid) {
    if (!s_gs || !csr || !siglblid) return 0;
    apply_pending_surface_resize();
    try { const auto r=s_gs->readback(); *csr=r.csr; *siglblid=r.siglblid; return 1; }
    catch (const std::exception& e) { NSLog(@"Metal GS readback failed: %s", e.what()); return 0; }
}
extern "C" uint32_t ps2x_metal_gs_read_fifo(uint8_t* dst, uint32_t byte_count) {
    if (!s_gs || !dst || byte_count == 0) return 0;
    return s_gs->read_fifo(std::span<uint8_t>(dst, byte_count));
}
extern "C" int ps2x_metal_gs_read_local_memory(uint8_t* dst, uint32_t byte_count,
                                                  uint64_t bitbltbuf, uint64_t trxpos,
                                                  uint64_t trxreg) {
    if (!s_gs || !dst || byte_count == 0) return 0;
    try {
        s_gs->read_local_memory(std::span<uint8_t>(dst, byte_count), bitbltbuf, trxpos, trxreg);
        return 1;
    } catch (const std::exception& e) {
        NSLog(@"Metal GS local-to-host read failed: %s", e.what());
        return 0;
    }
}
extern "C" int ps2x_metal_gs_capabilities(uint32_t* renderer, uint32_t* flags) {
    if (!s_gs || !renderer || !flags) return 0;
    *renderer = s_gs->capabilities().renderer_kind;
    *flags = s_gs->capabilities().capability_flags;
    return 1;
}

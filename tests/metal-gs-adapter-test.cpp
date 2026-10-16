// SPDX-License-Identifier: GPL-3.0-or-later
#include "metal-gs-adapter.hpp"

#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <stdexcept>

struct RrvPcsx2GsBridge { int marker; };
namespace {
RrvPcsx2GsBridge g_handle{42};
RrvPcsx2GsBridgeConfig g_config{};
uint32_t g_create_flags = RRV_PCSX2_GS_CAP_HARDWARE_GS | RRV_PCSX2_GS_CAP_METAL_BACKEND | RRV_PCSX2_GS_CAP_DIRECT_PRESENT;
uint32_t g_create_renderer = RRV_PCSX2_GS_RENDERER_METAL;
uint32_t g_path = 0, g_submits = 0, g_vsyncs = 0, g_readbacks = 0, g_destroys = 0, g_owner_inits = 0;
uint64_t g_field = 0;
uint32_t g_parity = 0;
bool g_fail_vsync = false;
bool g_fail_local_read = false;
uint64_t g_read_bitbltbuf = 0, g_read_trxpos = 0, g_read_trxreg = 0;
uint32_t g_read_bytes = 0;
uint64_t g_written_csr = 0;
uint32_t g_csr_write_mask = 0, g_csr_write_high = 0;
std::array<uint64_t, RRV_PCSX2_GS_BRIDGE_PRIV_REG_COUNT> g_regs{};
void reset_fake() {
    g_config = {}; g_create_flags = RRV_PCSX2_GS_CAP_HARDWARE_GS | RRV_PCSX2_GS_CAP_METAL_BACKEND | RRV_PCSX2_GS_CAP_DIRECT_PRESENT;
    g_create_renderer = RRV_PCSX2_GS_RENDERER_METAL;
    g_path = g_submits = g_vsyncs = g_readbacks = g_destroys = g_owner_inits = 0;
    g_fail_vsync = false;
    g_fail_local_read = false;
    g_read_bitbltbuf = g_read_trxpos = g_read_trxreg = 0;
    g_read_bytes = 0;
    g_written_csr = 0;
    g_csr_write_mask = g_csr_write_high = 0;
}
}

extern "C" {
void rrv_pcsx2_gs_bridge_owner_thread_init(void) { ++g_owner_inits; }
RrvPcsx2GsBridge* rrv_pcsx2_gs_bridge_create(const RrvPcsx2GsBridgeConfig* c,
    RrvPcsx2GsBridgeCapabilities* caps, char*, uint32_t) {
    g_config = *c;
    caps->renderer_kind = g_create_renderer; caps->capability_flags = g_create_flags;
    caps->renderer_name = g_create_renderer == RRV_PCSX2_GS_RENDERER_METAL ? "fake-metal" : "fake-software";
    return &g_handle;
}
void rrv_pcsx2_gs_bridge_destroy(RrvPcsx2GsBridge*) { ++g_destroys; }
int rrv_pcsx2_gs_bridge_submit(RrvPcsx2GsBridge*, uint32_t path, const uint8_t*, uint32_t, char*, uint32_t) { g_path = path; ++g_submits; return 1; }
int rrv_pcsx2_gs_bridge_vsync(RrvPcsx2GsBridge*, const RrvPcsx2GsPrivRegs regs, uint64_t field, uint32_t parity, char* error, uint32_t error_size) {
    ++g_vsyncs;
    if (g_fail_vsync) {
        if (error && error_size) std::snprintf(error, error_size, "synthetic VSync failure");
        return 0;
    }
    std::memcpy(g_regs.data(), regs, sizeof(uint64_t) * RRV_PCSX2_GS_BRIDGE_PRIV_REG_COUNT); g_field = field; g_parity = parity; return 1;
}
int rrv_pcsx2_gs_bridge_reset_gs(RrvPcsx2GsBridge*, char*, uint32_t) { return 1; }
int rrv_pcsx2_gs_bridge_write_csr(RrvPcsx2GsBridge*, uint64_t csr, uint32_t mask,
                                  int write_high, char*, uint32_t) {
    g_written_csr = csr; g_csr_write_mask = mask; g_csr_write_high = write_high ? 1u : 0u;
    return 1;
}
int rrv_pcsx2_gs_bridge_readback(RrvPcsx2GsBridge*, uint64_t* csr, uint64_t* sig, char*, uint32_t) { ++g_readbacks; *csr = 11; *sig = 22; return 1; }
int rrv_pcsx2_gs_bridge_read_local_memory(RrvPcsx2GsBridge*, uint8_t* dst, uint32_t bytes,
    uint64_t bitbltbuf, uint64_t trxpos, uint64_t trxreg, char* error, uint32_t error_size) {
    g_read_bytes = bytes; g_read_bitbltbuf = bitbltbuf; g_read_trxpos = trxpos; g_read_trxreg = trxreg;
    if (g_fail_local_read) {
        if (error && error_size) std::snprintf(error, error_size, "synthetic local read failure");
        return 0;
    }
    if (dst && bytes) std::memset(dst, 0x5a, bytes);
    return 1;
}
int rrv_pcsx2_gs_bridge_snapshot_local_memory(RrvPcsx2GsBridge*, uint8_t*, uint32_t, char*, uint32_t) { return 1; }
int rrv_pcsx2_gs_bridge_resize(RrvPcsx2GsBridge*, uint32_t, uint32_t, float, char*, uint32_t) { return 1; }
int rrv_pcsx2_gs_bridge_get_stats(RrvPcsx2GsBridge*, RrvPcsx2GsBridgeStats* s, char*, uint32_t) {
    s->direct_gpu_presents = 9; s->synchronous_cpu_readbacks = 0; s->unexpected_readbacks = 0; return 1;
}
}

int main() {
    reset_fake();
    int view = 0, layer = 0;
    const tarzan::ps2::MetalGsSurface surface{&view, &layer, 1280, 720, 2.0F};
    {
        auto adapter = tarzan::ps2::MetalGsAdapter::create(surface);
        assert(g_owner_inits == 1);
        assert(g_config.renderer_kind == RRV_PCSX2_GS_RENDERER_METAL);
        assert(g_config.presentation_mode == RRV_PCSX2_GS_PRESENTATION_DIRECT_GPU);
        assert(g_config.surface.kind == RRV_PCSX2_GS_SURFACE_MACOS_VIEW_METAL_LAYER);
        assert(g_config.surface.flags == (RRV_PCSX2_GS_SURFACE_FLAG_CALLER_OWNS_HANDLES | RRV_PCSX2_GS_SURFACE_FLAG_MAIN_THREAD_PREPARED | RRV_PCSX2_GS_SURFACE_FLAG_OWNER_THREAD));
        assert(g_config.surface.native_view == &view && g_config.surface.native_layer == &layer);
        assert(g_config.surface.width_pixels == 1280 && g_config.surface.height_pixels == 720 && g_config.surface.backing_scale == 2.0F);
        const std::array<uint8_t, 8> packet{1,2,3,4,5,6,7,8};
        adapter.submit(3, packet);
        assert(g_submits == 1 && g_path == 3);
        std::array<uint64_t, RRV_PCSX2_GS_BRIDGE_PRIV_REG_COUNT> regs{};
        regs[0] = 0xabc;
        adapter.vsync(regs, 123, 1);
        assert(g_vsyncs == 1 && g_field == 123 && g_parity == 1 && g_regs[0] == 0xabc);
        assert(g_readbacks == 0); // presenting a field does not request CPU readback
        const auto stat = adapter.stats();
        assert(stat.direct_gpu_presents == 9 && stat.synchronous_cpu_readbacks == 0 && stat.unexpected_readbacks == 0);
        assert(g_readbacks == 0);
        const auto state = adapter.readback();
        assert(state.csr == 11 && state.siglblid == 22 && g_readbacks == 1);
        adapter.write_csr(0x12345678551b6002ull, 0x12345678, true);
        assert(g_written_csr == 0x12345678551b6002ull &&
               g_csr_write_mask == 0x12345678 && g_csr_write_high == 1);
        std::array<uint8_t, 4> local{};
        adapter.read_local_memory(local, 0x11, 0x22, 0x33);
        assert((local == std::array<uint8_t, 4>{0x5a, 0x5a, 0x5a, 0x5a}));
        assert(g_read_bytes == local.size() && g_read_bitbltbuf == 0x11 &&
               g_read_trxpos == 0x22 && g_read_trxreg == 0x33);
        adapter.close();
    }
    assert(g_destroys == 1);

    reset_fake();
    {
        auto adapter = tarzan::ps2::MetalGsAdapter::create(surface);
        std::array<uint8_t, 4> local{};
        g_fail_local_read = true;
        bool diagnostic_reported = false;
        try { adapter.read_local_memory(local, 1, 2, 3); }
        catch (const std::runtime_error& e) {
            diagnostic_reported = std::string(e.what()).find("synthetic local read failure") != std::string::npos;
        }
        assert(diagnostic_reported);
    }

    reset_fake();
    {
        auto adapter = tarzan::ps2::MetalGsAdapter::create(surface);
        g_fail_vsync = true;
        bool diagnostic_reported = false;
        try { adapter.vsync({}, 1, 0); }
        catch (const std::runtime_error& e) {
            diagnostic_reported = std::string(e.what()).find("synthetic VSync failure") != std::string::npos;
        }
        assert(diagnostic_reported);
    }

    for (const auto bad : {RRV_PCSX2_GS_CAP_HARDWARE_GS | RRV_PCSX2_GS_CAP_METAL_BACKEND,
                           RRV_PCSX2_GS_CAP_HARDWARE_GS | RRV_PCSX2_GS_CAP_DIRECT_PRESENT,
                           RRV_PCSX2_GS_CAP_METAL_BACKEND | RRV_PCSX2_GS_CAP_DIRECT_PRESENT}) {
        reset_fake(); g_create_flags = bad;
        bool rejected = false;
        try { auto adapter = tarzan::ps2::MetalGsAdapter::create(surface); }
        catch (const std::runtime_error&) { rejected = true; }
        assert(rejected && g_destroys == 1);
    }
    reset_fake(); g_create_renderer = RRV_PCSX2_GS_RENDERER_SOFTWARE;
    bool software_rejected = false;
    try { auto adapter = tarzan::ps2::MetalGsAdapter::create(surface); }
    catch (const std::runtime_error&) { software_rejected = true; }
    assert(software_rejected && g_destroys == 1);

    reset_fake();
    auto adapter = tarzan::ps2::MetalGsAdapter::create(surface);
    bool invalid_path = false;
    try { adapter.submit(0, {}); } catch (const std::invalid_argument&) { invalid_path = true; }
    assert(invalid_path && g_submits == 0);
    std::cout << "metal-gs-adapter tests passed\n";
}

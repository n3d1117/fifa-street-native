// SPDX-License-Identifier: GPL-3.0-or-later
#include "metal-gs-adapter.hpp"
#include "experimental-settings.hpp"

#include <cmath>
#include <charconv>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

extern "C" int rrv_pcsx2_gs_bridge_write_csr(
    RrvPcsx2GsBridge*, uint64_t, uint32_t, int, char*, uint32_t);

namespace tarzan::ps2 {
namespace {
constexpr uint32_t kRequiredCapabilities = RRV_PCSX2_GS_CAP_HARDWARE_GS |
    RRV_PCSX2_GS_CAP_METAL_BACKEND | RRV_PCSX2_GS_CAP_DIRECT_PRESENT;

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

struct CaptureSession {
    bool initialized{};
    bool enabled{};
    std::filesystem::path directory;
    uint32_t max_frames{};
    uint32_t completed_frames{};
    uint64_t last_captured_present_index{};
    uint64_t start_field{};
    uint64_t interval{1};
    std::filesystem::path trigger_file;
};

// VSync and capture_after_vsync run synchronously on the EE/GS owner thread.
// Keep the exact guest snapshot passed to the renderer available to capture
// metadata without another bridge call or changing the adapter ABI.
thread_local std::array<uint64_t, RRV_PCSX2_GS_BRIDGE_PRIV_REG_COUNT> capture_vsync_regs{};
// Opt-in raw trace for the short MENU transition window. Each accepted record
// is: "TGMG", uint64 field, uint32 original GIF path, uint32 byte count, bytes.
// This runs on the serialized GS owner and is deliberately bounded.
struct MenuGifTrace {
    bool initialized{};
    bool enabled{};
    bool warned{};
    uint64_t field{};
    uint32_t packets{};
    uint64_t bytes{};
    uint64_t firstField{1800u};
    uint64_t lastField{2200u};
    uint32_t packetLimit{256u};
    uint64_t byteLimit{4u * 1024u * 1024u};
    const char* trigger{};
    bool armed{};
    std::ofstream output;
};
thread_local MenuGifTrace menu_gif_trace{};
constexpr uint64_t kMenuTraceFirstField = 1800;
constexpr uint64_t kMenuTraceLastField = 2200;
constexpr uint32_t kMenuTraceMaxPackets = 256;
constexpr uint64_t kMenuTraceMaxBytes = 4u * 1024u * 1024u;

template <typename T>
void write_trace_scalar(std::ofstream& out, T value) {
    out.write(reinterpret_cast<const char*>(&value), sizeof(value));
}

void trace_menu_gif_submit(uint32_t path, std::span<const uint8_t> packet) {
    MenuGifTrace& trace = menu_gif_trace;
    if (!trace.initialized) {
        trace.initialized = true;
        const char* destination = std::getenv("TARZAN_TRACE_MENU_GIF");
        if (destination && destination[0] != '\0') {
            const std::filesystem::path file(destination);
            if (!file.is_absolute())
                throw std::invalid_argument("TARZAN_TRACE_MENU_GIF must be an absolute output file path");
            trace.output.open(file, std::ios::binary | std::ios::trunc);
            if (!trace.output)
                throw std::runtime_error("cannot create TARZAN_TRACE_MENU_GIF: " + file.string());
            trace.output.write("TGMG", 4);
            trace.enabled = true;
            trace.trigger = std::getenv("TARZAN_TRACE_GIF_TRIGGER");
            if (trace.trigger && *trace.trigger) {
                trace.firstField = UINT64_MAX;
                trace.lastField = UINT64_MAX;
                trace.packetLimit = 8192u;
                trace.byteLimit = 64u * 1024u * 1024u;
            }
            std::clog << "Metal raw GIF trace enabled: " << file
                      << " fields=" << kMenuTraceFirstField << ".." << kMenuTraceLastField
                      << " max_packets=" << kMenuTraceMaxPackets
                      << " max_bytes=" << kMenuTraceMaxBytes << '\n';
        }
    }
    if (trace.enabled && trace.trigger && *trace.trigger && !trace.armed) {
        std::error_code ec;
        if (std::filesystem::exists(trace.trigger, ec)) {
            std::filesystem::remove(trace.trigger, ec);
            trace.armed = true;
            trace.firstField = trace.field;
            trace.lastField = trace.field + 20u;
            std::clog << "Metal gameplay GIF capture armed at field " << trace.field << '\n';
        }
    }
    if (!trace.enabled || trace.field < trace.firstField ||
        trace.field > trace.lastField || trace.packets >= trace.packetLimit)
        return;
    constexpr uint64_t record_bytes = sizeof(uint64_t) + 2u * sizeof(uint32_t);
    if (trace.bytes > trace.byteLimit ||
        record_bytes > trace.byteLimit - trace.bytes ||
        packet.size() > trace.byteLimit - trace.bytes - record_bytes) {
        trace.enabled = false;
        if (!trace.warned) {
            trace.warned = true;
            std::clog << "Metal raw GIF trace reached its configured byte bound\n";
        }
        return;
    }
    write_trace_scalar(trace.output, trace.field);
    write_trace_scalar(trace.output, path);
    const uint32_t size = static_cast<uint32_t>(packet.size());
    write_trace_scalar(trace.output, size);
    if (size)
        trace.output.write(reinterpret_cast<const char*>(packet.data()), size);
    trace.output.flush();
    if (!trace.output)
        throw std::runtime_error("failed writing TARZAN_TRACE_MENU_GIF");
    trace.bytes += record_bytes + size;
    ++trace.packets;
}

constexpr const char* kPrivilegedRegisterNames[] = {
    "PMODE", "SMODE1", "SMODE2", "SRFSH", "SYNCH1", "SYNCH2", "SYNCV",
    "DISPFB1", "DISPLAY1", "DISPFB2", "DISPLAY2", "EXTBUF", "EXTDATA",
    "EXTWRITE", "BGCOLOR", "CSR", "IMR", "BUSDIR", "SIGLBLID"};

uint64_t capture_uint_env(const char* name, uint64_t fallback, bool allow_zero) {
    const char* raw = std::getenv(name);
    if (!raw || raw[0] == '\0')
        return fallback;
    const std::string value(raw);
    uint64_t parsed = 0;
    const auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), parsed);
    if (error != std::errc{} || end != value.data() + value.size() ||
        (!allow_zero && parsed == 0))
        throw std::invalid_argument(std::string(name) +
            (allow_zero ? " must be an unsigned integer" : " must be a positive unsigned integer"));
    return parsed;
}

CaptureSession& capture_session() {
    static thread_local CaptureSession session;
    if (session.initialized)
        return session;
    session.initialized = true;

    const char* directory = std::getenv("TARZAN_CAPTURE_DIR");
    if (!directory || directory[0] == '\0')
        return session;

    uint32_t max_frames = 10;
    if (const char* limit = std::getenv("TARZAN_CAPTURE_FRAMES"); limit && limit[0] != '\0') {
        const std::string value(limit);
        const auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), max_frames);
        if (error != std::errc{} || end != value.data() + value.size() ||
            max_frames == 0 || max_frames > 120)
            throw std::invalid_argument("TARZAN_CAPTURE_FRAMES must be an integer from 1 through 120");
    }
    const uint64_t start_field = capture_uint_env("TARZAN_CAPTURE_START_FIELD", 0, true);
    const uint64_t interval = capture_uint_env("TARZAN_CAPTURE_INTERVAL", 1, false);

    session.directory = std::filesystem::path(directory);
    if (!session.directory.is_absolute())
        throw std::invalid_argument("TARZAN_CAPTURE_DIR must be an absolute directory path");
    session.max_frames = max_frames;
    session.start_field = start_field;
    session.interval = interval;
    session.enabled = true;
    if (const char* trigger = std::getenv("TARZAN_CAPTURE_TRIGGER_FILE"); trigger && *trigger) {
        session.trigger_file = trigger;
        if (!session.trigger_file.is_absolute())
            throw std::invalid_argument("TARZAN_CAPTURE_TRIGGER_FILE must be absolute");
    }
    std::clog << "Metal diagnostic capture enabled: directory=" << session.directory
              << " max_frames=" << session.max_frames
              << " start_field=" << session.start_field
              << " interval=" << session.interval << '\n';
    return session;
}

uint64_t timestamp_microseconds() {
    const auto now = std::chrono::system_clock::now().time_since_epoch();
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(now).count());
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

    RrvPcsx2GsBridgeCapabilities caps{};
    caps.struct_size = sizeof(caps);
    error[0] = '\0';
    auto* handle = rrv_pcsx2_gs_bridge_create(&config, &caps, error, sizeof(error));
    const auto flags = caps.capability_flags;
    std::clog << "PCSX2 GS backend: " << (caps.renderer_name ? caps.renderer_name : "<unnamed>")
              << " kind=" << caps.renderer_kind << " capabilities=0x" << std::hex << flags
              << std::dec << '\n';
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
    trace_menu_gif_submit(path, packet);
}
void MetalGsAdapter::vsync(const std::array<uint64_t, RRV_PCSX2_GS_BRIDGE_PRIV_REG_COUNT>& regs,
                           uint64_t field_index, uint32_t parity) {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS vsync after close");
    if (parity > 1) throw std::invalid_argument("GS field parity must be 0 or 1");
    // Diagnostic A/B only: OPL's PAL FIELD profile supplies the CRT timing
    // missing from our HLE. Keep guest registers untouched and capture exactly
    // what was sent to the renderer on each side of the selected boundary.
    auto scanout_regs = regs;
    // Controlled comparison with KillzoneRecomp's same-buffer flicker-filter
    // workaround. A file toggle permits before/after evidence in one run.
    static const char* circuit_toggle = std::getenv("TARZAN_DIAG_SINGLE_CIRCUIT_FILE");
    if (circuit_toggle && (regs[0] & 3u) == 3u &&
        (regs[7] & UINT64_C(0x7ffffffffff)) == (regs[9] & UINT64_C(0x7ffffffffff)) &&
        std::filesystem::exists(circuit_toggle)) {
        scanout_regs[9] = scanout_regs[7];
        scanout_regs[10] = scanout_regs[8];
    }
    static const uint64_t pal_syncv_start = capture_uint_env(
        "TARZAN_DIAG_PAL_SYNCV_START_FIELD", UINT64_MAX, true);
    if (field_index >= pal_syncv_start && ((regs[1] >> 13) & 3) == 3 &&
        (regs[2] & 1) && regs[6] == 0) {
        scanout_regs[6] = UINT64_C(0x00a9000502101401);
        if (field_index == pal_syncv_start)
            std::clog << "Metal PAL SYNCV diagnostic begins at field=" << field_index << '\n';
    }
    if (tarzan_experiments_boot_language_hidden()) {
        // Keep GS work/VSync intact, but do not display the bypassed selector.
        scanout_regs[0] &= ~UINT64_C(3);
        scanout_regs[14] = 0;
    }
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_vsync(handle_, scanout_regs.data(), field_index, parity, error, sizeof(error)),
          "vsync", error);
    menu_gif_trace.field = field_index;
    capture_vsync_regs = scanout_regs;
    capture_after_vsync(field_index);
}

void MetalGsAdapter::capture_after_vsync(uint64_t field_index) {
    CaptureSession& session = capture_session();
    if (!session.enabled)
        return;
    // Explicit one-shot rearm lets a diagnostic capture the screen reached
    // by real input, without repeatedly replaying the intro on timed guesses.
    if (!session.trigger_file.empty()) {
        std::error_code ec;
        if (std::filesystem::remove(session.trigger_file, ec)) {
            session.completed_frames = 0;
            session.start_field = field_index;
        } else if (session.completed_frames == 0 || session.completed_frames >= session.max_frames) {
            return;
        }
    }
    if (session.completed_frames >= session.max_frames)
        return;
    if (field_index < session.start_field ||
        ((field_index - session.start_field) % session.interval) != 0)
        return;

    std::error_code fs_error;
    std::filesystem::create_directories(session.directory, fs_error);
    if (fs_error)
        throw std::runtime_error("cannot create TARZAN_CAPTURE_DIR: " + fs_error.message());

    const RrvPcsx2GsBridgeStats before = stats();
    // Only snapshot a fresh, actually submitted GPU presentation. This avoids
    // turning the bridge's documented pre-display blank fallback into a
    // misleading diagnostic image, and avoids duplicate stale captures when
    // the layer has no drawable.
    if (before.presented_frames == 0 ||
        before.presented_frames <= session.last_captured_present_index)
        return;
    RrvPcsx2GsBridgeCaptureRequest request{};
    request.struct_size = sizeof(request);
    request.guest_tick = field_index;
    request.field_index = field_index;
    request.present_index = before.presented_frames;

    RrvPcsx2GsBridgeCaptureResult result{};
    result.struct_size = sizeof(result);
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_capture(handle_, &request, &result, error, sizeof(error)),
          "diagnostic capture", error);
    const RrvPcsx2GsBridgeFrame& frame = result.frame;
    if (result.guest_tick != field_index || result.field_index != field_index ||
        result.present_index != request.present_index || !frame.rgba || !frame.width ||
        !frame.height || frame.width > std::numeric_limits<uint32_t>::max() / 4u ||
        frame.stride_bytes < frame.width * 4u)
        throw std::runtime_error("PCSX2 Metal GS diagnostic capture returned invalid frame metadata");

    const uint64_t capture_time_us = timestamp_microseconds();
    const uint32_t capture_number = session.completed_frames + 1u;
    const std::string stem = "metal-" + std::to_string(capture_time_us) +
        "-frame-" + std::to_string(capture_number) +
        "-field-" + std::to_string(field_index);
    const std::filesystem::path ppm_path = session.directory / (stem + ".ppm");
    std::ofstream ppm(ppm_path, std::ios::binary | std::ios::trunc);
    if (!ppm)
        throw std::runtime_error("cannot create diagnostic PPM: " + ppm_path.string());
    ppm << "P6\n" << frame.width << ' ' << frame.height << "\n255\n";
    for (uint32_t y = 0; y < frame.height; ++y) {
        const uint8_t* row = frame.rgba + static_cast<size_t>(y) * frame.stride_bytes;
        for (uint32_t x = 0; x < frame.width; ++x) {
            const uint8_t rgb[3] = {row[x * 4u], row[x * 4u + 1u], row[x * 4u + 2u]};
            ppm.write(reinterpret_cast<const char*>(rgb), sizeof(rgb));
        }
    }
    ppm.close();
    if (!ppm)
        throw std::runtime_error("failed writing diagnostic PPM: " + ppm_path.string());

    // The frame pointer remains valid only until another bridge call; the
    // pixel copy is complete before querying post-capture statistics.
    const RrvPcsx2GsBridgeStats after = stats();
    const std::filesystem::path metadata_path = session.directory / (stem + ".txt");
    std::ofstream metadata(metadata_path, std::ios::trunc);
    if (!metadata)
        throw std::runtime_error("cannot create diagnostic metadata: " + metadata_path.string());
    metadata << "timestamp_epoch_us=" << capture_time_us << '\n'
             << "capture_number=" << capture_number << '\n'
             << "guest_tick=" << result.guest_tick << '\n'
             << "field_index=" << result.field_index << '\n'
             << "present_index=" << result.present_index << '\n'
             << "frame_sequence=" << frame.sequence << '\n'
             << "width=" << frame.width << '\n'
             << "height=" << frame.height << '\n'
             << "presented_frames=" << after.presented_frames << '\n'
             << "direct_gpu_presents=" << after.direct_gpu_presents << '\n'
             << "requested_captures=" << after.requested_captures << '\n'
             << "completed_captures=" << after.completed_captures << '\n'
             << "synchronous_cpu_readbacks=" << after.synchronous_cpu_readbacks << '\n'
             << "unexpected_readbacks=" << after.unexpected_readbacks << '\n'
             << "cpu_waits=" << after.cpu_waits << '\n'
             << "ppm_file=" << ppm_path.filename().string() << '\n';
    for (size_t index = 0; index < capture_vsync_regs.size(); ++index) {
        metadata << "gs_" << kPrivilegedRegisterNames[index] << "=0x"
                 << std::hex << capture_vsync_regs[index] << std::dec << '\n';
    }
    metadata.close();
    if (!metadata)
        throw std::runtime_error("failed writing diagnostic metadata: " + metadata_path.string());

    ++session.completed_frames;
    session.last_captured_present_index = result.present_index;
    std::clog << "Metal diagnostic capture saved: " << ppm_path
              << " present=" << result.present_index
              << " readbacks=" << after.synchronous_cpu_readbacks
              << " unexpected=" << after.unexpected_readbacks << '\n';
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
void MetalGsAdapter::read_local_memory(std::span<uint8_t> dst, uint64_t bitbltbuf,
                                       uint64_t trxpos, uint64_t trxreg) {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS local-memory read after close");
    if (dst.size() > UINT32_MAX) throw std::length_error("local-memory read exceeds bridge ABI size limit");
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_read_local_memory(handle_, dst.data(), static_cast<uint32_t>(dst.size()),
          bitbltbuf, trxpos, trxreg, error, sizeof(error)), "local-memory read", error);
}
void MetalGsAdapter::snapshot_local_memory(std::span<uint8_t> dst) {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS snapshot after close");
    if (dst.size() != RRV_PCSX2_GS_BRIDGE_LOCAL_MEMORY_BYTES)
        throw std::invalid_argument("GS local-memory snapshot destination must be exactly 4 MiB");
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_snapshot_local_memory(handle_, dst.data(),
          static_cast<uint32_t>(dst.size()), error, sizeof(error)), "snapshot", error);
}
void MetalGsAdapter::resize(uint32_t width, uint32_t height, float scale) {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS resize after close");
    validate_dimensions(width, height, scale);
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_resize(handle_, width, height, scale, error, sizeof(error)), "resize", error);
}
RrvPcsx2GsBridgeStats MetalGsAdapter::stats() {
    if (!handle_) throw std::logic_error("PCSX2 Metal GS stats after close");
    RrvPcsx2GsBridgeStats result{};
    result.struct_size = sizeof(result);
    char error[1024]{};
    check(rrv_pcsx2_gs_bridge_get_stats(handle_, &result, error, sizeof(error)), "stats", error);
    return result;
}

} // namespace tarzan::ps2

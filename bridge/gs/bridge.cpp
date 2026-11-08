// SPDX-FileCopyrightText: 2002-2026 PCSX2 Dev Team; 2026 RRV-Recomp
// SPDX-License-Identifier: GPL-3.0-or-later
#include "rrv_pcsx2_gs_bridge.h"
#include "common/FileSystem.h"
#include "common/FPControl.h"
#include "common/MemorySettingsInterface.h"
#include "common/Path.h"
#include "pcsx2/Config.h"
#include "pcsx2/GS.h"
#include "pcsx2/Host.h"
#include "pcsx2/ImGui/ImGuiManager.h"
#include "pcsx2/VMManager.h"
#include <array>
#include <atomic>
#include <cstdarg>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <string_view>
#include <vector>
#include <dlfcn.h>

extern "C" void rrv_pcsx2_gs_bridge_initialize_host_console();
extern "C" int rrv_pcsx2_gs_bridge_configure_native_surface(
    uint32_t, uint32_t, void*, void*, uint32_t, uint32_t, float, char*, uint32_t);
extern "C" void rrv_pcsx2_gs_bridge_clear_native_surface();
extern "C" int rrv_pcsx2_gs_bridge_validate_macos_surface(void*, void*, char*, uint32_t);
extern "C" int rrv_pcsx2_gs_bridge_is_macos_main_thread();
extern "C" int rrv_pcsx2_gs_bridge_consume_owner_surface(const RrvPcsx2GsBridgeSurface*, char*, uint32_t);

namespace {
std::atomic<RrvPcsx2GsHostPresentCallback> s_host_present_callback{nullptr};
constexpr size_t kPrivRegsBytes = 0x2000;
constexpr size_t kSlotCount = RRV_PCSX2_GS_BRIDGE_PRIV_REG_COUNT;
constexpr std::array<size_t, kSlotCount> kPcsx2Offsets = {
    0, 16, 32, 48, 64, 80, 96, 112, 128, 144, 160, 176, 192, 208, 224,
    0x1000, 0x1010, 0x1040, 0x1080,
};
void SetError(char* destination, uint32_t capacity, const char* format, ...)
{
    if (!destination || capacity == 0)
        return;
    va_list args;
    va_start(args, format);
    std::vsnprintf(destination, capacity, format, args);
    va_end(args);
    destination[capacity - 1] = '\0';
}
std::string GetBridgeDirectory()
{
    Dl_info info{};
    if (dladdr(reinterpret_cast<const void*>(&GetBridgeDirectory), &info) == 0 || !info.dli_fname)
        return {};
    const std::string library_path = Path::Canonicalize(info.dli_fname);
    return std::string(Path::GetDirectory(library_path));
}

bool InitializePcsx2BaseSettings(std::string& error)
{

    static bool initialized = false;
    static MemorySettingsInterface settings;
    if (initialized)
        return true;

    rrv_pcsx2_gs_bridge_initialize_host_console();

    EmuFolders::SetAppRoot();

    const std::string bridge_directory = GetBridgeDirectory();
    const std::string staged_resources = bridge_directory.empty() ? std::string{} :
        Path::Combine(bridge_directory, "resources");
    if (!staged_resources.empty() && FileSystem::DirectoryExists(staged_resources.c_str()))
        EmuFolders::Resources = Path::Canonicalize(staged_resources);
    else
    {
        error = "PCSX2 resources directory is unavailable beside the verified bridge";
        return false;
    }

    if (const char* data_root = std::getenv("PS2X_PCSX2_DATA_ROOT"); data_root && *data_root)
        EmuConfig.CustomDataPath = data_root;

    if (!FileSystem::DirectoryExists(EmuFolders::Resources.c_str()) ||
        !EmuFolders::SetDataDirectory(nullptr))
    {
        error = "PCSX2 resources/data directory setup failed";
        return false;
    }

    EmuFolders::UserResources = EmuFolders::Resources;

    const char* hardware_error = nullptr;
    if (!VMManager::PerformEarlyHardwareChecks(&hardware_error))
    {
        error = hardware_error ? hardware_error : "PCSX2 early hardware checks failed";
        return false;
    }

    Host::Internal::SetBaseSettingsLayer(&settings);
    VMManager::SetDefaultSettings(settings, true, true, true, true, true);
    VMManager::Internal::LoadStartupSettings();

    const std::string roboto_path =
        EmuFolders::GetOverridableResourcePath("fonts" FS_OSPATH_SEPARATOR_STR "Roboto-Regular.ttf");
    const auto roboto_data = FileSystem::MapBinaryFileForRead(roboto_path.c_str());
    if (roboto_data.empty())
    {
        error = "PCSX2 resource font is unavailable: " + roboto_path;
        return false;
    }

    std::vector<ImGuiManager::FontInfo> fonts;
    ImGuiManager::FontInfo font{};
    font.data = roboto_data;
    fonts.push_back(font);
    ImGuiManager::SetFonts(std::move(fonts));
    initialized = true;
    return true;
}

bool ConfigureOptions(Pcsx2Config::GSOptions& options, char* error, uint32_t capacity)
{
    options.Renderer = GSRendererType::Metal;
    options.UpscaleMultiplier = 1.0f;
    options.AutoFlushSW = true;
    options.FXAA = false;
    options.CASMode = GSCASMode::Disabled;
    options.MaxAnisotropy = 0;
    options.TextureFiltering = BiFiltering::PS2;
    options.VsyncEnable = false;
    if (const char* scale = std::getenv("RRV_PCSX2_GS_UPSCALE"))
    {
        char* end = nullptr;
        const long value = std::strtol(scale, &end, 10);
        if (end == scale || *end || value < 1 || value > 6)
        {
            SetError(error, capacity, "render scale must be 1 through 6");
            return false;
        }
        options.UpscaleMultiplier = static_cast<float>(value);
    }
    if (const char* aniso = std::getenv("RRV_PCSX2_GS_ANISO"))
    {
        const std::string_view value(aniso);
        if (value != "0" && value != "2" && value != "4" && value != "8" && value != "16")
        {
            SetError(error, capacity, "anisotropy must be 0, 2, 4, 8 or 16");
            return false;
        }
        options.MaxAnisotropy = static_cast<u8>(std::atoi(aniso));
    }
    if (const char* aspect = std::getenv("RRV_PCSX2_GS_ASPECT"))
    {
        const std::string_view value(aspect);
        if (value == "auto") options.AspectRatio = AspectRatioType::RAuto4_3_3_2;
        else if (value == "4:3") options.AspectRatio = AspectRatioType::R4_3;
        else if (value == "16:9") options.AspectRatio = AspectRatioType::R16_9;
        else
        {
            SetError(error, capacity, "aspect must be auto, 4:3 or 16:9");
            return false;
        }
    }
    EmuConfig.GS.AspectRatio = options.AspectRatio;
    EmuConfig.CurrentAspectRatio = options.AspectRatio;
    EmuConfig.CurrentCustomAspectRatio = 0.0f;
    return true;
}
}

struct RrvPcsx2GsBridge {
    alignas(16) std::array<uint8_t, kPrivRegsBytes> pcsx2_regs{};
    bool owner_thread_surface = false;
    bool open = false;
};

extern "C" uint32_t rrv_pcsx2_gs_bridge_version(void)
{
    return RRV_PCSX2_GS_BRIDGE_ABI_VERSION;
}
extern "C" void rrv_pcsx2_gs_bridge_set_host_present_callback(
    RrvPcsx2GsHostPresentCallback callback)
{
    s_host_present_callback.store(callback, std::memory_order_release);
}
extern "C" void rrv_pcsx2_gs_bridge_host_present(int active)
{
    if (const auto callback = s_host_present_callback.load(std::memory_order_acquire))
        callback(active);
}
extern "C" void rrv_pcsx2_gs_bridge_owner_thread_init(void)
{
    // Match MTGS::ThreadEntryPoint at fd9d310c: the GS thread does not inherit
    // the producer's EE/VU rounding and denormal policy.
    FPControlRegister::SetCurrent(FPControlRegister::GetDefault());
}

extern "C" RrvPcsx2GsBridge* rrv_pcsx2_gs_bridge_create(
    const RrvPcsx2GsBridgeConfig* config, RrvPcsx2GsBridgeCapabilities* capabilities,
    char* error, uint32_t error_capacity)
{
    if (!config || config->struct_size < sizeof(*config) || !capabilities ||
        capabilities->struct_size < sizeof(*capabilities) ||
        config->presentation_mode != RRV_PCSX2_GS_PRESENTATION_DIRECT_GPU ||
        config->renderer_kind != RRV_PCSX2_GS_RENDERER_METAL ||
        config->gs_render_mode != RRV_PCSX2_GS_RENDER_MODE_FIELD)
    {
        SetError(error, error_capacity, "Metal GS requires a direct-present field-mode configuration");
        return nullptr;
    }
    const auto& surface = config->surface;
    if (surface.struct_size < sizeof(surface) ||
        surface.kind != RRV_PCSX2_GS_SURFACE_MACOS_VIEW_METAL_LAYER ||
        (surface.flags & (RRV_PCSX2_GS_SURFACE_FLAG_CALLER_OWNS_HANDLES |
                          RRV_PCSX2_GS_SURFACE_FLAG_MAIN_THREAD_PREPARED)) !=
            (RRV_PCSX2_GS_SURFACE_FLAG_CALLER_OWNS_HANDLES |
             RRV_PCSX2_GS_SURFACE_FLAG_MAIN_THREAD_PREPARED) ||
        !surface.native_view || !surface.native_layer ||
        !surface.width_pixels || !surface.height_pixels || surface.backing_scale <= 0.0f)
    {
        SetError(error, error_capacity, "Metal GS requires a prepared view and layer retained by its caller");
        return nullptr;
    }
    std::string startup_error;
    if (!InitializePcsx2BaseSettings(startup_error))
    {
        SetError(error, error_capacity, "%s", startup_error.c_str());
        return nullptr;
    }
    const bool owner_surface = (surface.flags & RRV_PCSX2_GS_SURFACE_FLAG_OWNER_THREAD) != 0;
    if ((!owner_surface && !rrv_pcsx2_gs_bridge_is_macos_main_thread()) ||
        (owner_surface && !rrv_pcsx2_gs_bridge_consume_owner_surface(&surface, error, error_capacity)) ||
        (!owner_surface && !rrv_pcsx2_gs_bridge_validate_macos_surface(
            surface.native_view, surface.native_layer, error, error_capacity)))
        return nullptr;
    if (!rrv_pcsx2_gs_bridge_configure_native_surface(surface.kind, surface.flags,
            surface.native_view, surface.native_layer, surface.width_pixels,
            surface.height_pixels, surface.backing_scale, error, error_capacity))
        return nullptr;
    auto bridge = std::make_unique<RrvPcsx2GsBridge>();
    bridge->owner_thread_surface = owner_surface;
    Pcsx2Config::GSOptions options{};
    if (!ConfigureOptions(options, error, error_capacity))
    {
        rrv_pcsx2_gs_bridge_clear_native_surface();
        return nullptr;
    }
    if (!GSopen(options, GSRendererType::Metal, bridge->pcsx2_regs.data(),
            options.VsyncEnable ? GSVSyncMode::FIFO : GSVSyncMode::Disabled, false))
    {
        rrv_pcsx2_gs_bridge_clear_native_surface();
        SetError(error, error_capacity, "PCSX2 Metal GS initialization failed");
        return nullptr;
    }
    if (GSGetCurrentRenderer() != GSRendererType::Metal || !GSIsHardwareRenderer())
    {
        GSclose();
        rrv_pcsx2_gs_bridge_clear_native_surface();
        SetError(error, error_capacity, "PCSX2 did not initialize the Metal hardware renderer");
        return nullptr;
    }
    capabilities->supported_render_modes = RRV_PCSX2_GS_RENDER_MODE_CAP_FIELD;
    capabilities->renderer_kind = RRV_PCSX2_GS_RENDERER_METAL;
    capabilities->capability_flags = RRV_PCSX2_GS_CAP_HARDWARE_GS |
        RRV_PCSX2_GS_CAP_METAL_BACKEND | RRV_PCSX2_GS_CAP_DIRECT_PRESENT;
    capabilities->renderer_name = "PCSX2 Metal";
    bridge->open = true;
    return bridge.release();
}

extern "C" void rrv_pcsx2_gs_bridge_destroy(RrvPcsx2GsBridge* bridge)
{
    if (!bridge) return;
    if (bridge->open) GSclose();
    rrv_pcsx2_gs_bridge_clear_native_surface();
    delete bridge;
}

extern "C" int rrv_pcsx2_gs_bridge_submit(
    RrvPcsx2GsBridge* bridge, uint32_t path_id, const uint8_t* gif_bytes,
    uint32_t size_bytes, char* error, uint32_t error_capacity)
{
    if (!bridge || !bridge->open)
    {
        SetError(error, error_capacity, "PCSX2 GS bridge is not initialized");
        return 0;
    }
    if (path_id < 1 || path_id > 3)
    {
        SetError(error, error_capacity, "invalid post-arbitration GIF path %u", path_id);
        return 0;
    }
    if ((size_bytes != 0 && !gif_bytes) || (size_bytes & 0x0f) != 0)
    {
        SetError(error, error_capacity, "GIF packet must be non-null and 16-byte aligned in size (got %u)", size_bytes);
        return 0;
    }

    if (size_bytes) GSgifTransfer(gif_bytes, size_bytes / 16u);
    return 1;
}
extern "C" int rrv_pcsx2_gs_bridge_reset_gs(
    RrvPcsx2GsBridge* bridge, char* error, uint32_t error_capacity)
{
    if (!bridge || !bridge->open)
    {
        SetError(error, error_capacity, "PCSX2 GS bridge is not open for reset");
        return 0;
    }
    // fd9d310c: GS.cpp::csrWrite calls MTGS::ResetGS(false); the consumer
    // calls GSreset(false). This resets GSState's parser and draw environment
    // but does not reset producer GIF progression or clear GS local memory.
    GSreset(false);
    return 1;
}

extern "C" int rrv_pcsx2_gs_bridge_readback(
    RrvPcsx2GsBridge* bridge, uint64_t* csr, uint64_t* siglblid,
    char* error, uint32_t error_capacity)
{
    if (!bridge || !bridge->open || !csr || !siglblid)
    {
        SetError(error, error_capacity, "PCSX2 GS bridge or readback outputs are null");
        return 0;
    }
    std::memcpy(csr, bridge->pcsx2_regs.data() + kPcsx2Offsets[15], sizeof(*csr));
    std::memcpy(siglblid, bridge->pcsx2_regs.data() + kPcsx2Offsets[18], sizeof(*siglblid));

    return 1;
}

extern "C" RRV_PCSX2_GS_BRIDGE_API int rrv_pcsx2_gs_bridge_write_csr(
    RrvPcsx2GsBridge* bridge, uint64_t csr, uint32_t write_mask,
    int write_high_word, char* error, uint32_t error_capacity)
{
    if (!bridge || !bridge->open)
    {
        SetError(error, error_capacity, "PCSX2 GS bridge is not open for CSR synchronization");
        return 0;
    }
    if (write_high_word != 0 && static_cast<uint32_t>(csr >> 32) != write_mask)
    {
        SetError(error, error_capacity,
                 "CSR high-word mirror 0x%08x does not match PCSX2 write mask 0x%08x",
                 static_cast<uint32_t>(csr >> 32), write_mask);
        return 0;
    }

    // GSopen() was given pcsx2_regs as basemem, so this is the renderer's
    // live CSR readback. The recompiler owns low-word W1C semantics; publish
    // that computed mirror without synthesizing or dropping interrupt events.
    std::memcpy(bridge->pcsx2_regs.data() + kPcsx2Offsets[15], &csr, sizeof(csr));
    if (write_high_word != 0)
        GSwriteCSR(write_mask);
    return 1;
}

extern "C" uint32_t rrv_pcsx2_gs_bridge_read_fifo(
    RrvPcsx2GsBridge* bridge, uint8_t* dst, uint32_t byte_count)
{
    if (!bridge || !bridge->open || !dst || byte_count == 0 || (byte_count & 15u) ||
        byte_count > RRV_PCSX2_GS_BRIDGE_LOCAL_MEMORY_BYTES)
        return 0;
    std::vector<uint8_t> fifo(byte_count);
    const uint32_t copied = GSReadCurrentFIFO(fifo.data(), byte_count);
    if (copied) std::memcpy(dst, fifo.data(), copied);
    return copied;
}

extern "C" int rrv_pcsx2_gs_bridge_read_local_memory(
    RrvPcsx2GsBridge* bridge, uint8_t* dst, uint32_t byte_count,
    uint64_t bitbltbuf, uint64_t trxpos, uint64_t trxreg,
    char* error, uint32_t error_capacity)
{
    if (!bridge || !bridge->open)
    {
        SetError(error, error_capacity, "PCSX2 GS bridge is not open");
        return 0;
    }
    if (byte_count == 0u)
    {
        // A zero-byte result request has no guest transaction meaning.  In
        // particular, it must not report success before checking the native
        // local-to-host cursor or be mistaken for an acknowledged no-op.
        SetError(error, error_capacity, "local-memory read requires a non-zero byte count");
        return 0;
    }
    if (!dst)
    {
        SetError(error, error_capacity, "PCSX2 GS local-memory destination is null");
        return 0;
    }

    // Keep the bridge allocation within the same fixed local-memory maximum
    // enforced by GSState::RrvReadLocalMemoryChecked.  This check must happen
    // before vector construction: the bridge is built without exceptions, so
    // an attacker-controlled UINT32_MAX request must not reach allocation and
    // terminate the process before the native adapter can reject it.
    constexpr uint64_t kNativeLocalMemoryBytes = RRV_PCSX2_GS_BRIDGE_LOCAL_MEMORY_BYTES;
    if (static_cast<uint64_t>(byte_count) > kNativeLocalMemoryBytes)
    {
        SetError(error, error_capacity, "local-memory read size %u exceeds native GS local memory", byte_count);
        return 0;
    }
    const uint64_t qwc64 = (static_cast<uint64_t>(byte_count) + 15u) / 16u;
    if (qwc64 == 0u || qwc64 > UINT32_MAX || qwc64 > static_cast<uint64_t>(SIZE_MAX) / 16u)
    {
        SetError(error, error_capacity, "local-memory read size %u is not representable", byte_count);
        return 0;
    }
    const size_t fifo_bytes = static_cast<size_t>(qwc64 * 16u);

    // PCSX2 owns the cursor created by the preceding guest TRXDIR=1. The
    // maintained GS adapter compares these ABI provenance descriptors against
    // both that cursor and the live GS environment, rejects no-active/upload/
    // rewritten/inconsistent transfers before consuming anything, then uses
    // PCSX2's native InitReadFIFO/ReadFIFO continuation path. It also retires
    // rasterizer work before the local-memory operation. The bridge keeps no
    // duplicate transaction state and never falls back to the stale-state
    // ReadLocalMemoryUnsync helper.
    std::vector<uint8_t> fifo(fifo_bytes);
    const bool local_read_ok = GSReadLocalMemoryChecked(fifo.data(), byte_count, bitbltbuf, trxpos, trxreg);

    if (!local_read_ok)
    {
        SetError(error, error_capacity,
                 "PCSX2 rejected local-to-host transfer state or descriptor before FIFO consumption");
        return 0;
    }
    std::memcpy(dst, fifo.data(), byte_count);
    return 1;
}

extern "C" int rrv_pcsx2_gs_bridge_vsync(
    RrvPcsx2GsBridge* bridge, const RrvPcsx2GsPrivRegs regs,
    uint64_t field_index, uint32_t field_parity, char* error, uint32_t error_capacity)
{
    if (!bridge || !bridge->open || !regs || field_parity > 1)
    {
        SetError(error, error_capacity, "invalid GS field transition");
        return 0;
    }
    for (size_t index = 0; index < kSlotCount; ++index)
    {
        uint64_t value = regs[index];
        if (index == 15)
            value = (value & ~(uint64_t{1} << 13)) | (static_cast<uint64_t>(field_parity) << 13);
        std::memcpy(bridge->pcsx2_regs.data() + kPcsx2Offsets[index], &value, sizeof(value));
    }
    // PCSX2 expects the inverse of CSR.FIELD.
    GSvsync(field_parity ? 0 : 1, true);
    (void)field_index;
    return 1;
}

extern "C" int rrv_pcsx2_gs_bridge_resize(
    RrvPcsx2GsBridge* bridge, uint32_t width_pixels, uint32_t height_pixels,
    float backing_scale, char* error, uint32_t error_capacity)
{
    if (!bridge || !bridge->open || width_pixels == 0u ||
        height_pixels == 0u || backing_scale <= 0.0f)
    {
        SetError(error, error_capacity,
                 "direct presentation resize requires an active surface and non-zero dimensions");
        return 0;
    }
    // Default ABI callers keep their original main-thread contract. The owner
    // path uses pinned GSDeviceMTL::ResizeWindow, which only updates owner GS
    // dimensions and CAMetalLayer.drawableSize; it never touches NSView or
    // dispatches to main. SDL prepares its own view on main before submission.
    if (!bridge->owner_thread_surface && !rrv_pcsx2_gs_bridge_is_macos_main_thread())
    {
        SetError(error, error_capacity,
                 "direct presentation resize must run on AppKit's main thread");
        return 0;
    }
    GSResizeDisplayWindow(width_pixels, height_pixels, backing_scale);
    return 1;
}

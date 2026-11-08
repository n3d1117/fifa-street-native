// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef RRV_PCSX2_GS_BRIDGE_H
#define RRV_PCSX2_GS_BRIDGE_H

#include <stdint.h>

#define RRV_PCSX2_GS_BRIDGE_API __attribute__((visibility("default")))

#ifdef __cplusplus
extern "C" {
#endif

enum {
    RRV_PCSX2_GS_BRIDGE_ABI_VERSION = 5,
    RRV_PCSX2_GS_BRIDGE_PRIV_REG_COUNT = 19,
    RRV_PCSX2_GS_BRIDGE_LOCAL_MEMORY_BYTES = 4 * 1024 * 1024,
    RRV_PCSX2_GS_RENDER_MODE_FIELD = 0,
    RRV_PCSX2_GS_RENDER_MODE_CAP_FIELD = 1u << RRV_PCSX2_GS_RENDER_MODE_FIELD,
    RRV_PCSX2_GS_RENDERER_UNKNOWN = 0,
    RRV_PCSX2_GS_RENDERER_METAL = 2,

    RRV_PCSX2_GS_PRESENTATION_DIRECT_GPU = 1,

    RRV_PCSX2_GS_SURFACE_NONE = 0,
    RRV_PCSX2_GS_SURFACE_MACOS_VIEW_METAL_LAYER = 1,

    RRV_PCSX2_GS_SURFACE_FLAG_CALLER_OWNS_HANDLES = 1u << 0,

    RRV_PCSX2_GS_SURFACE_FLAG_MAIN_THREAD_PREPARED = 1u << 1,

    RRV_PCSX2_GS_SURFACE_FLAG_OWNER_THREAD = 1u << 2,
    RRV_PCSX2_GS_CAP_HARDWARE_GS = 1u << 0,
    RRV_PCSX2_GS_CAP_METAL_BACKEND = 1u << 1,
    RRV_PCSX2_GS_CAP_DIRECT_PRESENT = 1u << 2,
};

typedef struct RrvPcsx2GsBridge RrvPcsx2GsBridge;

typedef struct RrvPcsx2GsBridgeSurface {
    uint32_t struct_size;
    uint32_t kind;  // RRV_PCSX2_GS_SURFACE_*
    uint32_t flags; // RRV_PCSX2_GS_SURFACE_FLAG_*
    uint32_t reserved;
    void* native_view;  // opaque; SDL_MetalView for the macOS direct-present kind
    void* native_layer; // opaque; result of SDL_Metal_GetLayer for that view
    uint32_t width_pixels;
    uint32_t height_pixels;
    float backing_scale;
    uint32_t reserved2;
} RrvPcsx2GsBridgeSurface;

typedef struct RrvPcsx2GsBridgeConfig {
    uint32_t struct_size;
    uint32_t snapshot_width;
    uint32_t snapshot_height;
    uint32_t sw_threads; // 0: PCSX2 Software renderer default.

    uint32_t gs_render_mode;
    uint32_t presentation_mode; // RRV_PCSX2_GS_PRESENTATION_*

    uint32_t renderer_kind; // RRV_PCSX2_GS_RENDERER_*
    uint32_t reserved;

    RrvPcsx2GsBridgeSurface surface;
} RrvPcsx2GsBridgeConfig;

typedef struct RrvPcsx2GsBridgeCapabilities {
    uint32_t struct_size;
    uint32_t supported_render_modes; // RRV_PCSX2_GS_RENDER_MODE_CAP_* bitset
    uint32_t renderer_kind;          // RRV_PCSX2_GS_RENDERER_*
    uint32_t capability_flags;       // RRV_PCSX2_GS_CAP_* bitset
    const char* renderer_name;
} RrvPcsx2GsBridgeCapabilities;

typedef uint64_t RrvPcsx2GsPrivRegs[RRV_PCSX2_GS_BRIDGE_PRIV_REG_COUNT];

RRV_PCSX2_GS_BRIDGE_API uint32_t rrv_pcsx2_gs_bridge_version(void);
typedef void (*RrvPcsx2GsHostPresentCallback)(int active);
RRV_PCSX2_GS_BRIDGE_API void rrv_pcsx2_gs_bridge_set_host_present_callback(
    RrvPcsx2GsHostPresentCallback callback);
RRV_PCSX2_GS_BRIDGE_API void rrv_pcsx2_gs_bridge_host_present(int active);

RRV_PCSX2_GS_BRIDGE_API int rrv_pcsx2_gs_bridge_prepare_owner_surface(
    RrvPcsx2GsBridgeSurface* surface, char* error, uint32_t error_capacity);
RRV_PCSX2_GS_BRIDGE_API void rrv_pcsx2_gs_bridge_owner_thread_init(void);
typedef void (*RrvPcsx2GsBridgeOwnerCommand)(void* context);
RRV_PCSX2_GS_BRIDGE_API void rrv_pcsx2_gs_bridge_owner_command(
    RrvPcsx2GsBridgeOwnerCommand command, void* context);

RRV_PCSX2_GS_BRIDGE_API int rrv_pcsx2_gs_bridge_pump_main_thread(void);

RRV_PCSX2_GS_BRIDGE_API RrvPcsx2GsBridge* rrv_pcsx2_gs_bridge_create(
    const RrvPcsx2GsBridgeConfig* config,
    RrvPcsx2GsBridgeCapabilities* capabilities,
    char* error, uint32_t error_capacity);
RRV_PCSX2_GS_BRIDGE_API void rrv_pcsx2_gs_bridge_destroy(RrvPcsx2GsBridge* bridge);

RRV_PCSX2_GS_BRIDGE_API int rrv_pcsx2_gs_bridge_reset_gs(
    RrvPcsx2GsBridge* bridge, char* error, uint32_t error_capacity);

RRV_PCSX2_GS_BRIDGE_API int rrv_pcsx2_gs_bridge_submit(
    RrvPcsx2GsBridge* bridge, uint32_t path_id, const uint8_t* gif_bytes,
    uint32_t size_bytes, char* error, uint32_t error_capacity);

RRV_PCSX2_GS_BRIDGE_API int rrv_pcsx2_gs_bridge_readback(
    RrvPcsx2GsBridge* bridge, uint64_t* csr, uint64_t* siglblid,
    char* error, uint32_t error_capacity);

RRV_PCSX2_GS_BRIDGE_API int rrv_pcsx2_gs_bridge_write_csr(
    RrvPcsx2GsBridge* bridge, uint64_t csr, uint32_t write_mask,
    int write_high_word, char* error, uint32_t error_capacity);

RRV_PCSX2_GS_BRIDGE_API uint32_t rrv_pcsx2_gs_bridge_read_fifo(
    RrvPcsx2GsBridge* bridge, uint8_t* dst, uint32_t byte_count);

RRV_PCSX2_GS_BRIDGE_API int rrv_pcsx2_gs_bridge_read_local_memory(
    RrvPcsx2GsBridge* bridge, uint8_t* dst, uint32_t byte_count,
    uint64_t bitbltbuf, uint64_t trxpos, uint64_t trxreg,
    char* error, uint32_t error_capacity);

RRV_PCSX2_GS_BRIDGE_API int rrv_pcsx2_gs_bridge_vsync(
    RrvPcsx2GsBridge* bridge, const RrvPcsx2GsPrivRegs regs,
    uint64_t field_index, uint32_t field_parity, char* error, uint32_t error_capacity);

RRV_PCSX2_GS_BRIDGE_API int rrv_pcsx2_gs_bridge_resize(
    RrvPcsx2GsBridge* bridge, uint32_t width_pixels, uint32_t height_pixels,
    float backing_scale, char* error, uint32_t error_capacity);

#ifdef __cplusplus
} // extern "C"
#endif

#endif // RRV_PCSX2_GS_BRIDGE_H

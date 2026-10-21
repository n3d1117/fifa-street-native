// Link seam for the tests preset: ps2_runtime is built with PS2X_USE_METAL=1
// exactly as in the game, but the native window, GS bridge and gamepad are
// replaced by inert fakes so no GPU or window is needed.
#include <cstdint>
#include "gamepad-input.hpp"

extern "C" {
int ps2x_metal_host_start(const char *, uint32_t, uint32_t) { return 1; }
void ps2x_metal_host_poll() {}
int ps2x_metal_host_should_close() { return 0; }
void ps2x_metal_host_stop() {}
int ps2x_metal_key_down(uint32_t) { return 0; }
void ps2x_gamepad_read(Ps2xGamepadSnapshot *out) { *out = Ps2xGamepadSnapshot{}; }
int ps2x_metal_gs_create() { return 1; }
void ps2x_metal_gs_destroy() {}
int ps2x_metal_gs_reset() { return 1; }
int ps2x_metal_gs_submit(uint32_t, const uint8_t *, uint32_t) { return 1; }
int ps2x_metal_gs_readback(uint64_t *csr, uint64_t *siglblid) { *csr = 0; *siglblid = 0; return 1; }
int ps2x_metal_gs_write_csr(uint64_t, uint32_t, int) { return 1; }
int ps2x_metal_gs_vsync(const uint64_t *, uint64_t, uint32_t) { return 1; }
uint32_t ps2x_metal_gs_read_fifo(uint8_t *, uint32_t bytes) { return bytes; }
int ps2x_metal_gs_read_local_memory(uint8_t *, uint32_t, uint64_t, uint64_t, uint64_t) { return 1; }
}

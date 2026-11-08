// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <cstdint>

extern "C" {
// Cocoa host lifecycle. All functions except create/submit/vsync/destroy run
// on the process main thread. GS functions are serialized on the EE thread.
int ps2x_metal_host_start(const char* title, uint32_t width, uint32_t height);
void ps2x_metal_host_poll();
int ps2x_metal_host_should_close();
void ps2x_metal_host_request_close();  // any thread
void ps2x_metal_host_stop();
int ps2x_metal_gs_create();
void ps2x_metal_gs_destroy();
int ps2x_metal_gs_submit(uint32_t path, const uint8_t* bytes, uint32_t size);
int ps2x_metal_gs_reset();
int ps2x_metal_gs_write_csr(uint64_t authoritative_csr, uint32_t high_word_write_mask,
                              int write_high_word);
int ps2x_metal_gs_vsync(const uint64_t* regs19, uint64_t tick, uint32_t parity);
int ps2x_metal_gs_readback(uint64_t* csr, uint64_t* siglblid);
uint32_t ps2x_metal_gs_read_fifo(uint8_t* dst, uint32_t byte_count);
int ps2x_metal_gs_read_local_memory(uint8_t* dst, uint32_t byte_count,
                                     uint64_t bitbltbuf, uint64_t trxpos, uint64_t trxreg);
int ps2x_metal_gs_capabilities(uint32_t* renderer_kind, uint32_t* flags);
int ps2x_metal_key_down(uint32_t key);
}

enum Ps2xMetalKey : uint32_t {
    PS2X_KEY_UP=1, PS2X_KEY_DOWN, PS2X_KEY_LEFT, PS2X_KEY_RIGHT,
    PS2X_KEY_X, PS2X_KEY_SPACE, PS2X_KEY_C, PS2X_KEY_ESCAPE,
    PS2X_KEY_Z, PS2X_KEY_KP0, PS2X_KEY_V, PS2X_KEY_KP1,
    PS2X_KEY_Q, PS2X_KEY_E, PS2X_KEY_LSHIFT, PS2X_KEY_RSHIFT,
    PS2X_KEY_ENTER, PS2X_KEY_TAB
};

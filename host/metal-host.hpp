// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <cstdint>

extern "C" {
// Cocoa host lifecycle. All functions except create/submit/vsync/destroy run
// on the process main thread. GS functions are serialized on the EE thread.
int tarzan_metal_host_start(const char* title, uint32_t width, uint32_t height);
void tarzan_metal_host_poll();
int tarzan_metal_host_should_close();
void tarzan_metal_host_request_close();  // any thread
void tarzan_metal_host_stop();
int tarzan_metal_gs_create();
void tarzan_metal_gs_destroy();
int tarzan_metal_gs_submit(uint32_t path, const uint8_t* bytes, uint32_t size);
int tarzan_metal_gs_reset();
int tarzan_metal_gs_write_csr(uint64_t authoritative_csr, uint32_t high_word_write_mask,
                              int write_high_word);
int tarzan_metal_gs_vsync(const uint64_t* regs19, uint64_t tick, uint32_t parity);
int tarzan_metal_gs_readback(uint64_t* csr, uint64_t* siglblid);
int tarzan_metal_gs_read_local_memory(uint8_t* dst, uint32_t byte_count,
                                     uint64_t bitbltbuf, uint64_t trxpos, uint64_t trxreg);
int tarzan_metal_gs_capabilities(uint32_t* renderer_kind, uint32_t* flags);
int tarzan_metal_key_down(uint32_t key);
}

enum TarzanMetalKey : uint32_t {
    TARZAN_KEY_UP=1, TARZAN_KEY_DOWN, TARZAN_KEY_LEFT, TARZAN_KEY_RIGHT,
    TARZAN_KEY_X, TARZAN_KEY_SPACE, TARZAN_KEY_C, TARZAN_KEY_ESCAPE,
    TARZAN_KEY_Z, TARZAN_KEY_KP0, TARZAN_KEY_V, TARZAN_KEY_KP1,
    TARZAN_KEY_Q, TARZAN_KEY_E, TARZAN_KEY_LSHIFT, TARZAN_KEY_RSHIFT,
    TARZAN_KEY_ENTER, TARZAN_KEY_TAB
};

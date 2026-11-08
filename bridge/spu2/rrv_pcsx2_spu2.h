// SPDX-License-Identifier: GPL-3.0+
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct rrv_spu2 rrv_spu2;
enum {
    RRV_SPU2_EVENT_SPU_IRQ = 1,
    RRV_SPU2_EVENT_DMA4_DONE = 4,
    RRV_SPU2_EVENT_DMA7_DONE = 7,
};
typedef struct rrv_spu2_event {
    uint64_t iop_cycle;
    uint32_t kind;
    uint32_t data;
} rrv_spu2_event;
#define RRV_SPU2_KEEP_TSA 0xFFFFFFFFu

// One instance and one core owner; the audio consumer may pull concurrently.
// All core calls carry monotonically increasing guest IOP cycles.
rrv_spu2* rrv_spu2_create(void);
void rrv_spu2_destroy(rrv_spu2* s);
void rrv_spu2_write16(rrv_spu2* s, uint64_t t, uint32_t reg_addr, uint16_t value);
uint16_t rrv_spu2_read16(rrv_spu2* s, uint64_t t, uint32_t reg_addr);
void rrv_spu2_dma_write(rrv_spu2* s, uint64_t t, int core, uint32_t spu_addr16, const uint16_t* data, uint32_t count16);
void rrv_spu2_dma_read(rrv_spu2* s, uint64_t t, int core, uint32_t spu_addr16, uint16_t* output, uint32_t count16);
size_t rrv_spu2_sync(rrv_spu2* s, uint64_t t, rrv_spu2_event* output, size_t max);
// Nonblocking stereo float pull; missing output is filled with silence.
size_t rrv_spu2_pull_output(rrv_spu2* s, float* output, size_t frames);
#ifdef __cplusplus
}
#endif

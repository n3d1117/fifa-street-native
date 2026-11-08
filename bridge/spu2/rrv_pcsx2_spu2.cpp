// SPDX-FileCopyrightText: 2002-2026 PCSX2 Dev Team
// SPDX-FileCopyrightText: 2026 RRV-Recomp contributors
// SPDX-License-Identifier: GPL-3.0+
//
// rrv_pcsx2_spu2 glue: replaces PCSX2's pcsx2/SPU2/spu2.cpp (2.8.2,
// fd9d310ccbb6b8b62c976da8886a3c8fd3a10ff3) and the IOP-side hooks the SPU2
// core calls (IopDma.cpp spu2DMA4Irq/spu2DMA7Irq, IopIrq.cpp spu2Irq).
//
// Adapted from upstream spu2.cpp: StereoOut32::Empty,
// lClocks, SPU2read(), SPU2write(), SPU2{read,write}DMA{4,7}Mem(),
// SPU2interruptDMA{4,7}(), SPU2::InternalReset(), DCFilter() and spu2Output().
// The AudioStream backend is replaced by a lock-free SPSC ring; the IOP
// CPU/DMA controller is replaced by timestamped commands.

#include "rrv_pcsx2_spu2.h"

#include "SPU2/defs.h"
#include "SPU2/regs.h"
#include "SPU2/spu2.h"
#include "IopDma.h"
#include "IopHw.h"
#include "IopMem.h"
#include "IopCounters.h"
#include "R3000A.h"

#include <atomic>
#include <array>
#include <algorithm>
#include <vector>
#include <cstring>


// ---------------------------------------------------------------------------
// Globals the PCSX2 SPU2 core expects from the rest of the emulator.
// ---------------------------------------------------------------------------

const StereoOut32 StereoOut32::Empty(0, 0); // upstream spu2.cpp
u64 lClocks = 0;                            // upstream spu2.cpp

psxRegisters psxRegs;
s32 psxNextDeltaCounter;
u64 psxNextStartCounter;
psxCounter psxCounters[NUM_COUNTERS];
u8 rrv_spu2_iopHw[0x10000];
u8 rrv_spu2_iopRam[RRV_SPU2_IOP_RAM_SIZE];

namespace
{
constexpr u32 kTickInterval = 768; // IOP cycles per 48 kHz frame (spu2sys.cpp TickInterval)
constexpr u64 kMaxStep = u64{kTickInterval} * 1024; // stays far below TimeUpdate's sanity clamp (768*4800)
constexpr u32 kChcrBusy = 0x01000000;
constexpr u32 kChcrToSpu = 0x01000201;   // psxDmaGeneric: IOP -> SPU2
constexpr u32 kChcrFromSpu = 0x01000200; // psxDmaGeneric: SPU2 -> IOP
constexpr u32 kStagingBytesPerCore = RRV_SPU2_IOP_RAM_SIZE / 2;
constexpr u32 kMaxDmaHalfwords = kStagingBytesPerCore / 2;

struct rrv_core_state;
rrv_core_state* g_state = nullptr; // core-thread view of the live instance
std::atomic<bool> g_instance_live{false};
// Core-thread-only state (touched by whichever thread runs the core).
struct rrv_core_state
{
	std::vector<rrv_spu2_event> events;
	bool in_mix = false;
	bool suppress_sinks = false;
	u32 irq_bits_seen = 0;
	float dc_in[2] = {0, 0};
	float dc_out[2] = {0, 0};
};

void push_event(u64 cycle, u32 kind, u32 data)
{
	if (!g_state || g_state->suppress_sinks)
		return;
	g_state->events.push_back(rrv_spu2_event{cycle, kind, data});
}

void rrv_mix()
{
    g_state->in_mix = true;
    isa_native::spu2Mix();
    g_state->in_mix = false;
}
} // namespace

// ---------------------------------------------------------------------------
// IOP interrupt sinks (upstream: IopIrq.cpp spu2Irq, IopDma.cpp spu2DMA4Irq/7).
// ---------------------------------------------------------------------------

// Upstream spu2.cpp SPU2interruptDMA4/7 (unchanged logic).
void SPU2interruptDMA4()
{
	if (Cores[0].DmaMode)
		Cores[0].Regs.STATX |= 0x80;
	Cores[0].Regs.STATX &= ~0x400;
	Cores[0].TSA = Cores[0].ActiveTSA;
}

void SPU2interruptDMA7()
{
	if (Cores[1].DmaMode)
		Cores[1].Regs.STATX |= 0x80;
	Cores[1].Regs.STATX &= ~0x400;
	Cores[1].TSA = Cores[1].ActiveTSA;
}

void spu2Irq()
{
	// SetIrqCall()/CheckDMAProgress() set Spdif.Info bit (4 << core) right
	// before calling us; report the bits that were not already reported.
	const u32 bits = (Spdif.Info >> 2) & 3;
	u32 fresh = bits & ~(g_state ? g_state->irq_bits_seen : 0);
	if (!fresh)
		fresh = bits;
	if (g_state)
		g_state->irq_bits_seen = bits;
	// Inside a mix tick the IRQ belongs to that tick's end cycle (lClocks);
	// otherwise to the current core time.
	const u64 cyc = (g_state && g_state->in_mix) ? lClocks : psxRegs.cycle;
	push_event(cyc, RRV_SPU2_EVENT_SPU_IRQ, fresh);
}

void spu2DMA4Irq()
{
	SPU2interruptDMA4();
	if (HW_DMA4_CHCR & kChcrBusy)
	{
		HW_DMA4_CHCR &= ~kChcrBusy;
		push_event(psxRegs.cycle, RRV_SPU2_EVENT_DMA4_DONE, 0);
	}
}

void spu2DMA7Irq()
{
	SPU2interruptDMA7();
	if (HW_DMA7_CHCR & kChcrBusy)
	{
		HW_DMA7_CHCR &= ~kChcrBusy;
		push_event(psxRegs.cycle, RRV_SPU2_EVENT_DMA7_DONE, 0);
	}
}

// ---------------------------------------------------------------------------
// Upstream spu2.cpp entry points (adapted: no logging, no AudioStream).
// ---------------------------------------------------------------------------

void SPU2readDMA4Mem(u16* pMem, u32 size)
{
	TimeUpdate(psxRegs.cycle);
	Cores[0].DoDMAread(pMem, size);
}

void SPU2writeDMA4Mem(u16* pMem, u32 size)
{
	TimeUpdate(psxRegs.cycle);
	Cores[0].DoDMAwrite(pMem, size);
}

void SPU2readDMA7Mem(u16* pMem, u32 size)
{
	TimeUpdate(psxRegs.cycle);
	Cores[1].DoDMAread(pMem, size);
}

void SPU2writeDMA7Mem(u16* pMem, u32 size)
{
	TimeUpdate(psxRegs.cycle);
	Cores[1].DoDMAwrite(pMem, size);
}

// Upstream spu2.cpp: true only after SPU2::Reset(true) (PS1 mode), which RRV never does.
bool SPU2::IsRunningPSXMode()
{
	return false;
}

void SPU2async()
{
	TimeUpdate(psxRegs.cycle);
}

u16 SPU2read(u32 rmem)
{
	u16 ret = 0xDEAD;
	u32 core = 0;
	const u32 mem = rmem & 0xFFFF;
	u32 omem = mem;

	if (mem & 0x400)
	{
		omem ^= 0x400;
		core = 1;
	}

	// Upstream compares the 16-bit offset with a full address, so this branch
	// never fires in PCSX2 2.8.2 either; kept verbatim for equivalence.
	if (omem == 0x1f9001AC)
	{
		Cores[core].ActiveTSA = Cores[core].TSA;
		for (int i = 0; i < 2; i++)
		{
			if (Cores[i].IRQEnable && (Cores[i].IRQA == Cores[core].ActiveTSA))
				SetIrqCall(i);
		}
		ret = Cores[core].DmaRead();
	}
	else
	{
		TimeUpdate(psxRegs.cycle);

		if (rmem >> 16 == 0x1f80)
			ret = Cores[0].ReadRegPS1(rmem);
		else if (mem >= 0x800)
			ret = spu2Ru16(mem);
		else
			ret = *(regtable[(mem >> 1)]);
	}

	return ret;
}

void SPU2write(u32 rmem, u16 value)
{
	TimeUpdate(psxRegs.cycle);

	if (rmem >> 16 == 0x1f80)
		Cores[0].WriteRegPS1(rmem, value);
	else
		SPU2_FastWrite(rmem, value);
}

// Upstream spu2.cpp DCFilter() + spu2Output(), writing to our ring instead of
// AudioStream::WriteChunk. Declared in SPU2/defs.h; called once per frame.
namespace rrv_out
{
void push_frame(float l, float r);
}

void spu2Output(StereoOut32 out)
{
	rrv_core_state& st = *g_state;
	const s32 cl = clamp_mix(out.Left);
	const s32 cr = clamp_mix(out.Right);

	float conv[2];
	conv[0] = static_cast<float>(cl) / INT16_MAX;
	conv[1] = static_cast<float>(cr) / INT16_MAX;

	// DC blocking high-pass filter (upstream DCFilter()).
	float o0 = (conv[0] - st.dc_in[0] + ((0.995f * st.dc_out[0])));
	float o1 = (conv[1] - st.dc_in[1] + ((0.995f * st.dc_out[1])));
	st.dc_in[0] = conv[0];
	st.dc_in[1] = conv[1];
	st.dc_out[0] = o0;
	st.dc_out[1] = o1;

	rrv_out::push_frame(o0, o1);
}

// ---------------------------------------------------------------------------
// Instance
// ---------------------------------------------------------------------------

struct rrv_spu2
{
    u64 last_t = 0;
    rrv_core_state core;
    std::array<float, 16384 * 2> ring{};
    alignas(64) std::atomic<u64> ring_head{0};
    alignas(64) std::atomic<u64> ring_tail{0};
    bool output_primed = false;
};

namespace
{
rrv_spu2* g_inst = nullptr;
constexpr u64 kRingMask = 16384 - 1;
constexpr u64 kPrimeFrames = 4608;
}

void rrv_out::push_frame(float l, float r)
{
    rrv_spu2* s = g_inst;
    const u64 head = s->ring_head.load(std::memory_order_relaxed);
    const u64 tail = s->ring_tail.load(std::memory_order_acquire);
    if (head - tail > kRingMask)
        return;
    const u64 index = (head & kRingMask) * 2u;
    s->ring[index] = l;
    s->ring[index + 1u] = r;
    s->ring_head.store(head + 1u, std::memory_order_release);
}

namespace
{
// Adapted from upstream spu2.cpp SPU2::InternalReset(false) + SPU2::Open(),
// plus a full power-on clear so a second instance in the same process starts
// from exactly the state the first one did.
void core_power_on(rrv_core_state& st)
{
	// spu2sys.cpp keeps `static bool has_to_call_irq_dma[2]`, which no reset
	// clears. It is cleared when a DMA countdown expires, so run one 1-cycle
	// countdown on core 0 with IRQs disabled and sinks suppressed.
	st.suppress_sinks = true;
	Cores[0].IRQEnable = false;
	Cores[1].IRQEnable = false;
	Cores[0].AutoDMACtrl = 0;
	Cores[0].ReadSize = 0;
	Cores[0].DMAICounter = 1;
	Cores[0].LastClock = 0;
	lClocks = 0;
	psxRegs.cycle = 1;
	TimeUpdate(psxRegs.cycle);
	st.suppress_sinks = false;

	std::memset(static_cast<void*>(Cores), 0, sizeof(Cores));
	std::memset(static_cast<void*>(DebugCores), 0, sizeof(DebugCores));
	std::memset(&Spdif, 0, sizeof(Spdif));
	std::memset(static_cast<void*>(pcm_cache_data), 0, sizeof(PcmCacheEntry) * pcm_BlockCount);
	std::memset(rrv_spu2_iopHw, 0, sizeof(rrv_spu2_iopHw));
	std::memset(rrv_spu2_iopRam, 0, sizeof(rrv_spu2_iopRam));
	std::memset(psxCounters, 0, sizeof(psxCounters));
	psxNextDeltaCounter = 0;
	psxNextStartCounter = 0;
	OutPos = 0;
	InputPos = 0;
	Cycles = 0;
	PlayMode = 0;
	psxRegs.cycle = 0;
	lClocks = psxRegs.cycle; // SPU2::Open()

	// SPU2::InternalReset(false)
	spu2Mix = rrv_mix; // wraps isa_native::spu2Mix to tag IRQs raised inside a tick
	ReverbDownsample = MULTI_ISA_SELECT(ReverbDownsample);
	ReverbUpsample = MULTI_ISA_SELECT(ReverbUpsample);
	std::memset(spu2regs, 0, 0x010000);
	std::memset(_spu2mem, 0, 0x200000);
	std::memset(_spu2mem + 0x2800, 7, 0x10); // from BIOS reversal. Locks the voices so they don't run free.
	std::memset(_spu2mem + 0xe870, 7, 0x10); // Loop which gets left over by the BIOS, Megaman X7 relies on it being there.
	st.dc_in[0] = st.dc_in[1] = 0;
	st.dc_out[0] = st.dc_out[1] = 0;
	Spdif.Info = 0;
	Cores[0].Init(0);
	Cores[1].Init(1);

	st.events.clear();
	st.irq_bits_seen = 0;
}

// Run the core to guest cycle t. Steps stop at each pending DMA countdown
// deadline, which is what PCSX2's IOP counter 6 (CounterUpdate) schedules, so
// DMA completion events land on their exact guest cycle regardless of how the
// caller batches its calls.
void run_to(u64 t)
{
	while (psxRegs.cycle < t)
	{
		u64 next = std::min<u64>(t, psxRegs.cycle + kMaxStep);
		for (int c = 0; c < 2; c++)
		{
			if (Cores[c].DMAICounter > 0)
			{
				const u64 dl = Cores[c].LastClock + static_cast<u64>(Cores[c].DMAICounter);
				if (dl > psxRegs.cycle && dl < next)
					next = dl;
			}
		}
		psxRegs.cycle = next;
		TimeUpdate(psxRegs.cycle);
	}
}

void write_tsa(int core, u32 addr)
{
	const u32 base = 0x1F900000u + (core ? 0x400u : 0u);
	SPU2write(base + REG_A_TSA, static_cast<u16>((addr >> 16) & 0xF));
	SPU2write(base + REG_A_TSA + 2, static_cast<u16>(addr & 0xFFFF));
}

u32 norm_reg(u32 a)
{
	return (a < 0x10000u) ? (a | 0x1F900000u) : a;
}

u64 stamp(rrv_spu2* s, u64 t)
{
    s->last_t = std::max(t, s->last_t);
    return s->last_t;
}

void start_dma(rrv_spu2* s, u64 t, int core, u32 spu_addr16, const u16* input, u16* output, u32 count16)
{
    run_to(stamp(s, t));
    core = core ? 1 : 0;
    count16 = std::min(count16, kMaxDmaHalfwords);
    if (spu_addr16 != RRV_SPU2_KEEP_TSA)
        write_tsa(core, spu_addr16);
    const u32 staging = core ? kStagingBytesPerCore : 0;
    u16* mem = reinterpret_cast<u16*>(iopPhysMem(staging));
    (core ? HW_DMA7_MADR : HW_DMA4_MADR) = staging;
    (core ? HW_DMA7_BCR : HW_DMA4_BCR) = (count16 + 1) / 2 | (1u << 16);
    u32& chcr = core ? HW_DMA7_CHCR : HW_DMA4_CHCR;
    if (input) {
        std::memcpy(mem, input, count16 * sizeof(u16));
        chcr = kChcrToSpu;
        if (core) SPU2writeDMA7Mem(mem, count16);
        else SPU2writeDMA4Mem(mem, count16);
    } else {
        const u32 address = Cores[core].TSA & 0xFFFFF;
        for (u32 i = 0; i < count16; ++i)
            output[i] = static_cast<u16>(_spu2mem[(address + i) & 0xFFFFF]);
        chcr = kChcrFromSpu;
        if (core) SPU2readDMA7Mem(mem, count16);
        else SPU2readDMA4Mem(mem, count16);
    }
    s->core.irq_bits_seen = (Spdif.Info >> 2) & 3;
}
} // namespace

extern "C" {
rrv_spu2* rrv_spu2_create(void)
{
    bool expected = false;
    if (!g_instance_live.compare_exchange_strong(expected, true))
        return nullptr;
    rrv_spu2* s = new rrv_spu2();
    g_inst = s;
    g_state = &s->core;
    core_power_on(s->core);
    return s;
}

void rrv_spu2_destroy(rrv_spu2* s)
{
    if (!s) return;
    g_state = nullptr;
    g_inst = nullptr;
    delete s;
    g_instance_live.store(false);
}

void rrv_spu2_write16(rrv_spu2* s, uint64_t t, uint32_t reg_addr, uint16_t value)
{
    run_to(stamp(s, t));
    SPU2write(norm_reg(reg_addr), value);
    s->core.irq_bits_seen = (Spdif.Info >> 2) & 3;
}

uint16_t rrv_spu2_read16(rrv_spu2* s, uint64_t t, uint32_t reg_addr)
{
    run_to(stamp(s, t));
    const u16 result = SPU2read(norm_reg(reg_addr));
    s->core.irq_bits_seen = (Spdif.Info >> 2) & 3;
    return result;
}

void rrv_spu2_dma_write(rrv_spu2* s, uint64_t t, int core, uint32_t spu_addr16, const uint16_t* data, uint32_t count16)
{
    start_dma(s, t, core, spu_addr16, data, nullptr, count16);
}

void rrv_spu2_dma_read(rrv_spu2* s, uint64_t t, int core, uint32_t spu_addr16, uint16_t* output, uint32_t count16)
{
    start_dma(s, t, core, spu_addr16, nullptr, output, count16);
}

size_t rrv_spu2_sync(rrv_spu2* s, uint64_t t, rrv_spu2_event* output, size_t max)
{
    run_to(stamp(s, t));
    s->core.irq_bits_seen = (Spdif.Info >> 2) & 3;
    auto& events = s->core.events;
    const size_t count = std::min(max, events.size());
    std::copy_n(events.begin(), count, output);
    events.erase(events.begin(), events.begin() + static_cast<std::ptrdiff_t>(count));
    return count;
}

size_t rrv_spu2_pull_output(rrv_spu2* s, float* output, size_t frames)
{
    const u64 tail = s->ring_tail.load(std::memory_order_relaxed);
    const u64 head = s->ring_head.load(std::memory_order_acquire);
    if (!s->output_primed) {
        if (head - tail < kPrimeFrames) {
            std::memset(output, 0, frames * 2 * sizeof(float));
            return 0;
        }
        s->output_primed = true;
    }
    const size_t count = static_cast<size_t>(std::min<u64>(head - tail, frames));
    for (size_t i = 0; i < count; ++i) {
        const u64 index = ((tail + i) & kRingMask) * 2;
        output[i * 2] = s->ring[index];
        output[i * 2 + 1] = s->ring[index + 1];
    }
    s->ring_tail.store(tail + count, std::memory_order_release);
    if (count < frames)
        std::memset(output + count * 2, 0, (frames - count) * 2 * sizeof(float));
    return count;
}
} // extern "C"

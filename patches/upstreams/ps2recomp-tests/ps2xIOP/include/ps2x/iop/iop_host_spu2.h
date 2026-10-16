#pragma once

#include <cstdint>

// Optional host SPU2 implementation. Install before the IOP starts. Hooks are called on the EE/IOP emulation thread.
// Register addresses are physical IOP addresses in 0x1F900000..0x1F9007FF. DMA buffers are live pointers into IOP
// RAM and remain valid while the emulator exists; halfwords is the clamped transfer length. IOP cycles are monotonic
// across IOP resets. Without installed hooks, existing stub behavior is retained.
namespace ps2x::iop
{
    struct IopHostSpu2
    {
        uint16_t (*read16)(uint32_t physAddr, uint64_t iopCycle) = nullptr;
        void (*write16)(uint32_t physAddr, uint16_t value, uint64_t iopCycle) = nullptr;
        // core 0 = DMA channel 4, core 1 = channel 7. toSpu2 is CHCR bit 0 (IOP RAM -> SPU2 RAM).
        // The host calls iopSpu2DmaComplete(core) only after the transfer has actually finished.
        void (*dmaStart)(int core, uint16_t *ram, uint32_t madr, uint32_t halfwords,
                         bool toSpu2, uint64_t iopCycle) = nullptr;
        uint32_t (*dmaMadr)(int core) = nullptr;
        void (*advance)(uint64_t iopCycle) = nullptr;
        uint64_t (*nextEvent)() = nullptr; // monotonic IOP cycle; UINT64_MAX means none
        uint64_t maxIdleStep = 768u * 32u;
    };

    void iopSetHostSpu2(const IopHostSpu2 &hooks);
    const IopHostSpu2 &iopHostSpu2();
    void iopSpu2RaiseIrq();
    void iopSpu2DmaComplete(int core);
}

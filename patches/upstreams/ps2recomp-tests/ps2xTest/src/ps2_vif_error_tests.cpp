#include "MiniTest.h"
#include "runtime/ps2_memory.h"

#include <cstdint>
#include <cstring>
#include <vector>

namespace
{
    constexpr uint32_t kVifErrReg = 0x10003C20u;
    constexpr uint32_t kVifFbrstReg = 0x10003C10u;
    constexpr uint32_t kVifStatEr1 = 1u << 13u;
    constexpr uint32_t kVifStatInt = 1u << 11u;
    constexpr uint32_t kVifStatVis = 1u << 10u;

    void appendWord(std::vector<uint8_t> &bytes, uint32_t value)
    {
        const size_t offset = bytes.size();
        bytes.resize(offset + sizeof(value));
        std::memcpy(bytes.data() + offset, &value, sizeof(value));
    }

    std::vector<uint8_t> invalidThenStcycl(bool irq)
    {
        std::vector<uint8_t> bytes;
        appendWord(bytes, (irq ? 0x80000000u : 0u) | 0x40000000u); // Reserved opcode 0x40.
        appendWord(bytes, 0x01010102u); // STCYCL, WL=1, CL=2.
        return bytes;
    }
}

void register_ps2_vif_error_tests()
{
    MiniTest::Case("VIF1Error", [](TestCase &tc)
    {
        tc.Run("invalid opcode with I sets ER1 without IRQ and STC resumes tail", [](TestCase &t)
        {
            PS2Memory mem;
            t.IsTrue(mem.initialize(), "PS2Memory initialize should succeed");
            mem.m_ioRegisters[0x10009000u] = 0x100u; // Active VIF1 DMA: VifStallEnable is true.
            const auto packet = invalidThenStcycl(true);
            mem.processVIF1Data(packet.data(), static_cast<uint32_t>(packet.size()));

            t.IsTrue((mem.vif1_regs.stat & kVifStatEr1) != 0u, "invalid opcode should set ER1");
            t.IsFalse((mem.vif1_regs.stat & (kVifStatInt | kVifStatVis)) != 0u,
                      "invalid opcode I bit must not raise VIF interrupt status");
            t.IsTrue(mem.m_vif1Stalled, "invalid opcode should stall VIF1 until STC");
            t.Equals(mem.vif1_regs.cycle, 0u, "command tail must wait while ER1 is stalled");
            t.IsTrue(mem.takeCompletedIntcInterrupts().empty(), "invalid opcode must not queue INTC5");

            mem.writeIORegister(kVifFbrstReg, 0x8u);
            t.IsFalse(mem.m_vif1Stalled, "STC should release the invalid-command stall");
            t.Equals(mem.vif1_regs.cycle, 0x0102u, "STC should resume and execute the buffered tail");
        });

        tc.Run("invalid opcode without I sets ER1 and STC resumes tail", [](TestCase &t)
        {
            PS2Memory mem;
            t.IsTrue(mem.initialize(), "PS2Memory initialize should succeed");
            mem.m_ioRegisters[0x10009000u] = 0x100u; // Active VIF1 DMA: VifStallEnable is true.
            const auto packet = invalidThenStcycl(false);
            mem.processVIF1Data(packet.data(), static_cast<uint32_t>(packet.size()));

            t.IsTrue((mem.vif1_regs.stat & kVifStatEr1) != 0u, "invalid opcode should set ER1 without I");
            t.IsTrue(mem.m_vif1Stalled, "invalid opcode should stall independent of I bit");
            t.IsTrue(mem.takeCompletedIntcInterrupts().empty(), "invalid opcode without I must not queue INTC5");
            mem.writeIORegister(kVifFbrstReg, 0x8u);
            t.Equals(mem.vif1_regs.cycle, 0x0102u, "STC should resume and execute the buffered tail");
        });

        tc.Run("ERR.ME1 masks invalid opcode and continues without IRQ", [](TestCase &t)
        {
            PS2Memory mem;
            t.IsTrue(mem.initialize(), "PS2Memory initialize should succeed");
            t.IsTrue(mem.writeIORegister(kVifErrReg, 1u << 2u), "ERR.ME1 should be writable");
            const auto packet = invalidThenStcycl(true);
            mem.processVIF1Data(packet.data(), static_cast<uint32_t>(packet.size()));

            t.IsFalse((mem.vif1_regs.stat & (kVifStatEr1 | kVifStatInt | kVifStatVis)) != 0u,
                      "ME1 should suppress ER1 and VIF interrupt status");
            t.IsFalse(mem.m_vif1Stalled, "ME1 should prevent an error stall");
            t.Equals(mem.vif1_regs.cycle, 0x0102u, "ME1 should allow the following command to execute");
            t.IsTrue(mem.takeCompletedIntcInterrupts().empty(), "ME1-masked invalid I must not queue INTC5");
        });
    });
}

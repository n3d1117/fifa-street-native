#include "ps2_runtime.h"
#include "ps2_syscalls.h"
#include "runtime/ee_scheduler.h"

#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>

void _sceFsSemInit_0x2250b8(uint8_t *, R5900Context *, PS2Runtime *);
void _sceFsSigSema_0x225138(uint8_t *, R5900Context *, PS2Runtime *);
void CreateSema_0x2213a0(uint8_t *, R5900Context *, PS2Runtime *);
void SignalSema_0x2213c0(uint8_t *, R5900Context *, PS2Runtime *);

int main()
{
    try
    {
        std::vector<uint8_t> ram(PS2_RAM_SIZE, 0);
        R5900Context ctx{};
        PS2Runtime runtime;
        constexpr uint32_t slot = 0x49f94cu;
        constexpr uint32_t stack = 0x100000u;
        const auto require = [](bool ok, const char *message)
        {
            if (!ok) throw std::runtime_error(message);
        };
        const auto read = [&](uint32_t address)
        {
            uint32_t value;
            std::memcpy(&value, ram.data() + address, sizeof(value));
            return value;
        };
        const uint32_t uninitialized = 0xffffffffu;
        std::memcpy(ram.data() + slot, &uninitialized, sizeof(uninitialized));
        ctx.r[29] = _mm_set_epi64x(0, stack);
        ctx.r[31] = _mm_set_epi64x(0, 0x1000u);
        g_ps2RecompiledFunctionTable[0x2213a0u >> 2] = CreateSema_0x2213a0;
        g_ps2RecompiledFunctionTable[0x2213c0u >> 2] = SignalSema_0x2213c0;

        _sceFsSemInit_0x2250b8(ram.data(), &ctx, &runtime);
        const int id = static_cast<int>(read(slot));
        const auto *sema = runtime.eeScheduler().semaphore(id);
        require(id > 0 && sema, "init must store a real EE semaphore ID in the guest slot");
        require(sema->initCount == 1 && sema->maxCount == 1 && sema->count == 1,
                "FileIO semaphore must start available with capacity one");
        require(getRegU32(&ctx, 2) == static_cast<uint32_t>(id), "init result must be the stored ID");
        require(getRegU32(&ctx, 29) == stack, "init must restore the guest stack");

        require(runtime.eeScheduler().pollSemaphore(id) == id, "consume initial token");
        _sceFsSemInit_0x2250b8(ram.data(), &ctx, &runtime);
        require(read(slot) == static_cast<uint32_t>(id) && sema->count == 0,
                "repeated init must preserve the semaphore and its count");
        require(getRegU32(&ctx, 2) == static_cast<uint32_t>(id), "repeated init must return the existing ID");
        ctx.r[4] = _mm_set_epi64x(0, 0x7fffffffu);
        _sceFsSigSema_0x225138(ram.data(), &ctx, &runtime);
        require(getRegU32(&ctx, 2) == static_cast<uint32_t>(id) && sema->count == 1,
                "signal must use the guest slot, ignore a0 and restore the token");
        _sceFsSigSema_0x225138(ram.data(), &ctx, &runtime);
        require(static_cast<int32_t>(getRegU32(&ctx, 2)) == -420 && sema->count == 1,
                "signal must propagate semaphore overflow without adding a token");
        std::puts("PASS: FileIO guest initialization, reuse, signal and overflow");
        return 0;
    }
    catch (const std::exception &error)
    {
        std::fprintf(stderr, "FAIL: %s\n", error.what());
        return 1;
    }
}

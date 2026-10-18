#include "ps2_runtime.h"

#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>

void _sceRpcFreePacket_0x223eb8(uint8_t *, R5900Context *, PS2Runtime *);
void _sceRpcGetFPacket_0x223ed8(uint8_t *, R5900Context *, PS2Runtime *);
void _sceRpcGetFPacket2_0x223f08(uint8_t *, R5900Context *, PS2Runtime *);

int main()
{
    try
    {
        std::vector<uint8_t> ram(PS2_RAM_SIZE, 0);
        R5900Context ctx{};
        PS2Runtime runtime;
        constexpr uint32_t descriptor = 0x20000u;
        constexpr uint32_t freePool = 0x20030000u;
        constexpr uint32_t indexedPool = 0x20040000u;
        const auto require = [](bool ok, const char *message)
        {
            if (!ok) throw std::runtime_error(message);
        };
        const auto write = [&](uint32_t address, uint32_t value)
        {
            std::memcpy(ram.data() + address, &value, sizeof(value));
        };
        const auto read = [&](uint32_t address)
        {
            uint32_t value;
            std::memcpy(&value, ram.data() + address, sizeof(value));
            return value;
        };
        const auto call = [&](PS2Runtime::RecompiledFunction fn, uint32_t pointer, uint32_t index = 0)
        {
            ctx.r[4] = _mm_set_epi64x(0, pointer);
            ctx.r[5] = _mm_set_epi64x(0, index);
            ctx.r[29] = _mm_set_epi64x(0, 0x100000u);
            ctx.r[31] = _mm_set_epi64x(0, 0x1000u);
            fn(ram.data(), &ctx, &runtime);
            require(getRegU32(&ctx, 29) == 0x100000u, "packet helper must restore the stack");
            return getRegU32(&ctx, 2);
        };
        g_ps2RecompiledFunctionTable[0x223ed8u >> 2] = _sceRpcGetFPacket_0x223ed8;
        write(descriptor + 0x14, freePool);
        write(descriptor + 0x18, 3);
        write(descriptor + 0x1c, indexedPool);
        write(descriptor + 0x20, 2);
        write(descriptor + 0x24, 0);
        for (uint32_t i = 0; i < 7; ++i)
        {
            require(call(_sceRpcGetFPacket_0x223ed8, descriptor | (i % 2 ? 0x20000000u : 0)) == freePool + (i % 3) * 64,
                    "free packet selection must wrap in 64-byte slots and preserve pointer aliases");
            require(read(descriptor + 0x24) == (i % 3) + 1, "cursor must store remainder plus one");
        }
        const uint32_t cursor = read(descriptor + 0x24);
        require(call(_sceRpcGetFPacket2_0x223f08, descriptor, 0) == indexedPool,
                "index zero must use the indexed pool");
        require(call(_sceRpcGetFPacket2_0x223f08, descriptor, 1) == indexedPool + 64,
                "last valid index must use the indexed pool");
        require(read(descriptor + 0x24) == cursor, "indexed selection must not change the free cursor");
        require(call(_sceRpcGetFPacket2_0x223f08, descriptor, 2) == freePool + 64,
                "out-of-range index must fall back to the rotating pool");
        require(call(_sceRpcGetFPacket2_0x223f08, descriptor, 0xffffffffu) == freePool + 128,
                "negative index must fall back to the rotating pool");

        write(descriptor + 0x24, 0xffffffffu);
        require(call(_sceRpcGetFPacket_0x223ed8, descriptor) == freePool - 64,
                "rotation must use signed guest remainder");
        require(read(descriptor + 0x24) == 0, "negative remainder must advance back to zero");

        constexpr uint32_t packet = 0x50000u;
        for (uint32_t offset = 0; offset < 64; offset += 4) write(packet + offset, 0xa5a5a5a5u);
        call(_sceRpcFreePacket_0x223eb8, packet | 0x20000000u);
        for (uint32_t offset = 0; offset < 64; offset += 4)
        {
            const uint32_t expected = offset == 0x18 ? 0 : offset == 0x10 ? 0xa5a5a5a4u : 0xa5a5a5a5u;
            require(read(packet + offset) == expected, "free must clear only flag bit zero and the word at offset 0x18");
        }
        call(_sceRpcFreePacket_0x223eb8, packet);
        require(read(packet + 0x10) == 0xa5a5a5a4u && read(packet + 0x18) == 0,
                "freeing an already free packet must preserve the other flag bits");
        std::puts("PASS: RPC guest packet free, rotation, indexed selection and fallback");
        return 0;
    }
    catch (const std::exception &error)
    {
        std::fprintf(stderr, "FAIL: %s\n", error.what());
        return 1;
    }
}

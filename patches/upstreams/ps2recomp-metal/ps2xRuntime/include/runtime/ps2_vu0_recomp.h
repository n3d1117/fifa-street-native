#pragma once
// Exact-image native VU0 dispatch. Unknown code continues through the interpreter.
#include "runtime/ps2_vu1.h"
#include <cstddef>
#include <cstdint>
struct Vu0RecContext {
    VU1State* state = nullptr;
    uint8_t* data = nullptr;
    uint64_t cycle = 0;
    uint32_t maxCycles = 4096;
    uint32_t top = 0, itop = 0;
    bool failed = false;
    uint32_t failPc = 0;
    uint8_t backupReg = 0;
    int32_t backupValue = 0;
};
using Vu0RecFn = void (*)(Vu0RecContext&, uint32_t);
struct Vu0RecImage {
    uint64_t hash;
    const uint32_t* entries;
    size_t entryCount;
    Vu0RecFn full;
};
void vu0recRegister(const Vu0RecImage& image);
const Vu0RecImage* vu0recFind(uint64_t hash);
struct Vu0RecRegistrar {
    Vu0RecRegistrar(uint64_t hash, const uint32_t* entries, size_t count,
                    Vu0RecFn, Vu0RecFn full, bool = false) {
        vu0recRegister({hash, entries, count, full});
    }
};
// The caller has reset/imported state as for the original interpreter path.
// Differential mode keeps the interpreter result, including on a mismatch.
bool vu0recExecute(VU1Interpreter& vu, uint8_t* code, uint8_t* data,
                   GS& gs, PS2Memory& memory, uint32_t pc, uint32_t itop);

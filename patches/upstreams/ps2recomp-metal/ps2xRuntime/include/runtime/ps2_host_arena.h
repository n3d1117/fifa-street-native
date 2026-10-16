#pragma once

#include <cstdint>

// Configure guest-RAM ranges reserved for runtime-owned heap allocations and asynchronous callback stacks.
// The caller must set this before loadELF() and ensure neither range overlaps any guest ELF PT_LOAD segment.
// Ranges are half-open, 16-byte aligned, inside EE RDRAM, and must not overlap each other.
// Passing four zeroes disables the override.
void ps2SetRuntimeArena(uint32_t heapBase, uint32_t heapLimit, uint32_t stackFloor, uint32_t stackTop);

// Either range may be disabled independently by passing zero for both bounds.

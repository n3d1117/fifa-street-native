// Offline VU0 equivalence and microbenchmark for an exact captured image.
// Usage: vu0_replay <code.bin> <data.before.bin> <state.before.txt>
//        [--fuzz N] [--bench N]

#include "runtime/ps2_memory.h"
#include "runtime/ps2_vu0_recomp.h"
#include "runtime/ps2_vu1.h"
#include "runtime/gs/gs_frontend.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <random>
#include <string>
#include <vector>

namespace
{
    struct Result
    {
        VU1State state{};
        std::vector<uint8_t> data;
        uint64_t cycle = 0;
        bool failed = false;
        uint32_t failPc = 0;
    };

    uint32_t floatBits(float value)
    {
        uint32_t bits;
        std::memcpy(&bits, &value, sizeof(bits));
        return bits;
    }

    uint64_t codeHash(const uint8_t *code)
    {
        // Match vu1decode.fnv1a64 and the VU1 registry's established key.
        uint64_t hash = 1469598103934665603ull;
        for (uint32_t i = 0; i < PS2_VU0_CODE_SIZE; ++i)
        {
            hash ^= code[i];
            hash *= 1099511628211ull;
        }
        return hash;
    }

    bool loadStateText(const std::string &path, VU1State &state)
    {
        std::memset(&state, 0, sizeof(state));
        state.r = 0x3F800000u;
        std::ifstream input(path);
        if (!input)
            return false;

        bool haveSummary = false;
        std::string line;
        while (std::getline(input, line))
        {
            unsigned index = 0;
            float lanes[4]{};
            if (std::sscanf(line.c_str(), "vf[%u]=%f,%f,%f,%f", &index,
                            &lanes[0], &lanes[1], &lanes[2], &lanes[3]) == 5 && index < 32)
            {
                std::memcpy(state.vf[index], lanes, sizeof(lanes));
                continue;
            }
            int vi = 0;
            if (std::sscanf(line.c_str(), "vi[%u]=%d", &index, &vi) == 2 && index < 16)
            {
                state.vi[index] = vi;
                continue;
            }
            unsigned r = 0, pc = 0, mac = 0, clip = 0, status = 0;
            unsigned long long cycles = 0;
            int ebit = 0, halt = 0, stoppedD = 0, stoppedT = 0;
            if (std::sscanf(line.c_str(),
                            "acc=%f,%f,%f,%f q=%f p=%f i=%f r=0x%x pc=0x%x mac=0x%x clip=0x%x status=0x%x cycles=%llu ebit=%d haltAfterDelaySlot=%d stoppedByD=%d stoppedByT=%d",
                            &state.acc[0], &state.acc[1], &state.acc[2], &state.acc[3],
                            &state.q, &state.p, &state.i, &r, &pc, &mac, &clip, &status,
                            &cycles, &ebit, &halt, &stoppedD, &stoppedT) == 17)
            {
                state.r = r;
                state.pc = pc;
                state.mac = mac;
                state.clip = clip;
                state.status = status;
                state.cycles = cycles;
                state.ebit = ebit != 0;
                state.haltAfterDelaySlot = halt != 0;
                state.stoppedByD = stoppedD != 0;
                state.stoppedByT = stoppedT != 0;
                haveSummary = true;
            }
        }
        state.vi[0] = 0;
        state.vf[0][0] = state.vf[0][1] = state.vf[0][2] = 0.0f;
        state.vf[0][3] = 1.0f;
        return haveSummary && !input.bad();
    }

    void prepareState(VU1State &state, uint32_t pc)
    {
        state.pc = pc & (PS2_VU0_CODE_SIZE - 1u);
        state.ebit = false;
        state.haltAfterDelaySlot = false;
        state.stoppedByD = false;
        state.stoppedByT = false;
        state.top = 0;
        state.branchPending = false;
        state.branchTarget = 0;
        state.branchDelay = 0;
        state.vf[0][0] = state.vf[0][1] = state.vf[0][2] = 0.0f;
        state.vf[0][3] = 1.0f;
        state.vi[0] = 0;
        state.cycles = 0;
    }

    Result runInterpreter(const std::vector<uint8_t> &code, const VU1State &input,
                          const std::vector<uint8_t> &data, uint32_t pc, GS &gs)
    {
        Result result;
        result.state = input;
        result.data = data;
        prepareState(result.state, pc);
        VU1Interpreter vu(VU1Interpreter::Unit::VU0);
        vu.state() = result.state;
        vu.setCycleCount(0);
        vu.execute(const_cast<uint8_t *>(code.data()), PS2_VU0_CODE_SIZE,
                   result.data.data(), PS2_VU0_DATA_SIZE, gs, nullptr,
                   pc, 0, input.itop, 4096);
        result.state = vu.state();
        result.cycle = vu.cycleCount();
        return result;
    }

    Result runNative(Vu0RecFn fn, const VU1State &input,
                     const std::vector<uint8_t> &data, uint32_t pc)
    {
        Result result;
        result.state = input;
        result.data = data;
        prepareState(result.state, pc);
        Vu0RecContext context;
        context.state = &result.state;
        context.data = result.data.data();
        context.cycle = 0;
        context.maxCycles = 4096;
        context.top = 0;
        context.itop = input.itop;
        fn(context, pc);
        result.cycle = context.cycle;
        result.state.cycles = context.cycle;
        result.failed = context.failed;
        result.failPc = context.failPc;
        return result;
    }

    bool compareState(const VU1State &a, const VU1State &b, std::string &field)
    {
        for (int r = 0; r < 32; ++r)
            for (int c = 0; c < 4; ++c)
                if (floatBits(a.vf[r][c]) != floatBits(b.vf[r][c]))
                {
                    char name[32];
                    std::snprintf(name, sizeof(name), "vf[%d].%c", r, "xyzw"[c]);
                    field = name;
                    return false;
                }
        for (int c = 0; c < 4; ++c)
            if (floatBits(a.acc[c]) != floatBits(b.acc[c]))
            {
                char name[32];
                std::snprintf(name, sizeof(name), "acc.%c", "xyzw"[c]);
                field = name;
                return false;
            }
        for (int r = 0; r < 16; ++r)
            if (a.vi[r] != b.vi[r])
            {
                char name[24];
                std::snprintf(name, sizeof(name), "vi[%d]", r);
                field = name;
                return false;
            }
#define VU0_COMPARE_FLOAT(fieldName) \
        if (floatBits(a.fieldName) != floatBits(b.fieldName)) { field = #fieldName; return false; }
        VU0_COMPARE_FLOAT(q)
        VU0_COMPARE_FLOAT(p)
        VU0_COMPARE_FLOAT(i)
#undef VU0_COMPARE_FLOAT
#define VU0_COMPARE_FIELD(fieldName) \
        if (a.fieldName != b.fieldName) { field = #fieldName; return false; }
        VU0_COMPARE_FIELD(r)
        VU0_COMPARE_FIELD(pc)
        VU0_COMPARE_FIELD(mac)
        VU0_COMPARE_FIELD(clip)
        VU0_COMPARE_FIELD(status)
        VU0_COMPARE_FIELD(cycles)
        VU0_COMPARE_FIELD(ebit)
        VU0_COMPARE_FIELD(haltAfterDelaySlot)
        VU0_COMPARE_FIELD(dBitEnabled)
        VU0_COMPARE_FIELD(tBitEnabled)
        VU0_COMPARE_FIELD(stoppedByD)
        VU0_COMPARE_FIELD(stoppedByT)
        VU0_COMPARE_FIELD(top)
        VU0_COMPARE_FIELD(itop)
        VU0_COMPARE_FIELD(branchPending)
        VU0_COMPARE_FIELD(branchTarget)
        VU0_COMPARE_FIELD(branchDelay)
#undef VU0_COMPARE_FIELD
        return true;
    }

    bool compareResults(const Result &native, const Result &reference,
                        size_t caseIndex, uint32_t entryPc)
    {
        if (native.failed)
        {
            std::fprintf(stderr, "case %zu: native failed at pc=0x%03x (entry 0x%03x)\n",
                         caseIndex, native.failPc, entryPc);
            return false;
        }
        std::string field;
        if (!compareState(native.state, reference.state, field))
        {
            std::fprintf(stderr,
                         "case %zu: first mismatch field=%s entry=0x%03x finalPC=0x%03x/0x%03x cycle=%llu/%llu\n",
                         caseIndex, field.c_str(), entryPc, native.state.pc, reference.state.pc,
                         static_cast<unsigned long long>(native.cycle),
                         static_cast<unsigned long long>(reference.cycle));
            return false;
        }
        if (native.cycle != reference.cycle)
        {
            std::fprintf(stderr, "case %zu: first mismatch field=cycle entry=0x%03x\n", caseIndex, entryPc);
            return false;
        }
        if (native.data != reference.data)
        {
            const auto it = std::mismatch(native.data.begin(), native.data.end(), reference.data.begin());
            std::fprintf(stderr, "case %zu: first mismatch field=data[0x%04zx] entry=0x%03x\n",
                         caseIndex, static_cast<size_t>(it.first - native.data.begin()), entryPc);
            return false;
        }
        return true;
    }

    uint32_t pickFloatBits(std::mt19937 &rng)
    {
        static constexpr uint32_t edge[] = {
            0x00000000u, 0x80000000u, 0x00000001u, 0x007FFFFFu,
            0x00800000u, 0x80800000u, 0x7F7FFFFFu, 0xFF7FFFFFu,
            0x7F800000u, 0xFF800000u, 0x7FC00000u, 0xFFC00000u,
            0x7FA00001u, 0x1E800000u, 0x3F800000u, 0xBF800000u,
        };
        if ((rng() & 3u) != 0u)
            return edge[rng() % (sizeof(edge) / sizeof(edge[0]))];
        return rng();
    }

    void fuzzInput(VU1State &state, std::vector<uint8_t> &data, std::mt19937 &rng, uint32_t caseIndex)
    {
        for (int r = 1; r < 32; ++r)
            for (int c = 0; c < 4; ++c)
            {
                const uint32_t bits = pickFloatBits(rng);
                std::memcpy(&state.vf[r][c], &bits, sizeof(bits));
            }
        for (int c = 0; c < 4; ++c)
        {
            const uint32_t bits = pickFloatBits(rng);
            std::memcpy(&state.acc[c], &bits, sizeof(bits));
        }
        if ((caseIndex & 1u) == 0u)
        {
            const uint32_t q = pickFloatBits(rng), p = pickFloatBits(rng), i = pickFloatBits(rng);
            std::memcpy(&state.q, &q, sizeof(q));
            std::memcpy(&state.p, &p, sizeof(p));
            std::memcpy(&state.i, &i, sizeof(i));
        }
        state.mac = rng();
        state.status = rng();
        state.clip = rng();

        static constexpr uint16_t viEdges[] = {
            0x0000u, 0x0001u, 0x00FFu, 0x0100u, 0x0101u, 0x01FFu,
            0x0FFFu, 0x1000u, 0x7FFFu, 0x8000u, 0xFF00u, 0xFFFFu,
        };
        for (int r = 1; r <= 4; ++r)
        {
            const uint16_t bits = (rng() & 1u) ? viEdges[rng() % (sizeof(viEdges) / sizeof(viEdges[0]))]
                                               : static_cast<uint16_t>(rng());
            int16_t signedBits;
            std::memcpy(&signedBits, &bits, sizeof(bits));
            state.vi[r] = signedBits;
        }
        for (size_t off = 0; off < data.size(); off += 4)
        {
            const uint32_t bits = pickFloatBits(rng);
            std::memcpy(data.data() + off, &bits, sizeof(bits));
        }
        // Put edge values at every qword the audited program can reach through
        // its four post-increment LQI streams, including wraparound addresses.
        static constexpr uint32_t loadedEdges[4] = {
            0x7FC00000u, 0x00000001u, 0x7F800000u, 0xFF7FFFFFu,
        };
        for (int r = 1; r <= 4; ++r)
        {
            const uint32_t base = static_cast<uint16_t>(state.vi[r]);
            for (uint32_t qword = 0; qword < 4; ++qword)
            {
                const uint32_t address = ((base + qword) * 16u) & (PS2_VU0_DATA_SIZE - 1u);
                for (uint32_t lane = 0; lane < 4; ++lane)
                {
                    const uint32_t bits = loadedEdges[(lane + qword + r + caseIndex) & 3u];
                    std::memcpy(data.data() + address + lane * 4u, &bits, sizeof(bits));
                }
            }
        }
        static constexpr uint32_t inputEdges[4] = {0x7FC00000u, 0x00000001u, 0x80000000u, 0x7F800000u};
        for (int lane = 0; lane < 4; ++lane)
        {
            const uint32_t bits = inputEdges[(lane + caseIndex) & 3u];
            std::memcpy(&state.vf[1][lane], &bits, sizeof(bits));
        }
    }
}

int main(int argc, char **argv)
{
    if (argc < 4)
    {
        std::fprintf(stderr, "usage: %s <code.bin> <data.before.bin> <state.before.txt> [--fuzz N] [--bench N]\n", argv[0]);
        return 2;
    }
    int fuzzCases = 1000;
    int benchRuns = 10000;
    for (int i = 4; i < argc; ++i)
    {
        if (!std::strcmp(argv[i], "--fuzz") && i + 1 < argc)
            fuzzCases = std::atoi(argv[++i]);
        else if (!std::strcmp(argv[i], "--bench") && i + 1 < argc)
            benchRuns = std::atoi(argv[++i]);
        else
        {
            std::fprintf(stderr, "unknown option: %s\n", argv[i]);
            return 2;
        }
    }
    if (fuzzCases < 1000 || benchRuns < 1)
    {
        std::fprintf(stderr, "--fuzz must be at least 1000 and --bench must be positive\n");
        return 2;
    }

    std::vector<uint8_t> code(PS2_VU0_CODE_SIZE), inputData(PS2_VU0_DATA_SIZE);
    auto readBinary = [](const char *path, std::vector<uint8_t> &out) {
        std::ifstream f(path, std::ios::binary);
        return f && static_cast<bool>(f.read(reinterpret_cast<char *>(out.data()), out.size())) &&
               f.peek() == std::ifstream::traits_type::eof();
    };
    VU1State captured{};
    if (!readBinary(argv[1], code) || !readBinary(argv[2], inputData) || !loadStateText(argv[3], captured))
    {
        std::fprintf(stderr, "could not read exact 4 KiB code/data and state inputs\n");
        return 2;
    }
    captured.itop = 0;
    captured.cycles = 0;
    const uint32_t entryPc = captured.pc;
    const uint64_t hash = codeHash(code.data());
    const Vu0RecImage *image = vu0recFind(hash);
    if (!image || !image->full || image->entryCount == 0 || image->entries[0] != entryPc)
    {
        std::fprintf(stderr, "no generated VU0 full variant for hash=%016llx entry=0x%03x\n",
                     static_cast<unsigned long long>(hash), entryPc);
        return 2;
    }

    alignas(GS) uint8_t gsStorage[sizeof(GS)]{};
    GS &gs = *reinterpret_cast<GS *>(gsStorage); // The supported subset never calls GS operations.
    const Result reference = runInterpreter(code, captured, inputData, entryPc, gs);
    const Result native = runNative(image->full, captured, inputData, entryPc);
    if (!compareResults(native, reference, 0, entryPc))
        return 1;

    std::mt19937 rng(0x7A2A0u);
    for (int n = 1; n <= fuzzCases; ++n)
    {
        VU1State state = captured;
        std::vector<uint8_t> data = inputData;
        fuzzInput(state, data, rng, static_cast<uint32_t>(n));
        const Result expected = runInterpreter(code, state, data, entryPc, gs);
        const Result actual = runNative(image->full, state, data, entryPc);
        if (!compareResults(actual, expected, static_cast<size_t>(n), entryPc))
            return 1;
    }
    std::printf("vu0_replay: captured input + %d deterministic IEEE/VI edge cases matched (hash=%016llx, entry=0x%03x)\n",
                fuzzCases, static_cast<unsigned long long>(hash), entryPc);

    volatile uint64_t checksum = 0;
    auto timeRuns = [&](bool useNative) {
        const auto begin = std::chrono::steady_clock::now();
        for (int i = 0; i < benchRuns; ++i)
        {
            const Result r = useNative ? runNative(image->full, captured, inputData, entryPc)
                                       : runInterpreter(code, captured, inputData, entryPc, gs);
            checksum += r.cycle + r.state.pc + floatBits(r.state.vf[2][0]);
        }
        const auto end = std::chrono::steady_clock::now();
        return std::chrono::duration<double, std::nano>(end - begin).count() / benchRuns;
    };
    const double interpreterNs = timeRuns(false);
    const double nativeNs = timeRuns(true);
    std::printf("vu0_replay benchmark (uncached interpreter oracle; offline per-call harness): runs=%d interpreter=%.1f ns native-full=%.1f ns ratio=%.3fx checksum=%llu\n",
                benchRuns, interpreterNs, nativeNs, interpreterNs / nativeNs,
                static_cast<unsigned long long>(checksum));
    return 0;
}

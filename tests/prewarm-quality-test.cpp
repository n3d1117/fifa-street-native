#include "RRVMetalPrewarmQuality.h"

#include <array>
#include <cstdint>
#include <cstdio>
#include <stdexcept>

struct Selector
{
	struct Pixel
	{
		uint64_t key_lo;
		union
		{
			uint64_t key_hi;
			struct
			{
				uint64_t low : 25;
				uint64_t sw_aniso : 5;
				uint64_t high : 34;
			};
		};
	} ps;
	struct { uint32_t fullkey; } extras;
	struct { uint8_t key; } vs;
	std::array<uint8_t, 3> pad{0xA5, 0x5A, 0xC3};

	Selector(uint8_t vertex, uint64_t hi, uint64_t lo, uint32_t extra)
		: ps{lo, {hi}}, extras{extra}, vs{vertex} {}

	bool operator==(const Selector& rhs) const
	{
		return ps.key_hi == rhs.ps.key_hi && ps.key_lo == rhs.ps.key_lo &&
			extras.fullkey == rhs.extras.fullkey && vs.key == rhs.vs.key && pad == rhs.pad;
	}
};
static_assert(sizeof(Selector::Pixel) == 16);

static void check(bool value, const char* message)
{
	if (!value)
		throw std::runtime_error(message);
}

static bool contains(const std::vector<Selector>& keys, const Selector& key)
{
	return std::find(keys.begin(), keys.end(), key) != keys.end();
}

static void quality_options_and_legacy()
{
	const Selector first(0x02, 0x0000200010013579, 0xabcdef0123456789, 0x55aa55aa);
	const Selector duplicate(0x02, 0x0000200020013579, 0xabcdef0123456789, 0x55aa55aa);
	const Selector second(0x06, 0x0080000008024680, 0x0123456789abcdef, 0xaa55aa55);
	const std::vector<Selector> recorded{first, duplicate, second, first};
	const auto untouched = recorded;
	check(first.ps.sw_aniso == 8 && duplicate.ps.sw_aniso == 16 && second.ps.sw_aniso == 4,
		"fixture anisotropy field does not match serialized selectors");

	for (unsigned aniso : {0u, 2u, 4u, 8u, 16u})
	{
		const auto projected = RRVMetal::CurrentQualityPrewarmKeys(recorded, aniso);
		check(projected.size() == (aniso ? 4u : 2u), "full-key deduplication failed");
		check(contains(projected, Selector(0x02, 0x0000200000013579, 0xabcdef0123456789, 0x55aa55aa)),
			"off triangle counterpart missing");
		check(contains(projected, Selector(0x06, 0x0080000000024680, 0x0123456789abcdef, 0xaa55aa55)),
			"off second counterpart missing");
		if (aniso)
		{
			const uint64_t bits = uint64_t(aniso) << 25;
			check(contains(projected, Selector(0x02, 0x0000200000013579 | bits, 0xabcdef0123456789, 0x55aa55aa)),
				"selected triangle counterpart missing");
			check(contains(projected, Selector(0x06, 0x0080000000024680 | bits, 0x0123456789abcdef, 0xaa55aa55)),
				"selected second counterpart missing");
		}
		check(recorded == untouched, "recorded selectors were mutated");
		check(RRVMetal::CurrentQualityPrewarmKeys(recorded, aniso, false) == recorded,
			"kill switch changed legacy keys or order");
	}
	check(RRVMetal::CurrentQualityPrewarmKeys(recorded, 3) == recorded,
		"unsupported quality must keep the legacy warm set");
	check(RRVMetal::CurrentQualityPrewarmKeys(std::vector<Selector>{}, 8).empty(),
		"empty recorded set produced selectors");
}

static void fba_and_write_mask_remain_distinct()
{
	const Selector older(0x02, 0x00080000, 0x1122334455660088, 0x123456e1);
	const Selector first(0x02, 0x00080000, 0x1122334455660288, 0x123456f1);
	const Selector second(0x02, 0x00100000, 0x8877665544332200, 0x765432f1);
	const std::vector<Selector> recorded{older, first, second};
	const auto off = RRVMetal::CurrentQualityPrewarmKeys(recorded, 0);
	check(off == recorded, "FBA or alpha-write-mask bits changed or collapsed");
	const auto on = RRVMetal::CurrentQualityPrewarmKeys(recorded, 16);
	check(on.size() == 6, "FBA and alpha-write-mask combinations were not kept distinct");
	for (const auto& source : recorded)
	{
		check(contains(on, source), "off selector missing");
		check(contains(on, Selector(source.vs.key, source.ps.key_hi | 0x20000000,
			source.ps.key_lo, source.extras.fullkey)), "quality projection changed unrelated selector bits");
	}
}

int main()
{
	quality_options_and_legacy();
	fba_and_write_mask_remain_distinct();
	std::puts("prewarm quality tests passed");
}

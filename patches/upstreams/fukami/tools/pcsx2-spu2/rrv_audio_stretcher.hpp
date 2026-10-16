// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <SoundTouch.h>

#include <array>
#include <cstddef>
#include <cstdint>

class rrv_audio_stretcher {
public:
    using Sink = bool (*)(void* context, float left, float right);

    rrv_audio_stretcher();
    void process(const float* interleaved_stereo, size_t frames,
                 uint64_t queued_output_frames, uint64_t underrun_frames,
                 Sink sink, void* context);
    float tempo() const { return m_tempo; }

private:
    void update_tempo(uint64_t queued_output_frames, uint64_t underrun_frames);

    soundtouch::SoundTouch m_sound_touch;
    std::array<float, 4096 * 2> m_output{};
    std::array<float, 50> m_average_fullness{};
    size_t m_average_position{};
    size_t m_average_available{};
    uint64_t m_last_underrun_frames{};
    uint32_t m_reset_count{};
    uint32_t m_ok_count{};
    float m_dynamic_target_usage{4096.0f};
    float m_tempo{1.0f};
    bool m_inactive{};
    bool m_started{};
};

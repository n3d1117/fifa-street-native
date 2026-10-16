// SPDX-License-Identifier: GPL-3.0-or-later
#include "rrv_audio_stretcher.hpp"

#include <algorithm>
#include <cmath>

namespace {
constexpr float kBaseTargetFrames = 4096.0f;
constexpr uint64_t kStartupFrames = 4096;
constexpr float kMinControlTempo = 0.05f;
constexpr float kMaxControlTempo = 50.0f;
constexpr float kMinAppliedTempo = 0.90f;
constexpr float kMaxAppliedTempo = 1.10f;
constexpr float kInactiveGoodFactor = 1.04f;
constexpr float kInactiveBadFactor = 1.2f;
constexpr uint32_t kInactiveMinOkCount = 50;
constexpr uint32_t kCompensationDivider = 100;
constexpr uint32_t kResetThreshold = 5;
}

rrv_audio_stretcher::rrv_audio_stretcher() {
    m_sound_touch.setSampleRate(48000);
    m_sound_touch.setChannels(2);
    m_sound_touch.setSetting(SETTING_USE_QUICKSEEK, 1);
    m_sound_touch.setSetting(SETTING_USE_AA_FILTER, 1);
    m_sound_touch.setTempo(1.0f);
}

void rrv_audio_stretcher::update_tempo(uint64_t queued_output_frames,
                                       uint64_t underrun_frames) {
    // Let the first output batch accumulate before applying fullness feedback.
    // SoundTouch tempo below 1 expands samples, which deepens a startup underrun.
    if (!m_started && queued_output_frames < kStartupFrames) {
        m_tempo = 1.0f;
        m_sound_touch.setTempo(m_tempo);
        m_last_underrun_frames = underrun_frames;
        return;
    }
    m_started = true;
    if (underrun_frames > m_last_underrun_frames)
        m_reset_count++;
    m_last_underrun_frames = underrun_frames;

    const float base_target_usage = kBaseTargetFrames;
    if (m_reset_count >= kResetThreshold) {
        m_inactive = false;
        m_ok_count = 0;
        m_average_available = 0;
        m_dynamic_target_usage = base_target_usage;
    }

    float ratio = static_cast<float>(queued_output_frames) / m_dynamic_target_usage;
    if (m_average_available < m_average_fullness.size())
        ++m_average_available;
    m_average_fullness[m_average_position] = ratio;
    m_average_position = (m_average_position + 1u) % m_average_fullness.size();

    float sum = 0.0f;
    for (size_t i = 0; i < m_average_available; ++i)
        sum += m_average_fullness[i];
    ratio = sum / static_cast<float>(m_average_available);

    if (ratio < 2.0f)
        ratio = std::sqrt(std::max(ratio, 0.0f));
    ratio = std::clamp(ratio, kMinControlTempo, kMaxControlTempo);

    float adjusted_target = base_target_usage;
    if (ratio < 1.0f)
        adjusted_target /= std::sqrt(ratio);
    m_dynamic_target_usage +=
        (adjusted_target / ratio - m_dynamic_target_usage) /
        static_cast<float>(kCompensationDivider);
    if (std::abs(ratio - 1.0f) <= 0.1f &&
        std::abs(m_dynamic_target_usage - adjusted_target) <= adjusted_target * 0.1f)
        m_dynamic_target_usage = adjusted_target;

    if (!m_inactive) {
        if (ratio >= 1.0f / kInactiveGoodFactor && ratio <= kInactiveGoodFactor)
            ++m_ok_count;
        else
            m_ok_count = 0;
        if (m_ok_count >= kInactiveMinOkCount)
            m_inactive = true;
    } else if (ratio < 1.0f / kInactiveBadFactor || ratio > kInactiveBadFactor) {
        m_inactive = false;
        m_ok_count = 0;
    }

    // Keep hysteresis decisions on the unclamped fullness ratio so a large
    // backlog can reactivate stretching after the inactive 1:1 period.
    m_tempo = m_inactive ? 1.0f
                         : std::clamp(ratio, kMinAppliedTempo, kMaxAppliedTempo);
    m_sound_touch.setTempo(m_tempo);
    if (m_reset_count >= kResetThreshold)
        m_reset_count = 0;
}

void rrv_audio_stretcher::process(const float* interleaved_stereo, size_t frames,
                                  uint64_t queued_output_frames,
                                  uint64_t underrun_frames, Sink sink,
                                  void* context) {
    if (!interleaved_stereo || frames == 0)
        return;

    update_tempo(queued_output_frames, underrun_frames);
    m_sound_touch.putSamples(interleaved_stereo, static_cast<unsigned int>(frames));

    bool output_dropped = false;
    while (m_sound_touch.numSamples() != 0) {
        const unsigned int count = m_sound_touch.receiveSamples(
            m_output.data(), static_cast<unsigned int>(m_output.size() / 2u));
        if (count == 0)
            break;
        if (sink) {
            for (unsigned int i = 0; i < count; ++i) {
                if (!sink(context, m_output[2u * i], m_output[2u * i + 1u]))
                    output_dropped = true;
            }
        }
    }
    if (output_dropped)
        ++m_reset_count;
}

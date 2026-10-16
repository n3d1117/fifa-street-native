#!/usr/bin/env python3
"""Inspect IEEE-float stereo WAV captures from TARZAN_TRACE_PCM."""

import argparse
import math
import struct
import wave
from pathlib import Path


def read_float_wav(path):
    raw = Path(path).read_bytes()
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    offset = 12
    fmt = None
    samples = None
    while offset + 8 <= len(raw):
        tag = raw[offset : offset + 4]
        size = struct.unpack_from("<I", raw, offset + 4)[0]
        start = offset + 8
        end = start + size
        if end > len(raw):
            raise ValueError(f"truncated {tag!r} chunk")
        if tag == b"fmt ":
            if size < 16:
                raise ValueError("short fmt chunk")
            fmt = struct.unpack_from("<HHIIHH", raw, start)
        elif tag == b"data":
            samples = raw[start:end]
        offset = end + (size & 1)
    if fmt is None or samples is None:
        raise ValueError("missing fmt or data chunk")
    format_tag, channels, rate, byte_rate, align, bits = fmt
    if format_tag != 3 or bits != 32:
        raise ValueError(f"expected IEEE float32 WAV, got format={format_tag}, bits={bits}")
    if channels != 2 or align != 8 or byte_rate != rate * align:
        raise ValueError("expected valid stereo float WAV format")
    if len(samples) % align:
        raise ValueError("data chunk is not frame-aligned")
    values = [item[0] for item in struct.iter_unpack("<f", samples)]
    return rate, values[0::2], values[1::2]


def fft_peaks(left, right, rate, start, count, peak_count):
    size = 1 << min(15, count.bit_length() - 1)
    if size < 16:
        return []
    mono = [(left[start + index] + right[start + index]) * 0.5 for index in range(size)]
    mean = sum(mono) / size
    values = [complex((sample - mean) * (0.5 - 0.5 * math.cos(2 * math.pi * index / (size - 1))), 0)
              for index, sample in enumerate(mono)]

    # In-place radix-2 FFT; enough for the short, fixed 8-second captures.
    j = 0
    for i in range(1, size):
        bit = size >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            values[i], values[j] = values[j], values[i]
    length = 2
    while length <= size:
        angle = -2 * math.pi / length
        step = complex(math.cos(angle), math.sin(angle))
        for base in range(0, size, length):
            factor = 1 + 0j
            half = length >> 1
            for offset in range(half):
                even = values[base + offset]
                odd = values[base + offset + half] * factor
                values[base + offset] = even + odd
                values[base + offset + half] = even - odd
                factor *= step
        length <<= 1

    magnitudes = [abs(value) for value in values[: size // 2 + 1]]
    magnitudes[0] = 0.0
    candidates = [index for index in range(1, len(magnitudes) - 1)
                  if magnitudes[index] >= magnitudes[index - 1] and
                  magnitudes[index] >= magnitudes[index + 1]]
    candidates.sort(key=magnitudes.__getitem__, reverse=True)
    peaks = []
    for index in candidates:
        before = magnitudes[index - 1]
        center = magnitudes[index]
        after = magnitudes[index + 1]
        denominator = before - 2 * center + after
        correction = 0.5 * (before - after) / denominator if denominator else 0.0
        peaks.append(((index + correction) * rate / size, center))
        if len(peaks) == peak_count:
            break
    return peaks


def write_listen_copy(path, rate, left, right, scale):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        batch = bytearray()
        for l_sample, r_sample in zip(left, right):
            for sample in (l_sample * scale, r_sample * scale):
                sample = max(-1.0, min(1.0, sample))
                batch.extend(struct.pack("<h", round(sample * 32767.0)))
        wav.writeframes(batch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", help="captured float WAV")
    parser.add_argument("--window", type=float, default=1.0,
                        help="summary and FFT window in seconds (default: 1)")
    parser.add_argument("--fft-peaks", type=int, default=8,
                        help="largest non-DC FFT bins to show per window")
    parser.add_argument("--listen-copy", help="write a 16-bit PCM copy for playback")
    parser.add_argument("--listen-scale", type=float, default=0.5,
                        help="linear scale for playback copy (default: 0.5, -6 dB)")
    args = parser.parse_args()
    if args.window <= 0 or args.fft_peaks < 1 or not 0 < args.listen_scale <= 1:
        parser.error("window and FFT peaks must be positive; listen scale must be in (0, 1]")

    rate, left, right = read_float_wav(args.wav)
    frames = len(left)
    print(f"{args.wav}: {frames} frames, {frames / rate:.3f} s, {rate} Hz stereo float32")
    window_frames = max(1, round(args.window * rate))
    for start in range(0, frames, window_frames):
        end = min(frames, start + window_frames)
        l_window = left[start:end]
        r_window = right[start:end]
        finite = [value for value in (*l_window, *r_window) if math.isfinite(value)]
        rms = math.sqrt(sum(value * value for value in finite) / len(finite)) if finite else 0.0
        peak = max((abs(value) for value in finite), default=0.0)
        clipped = sum(abs(value) > 1.0 for value in finite)
        nonfinite = len(l_window) * 2 - len(finite)
        peaks = fft_peaks(left, right, rate, start, end - start, args.fft_peaks)
        result = f"{start / rate:6.2f}-{end / rate:6.2f}s rms={rms:.5f} peak={peak:.5f} over1={clipped} nonfinite={nonfinite}"
        if peaks:
            reference = max(peaks[0][1], 1e-30)
            result += " FFT=" + ", ".join(
                f"{frequency:.1f}Hz/{20 * math.log10(max(magnitude, 1e-30) / reference):.1f}dB"
                for frequency, magnitude in peaks)
        print(result)

    if args.listen_copy:
        write_listen_copy(args.listen_copy, rate, left, right, args.listen_scale)
        print(f"wrote playback copy at scale {args.listen_scale:g}: {args.listen_copy}")


if __name__ == "__main__":
    main()

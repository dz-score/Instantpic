"""
Generate a warm, gentle chime/bell sound for the photo booth shutter.
Uses only Python standard library: wave, struct, math.
"""
import os
import wave
import struct
import math

# Audio parameters
SAMPLE_RATE = 44100
DURATION = 0.8  # seconds
NUM_SAMPLES = int(SAMPLE_RATE * DURATION)
AMPLITUDE = 0.45  # master volume (0.0 - 1.0)

# Output path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
OUTPUT_DIR = os.path.join(BASE_DIR, "frontend", "public", "sounds")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "shutter.wav")

# Harmonics for a warm bell/music-box tone
# (frequency_hz, relative_amplitude, decay_rate)
HARMONICS = [
    (880.0,  1.0,   4.0),   # fundamental A5
    (1760.0, 0.5,   5.5),   # 2nd harmonic
    (2640.0, 0.25,  7.0),   # 3rd harmonic
    (3520.0, 0.12,  9.0),   # 4th harmonic
    (1320.0, 0.35,  5.0),   # perfect fifth for warmth
    (1108.7, 0.20,  6.0),   # major third for sweetness
]

# Soft attack duration (seconds) to avoid click
ATTACK_TIME = 0.012


def generate_sample(t: float) -> float:
    """Generate a single sample value at time t (seconds)."""
    value = 0.0
    for freq, amp, decay in HARMONICS:
        # Exponential decay envelope
        envelope = math.exp(-decay * t)
        value += amp * envelope * math.sin(2.0 * math.pi * freq * t)

    # Soft attack envelope (linear ramp)
    if t < ATTACK_TIME:
        value *= t / ATTACK_TIME

    return value


def main():
    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Generate all samples
    samples = []
    max_val = 0.0
    for i in range(NUM_SAMPLES):
        t = i / SAMPLE_RATE
        s = generate_sample(t)
        samples.append(s)
        if abs(s) > max_val:
            max_val = abs(s)

    # Normalize and convert to 16-bit PCM
    if max_val == 0:
        max_val = 1.0
    scale = AMPLITUDE * 32767.0 / max_val

    packed = b""
    for s in samples:
        clamped = max(-32768, min(32767, int(s * scale)))
        packed += struct.pack("<h", clamped)

    # Write WAV file
    with wave.open(OUTPUT_PATH, "w") as wf:
        wf.setnchannels(1)        # mono
        wf.setsampwidth(2)        # 16-bit
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(packed)

    print(f"Generated shutter sound: {OUTPUT_PATH}")
    print(f"  Duration: {DURATION}s | Sample rate: {SAMPLE_RATE}Hz | 16-bit mono")
    print(f"  File size: {os.path.getsize(OUTPUT_PATH)} bytes")


if __name__ == "__main__":
    main()

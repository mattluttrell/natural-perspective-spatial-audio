"""The Optimized tier: measure the RMS level that actually reached each of
the 8 channels of a finished mix, pull each channel toward the mean of the
active channels (smoothed distribution), clamp the correction, leave
near-silent channels alone, exclude the band-limited LFE, then land the
overall level on the configured target."""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

TARGET_TOTAL_DB = -20.0   # configured "normal" overall RMS for every track
SMOOTH = 0.5              # 0 = leave channels alone, 1 = pull fully to mean
MAX_ADJ_DB = 9.0          # clamp on any per-channel smoothing correction
SILENCE_FLOOR_DB = -55.0  # channels quieter than this are considered absent
LFE_CH = 3                # excluded from smoothing; global gain only


def measure_channel_rms(mix_file: Path, ffmpeg_bin: str = "ffmpeg") -> list[float]:
    """Per-channel RMS (dB) of an 8-channel file, via ffmpeg astats."""
    proc = subprocess.run(
        [ffmpeg_bin, "-nostdin", "-i", str(mix_file), "-af", "astats", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    levels: dict[int, float] = {}
    ch = None
    for line in proc.stderr.splitlines():
        m = re.search(r"Channel: (\d+)", line)
        if m:
            ch = int(m.group(1)) - 1
            continue
        m = re.search(r"RMS level dB: (-?[\d.]+|-inf)", line)
        if m and ch is not None and ch not in levels:
            levels[ch] = float("-inf") if m.group(1) == "-inf" else float(m.group(1))
    if len(levels) != 8:
        raise RuntimeError(f"expected 8 channels from astats, parsed {len(levels)}")
    return [levels[c] for c in range(8)]


def compute_gains(levels: list[float], offsets: list[float] | None = None,
                  target: float = TARGET_TOTAL_DB) -> list[float]:
    """Per-channel dB gains: smoothing toward the mean, optional per-channel
    system trims, then a global push to the target loudness.

    `offsets` (8 values, dB, FL..SR order) tune the result to a specific
    playback rig — see system_profile.py. They are added on top of the content
    smoothing and before the global loudness step, so the rig balance is
    honored while every track still lands on `target`. Channels below the
    silence floor are left alone."""
    offsets = offsets if offsets is not None else [0.0] * 8
    active = [c for c in range(8) if c != LFE_CH and levels[c] > SILENCE_FLOOR_DB]
    if not active:
        return [0.0] * 8  # nothing above the silence floor — nothing to level
    mean = sum(levels[c] for c in active) / len(active)

    adj = [0.0] * 8
    for c in active:
        adj[c] = max(-MAX_ADJ_DB, min(MAX_ADJ_DB, SMOOTH * (mean - levels[c])))
    for c in range(8):
        if levels[c] > SILENCE_FLOOR_DB:
            adj[c] += offsets[c]

    powers = [10 ** ((levels[c] + adj[c]) / 10) for c in range(8) if levels[c] > SILENCE_FLOOR_DB]
    overall = 10 * math.log10(sum(powers) / 8)
    glob = target - overall
    return [a + glob for a in adj]


def measure_mean_volume(path: Path, ffmpeg_bin: str = "ffmpeg") -> float:
    """Overall mean volume (dB) of a file, via ffmpeg volumedetect. Used to
    give the Natural Perspective model each stem's level before it decides."""
    proc = subprocess.run(
        [ffmpeg_bin, "-nostdin", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", proc.stderr)
    return float(m.group(1)) if m else float("-inf")


def measure_stem_levels(stems: dict[str, Path], crowd: Path | None = None,
                        ffmpeg_bin: str = "ffmpeg") -> dict[str, float]:
    """Per-stem mean volume (dB), including crowd when present."""
    levels = {name: measure_mean_volume(p, ffmpeg_bin) for name, p in stems.items()}
    if crowd is not None:
        levels["crowd"] = measure_mean_volume(crowd, ffmpeg_bin)
    return levels


def apply_gains(mix_file: Path, gains: list[float], out_file: Path,
                ffmpeg_bin: str = "ffmpeg") -> Path:
    split = "[0]channelsplit=channel_layout=7.1" + "".join(f"[c{i}]" for i in range(8)) + ";"
    buses = "".join(
        f"[c{i}]volume={gains[i]:.2f}dB,alimiter=limit=0.95,aformat=channel_layouts=mono[o{i}];"
        for i in range(8)
    )
    merge = "".join(f"[o{i}]" for i in range(8)) + "amerge=inputs=8"
    proc = subprocess.run(
        [ffmpeg_bin, "-nostdin", "-y", "-loglevel", "error", "-i", str(mix_file),
         "-filter_complex", split + buses + merge,
         "-c:a", "flac", "-sample_fmt", "s32", str(out_file)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg optimize failed:\n{proc.stderr.strip()[-2000:]}")
    return out_file

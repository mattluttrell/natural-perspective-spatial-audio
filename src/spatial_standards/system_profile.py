"""Parse an optional ``output_system.md`` describing the listener's playback
rig into per-channel level trims (dB) for the Optimized pass.

The file is plain markdown — write it like notes to yourself. Only lines of
the form ``LABEL: VALUE`` are read, where LABEL names a 7.1 channel (or a
group of channels) and VALUE is a decibel offset; the special label ``target``
sets the overall album loudness. Everything else (headings, prose, comments)
is ignored, and ``#`` starts an inline comment.

Channels are in 7.1 file order — FL FR FC LFE BL BR SL SR — matching the
order written to the FLAC (see mix.py) and measured by optimize.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 7.1 channel order as written to the FLAC.
CHANNELS = ("FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR")

# Accepted spellings -> channel index.
_ALIASES = {
    "FL": 0, "FRONT LEFT": 0,
    "FR": 1, "FRONT RIGHT": 1,
    "FC": 2, "C": 2, "CENTER": 2, "CENTRE": 2, "FRONT CENTER": 2, "FRONT CENTRE": 2,
    "LFE": 3, "SUB": 3, "SUBS": 3, "SUBWOOFER": 3,
    "BL": 4, "BACK LEFT": 4, "REAR LEFT": 4, "RBL": 4,
    "BR": 5, "BACK RIGHT": 5, "REAR RIGHT": 5, "RBR": 5,
    "SL": 6, "SIDE LEFT": 6, "SURROUND LEFT": 6,
    "SR": 7, "SIDE RIGHT": 7, "SURROUND RIGHT": 7,
}
# Group spellings -> the channel indices they set.
_GROUPS = {
    "FRONTS": (0, 1),
    "SURROUNDS": (6, 7), "SIDES": (6, 7),
    "BACKS": (4, 5), "REARS": (4, 5),
    "ALL": tuple(range(8)),
}

_LINE = re.compile(r"^\s*[-*]?\s*([A-Za-z][A-Za-z ]*?)\s*[:=]\s*([+-]?\d+(?:\.\d+)?)")


@dataclass
class SystemProfile:
    """Per-channel dB trims (8 values, FL..SR order) and an optional overall
    target loudness (dB). Both default to a no-op."""
    offsets: list[float] = field(default_factory=lambda: [0.0] * 8)
    target: float | None = None


def parse_system_profile(path: Path) -> SystemProfile:
    """Read a comments/rig file into a SystemProfile."""
    return parse_system_profile_text(Path(path).read_text(encoding="utf-8"))


def parse_system_profile_text(text: str) -> SystemProfile:
    """Parse comments/rig text into a SystemProfile. Unknown labels are
    ignored; group lines are applied first so a later specific channel line
    overrides them."""
    groups: dict[int, float] = {}
    specific: dict[int, float] = {}
    target: float | None = None

    for raw in text.splitlines():
        line = raw.split("#", 1)[0]  # strip inline comments
        m = _LINE.match(line)
        if not m:
            continue
        label = " ".join(m.group(1).upper().split())
        value = float(m.group(2))
        if label == "TARGET":
            target = value
        elif label in _GROUPS:
            for i in _GROUPS[label]:
                groups[i] = value
        elif label in _ALIASES:
            specific[_ALIASES[label]] = value
        # anything else: ignored on purpose

    offsets = [0.0] * 8
    for i, v in {**groups, **specific}.items():
        offsets[i] = v
    return SystemProfile(offsets=offsets, target=target)

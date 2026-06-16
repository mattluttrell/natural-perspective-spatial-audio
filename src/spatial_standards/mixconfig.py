"""The configuration-driven mixer for Natural Perspective.

A mix configuration (see CONFIG_SCHEMA.md) names, per 7.1 channel, which stems
feed it at what weight. `build_filtergraph` turns that into an ffmpeg
filter_complex; `mix_from_config` runs it. The builder enforces audio safety
regardless of what produced the config (model or default): every channel bus
gets a peak limiter, weights are clamped, LFE is low-passed, and unknown or
unavailable stems are dropped.

Channels are 7.1 file order: FL FR FC LFE BL BR SL SR.
"""
from __future__ import annotations

import subprocess
from collections import defaultdict, deque
from pathlib import Path

CHANNELS = ("FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR")
STEMS = ("vocals", "guitar", "piano", "bass", "drums", "other", "crowd")

MAX_WEIGHT = 4.0
LIMIT = 0.95
_BUS = f"alimiter=limit={LIMIT},aformat=channel_layouts=mono"


# The default config: Front Row + Optimized, used verbatim when no model
# customizes it. Mirrors the hand-tuned mix_front_row + the Optimized tier.
DEFAULT_CONFIG: dict = {
    "scene": "Front Stage",
    "perspective": "Front row — band ahead, crowd around you.",
    "separate_crowd": True,
    "optimized": True,
    "stem_prep": {"drums": {"gain": 2.0, "limit": 0.95}},
    "routing": {
        "FL": [{"stem": "guitar", "side": "L", "weight": 1.0},
               {"stem": "drums", "side": "L", "weight": 0.7},
               {"stem": "vocals", "weight": 0.3}],
        "FR": [{"stem": "guitar", "side": "R", "weight": 1.0},
               {"stem": "piano", "weight": 0.8},
               {"stem": "drums", "side": "R", "weight": 0.7},
               {"stem": "vocals", "weight": 0.3}],
        "FC": [{"stem": "vocals", "weight": 1.0}],
        "LFE": {"lowpass_hz": 120,
                "sources": [{"stem": "bass", "weight": 1.0},
                            {"stem": "drums", "weight": 0.6}]},
        "SL": [{"stem": "bass", "weight": 1.0}, {"stem": "other", "weight": 1.0},
               {"stem": "drums", "side": "L", "weight": 1.0},
               {"stem": "crowd", "side": "L", "weight": 0.7}],
        "SR": [{"stem": "piano", "weight": 1.0}, {"stem": "bass", "weight": 0.6},
               {"stem": "drums", "side": "R", "weight": 1.0},
               {"stem": "crowd", "side": "R", "weight": 0.7}],
        "BL": [{"stem": "other", "weight": 1.0},
               {"stem": "crowd", "side": "L", "weight": 1.0}],
        "BR": [{"stem": "bass", "weight": 0.4}, {"stem": "other", "weight": 1.0},
               {"stem": "crowd", "side": "R", "weight": 1.0}],
    },
}


def default_config() -> dict:
    """A fresh deep copy of the default (Front Row + Optimized) config."""
    import copy
    return copy.deepcopy(DEFAULT_CONFIG)


def _sources(spec) -> list[dict]:
    return spec["sources"] if isinstance(spec, dict) else spec


def _lowpass(spec) -> int | None:
    return spec.get("lowpass_hz") if isinstance(spec, dict) else None


def validate_config(config: dict) -> None:
    """Raise ValueError if the config can't be built into a safe mix."""
    routing = config.get("routing")
    if not isinstance(routing, dict):
        raise ValueError("config: 'routing' must be an object")
    missing = [c for c in CHANNELS if c not in routing]
    if missing:
        raise ValueError(f"config: routing missing channels {missing}")
    references_crowd = False
    for ch in CHANNELS:
        srcs = _sources(routing[ch])
        if not isinstance(srcs, list) or not srcs:
            raise ValueError(f"config: channel {ch} has no sources")
        for s in srcs:
            if s.get("stem") not in STEMS:
                raise ValueError(f"config: channel {ch} unknown stem {s.get('stem')!r}")
            if s.get("side") not in (None, "L", "R"):
                raise ValueError(f"config: channel {ch} bad side {s.get('side')!r}")
            float(s.get("weight", 1.0))  # raises on non-numeric
            references_crowd = references_crowd or s["stem"] == "crowd"
        lp = _lowpass(routing[ch])
        if lp is not None:
            int(lp)
    if references_crowd and not config.get("separate_crowd", False):
        raise ValueError("config: routing uses 'crowd' but separate_crowd is false")


def _clamp(w: float) -> float:
    return min(MAX_WEIGHT, max(0.0, float(w)))


# Stems that are full-range. Routing one of these *only* into LFE makes it
# inaudible on a bass-managed system (LFE is the sub feed, low-passed by the
# receiver), so the instrument effectively vanishes. Bass is the one stem
# allowed to live in LFE alone.
LFE_ONLY_ALLOWED = frozenset({"bass"})


def ensure_fullrange_homes(config: dict) -> dict:
    """Guardrail: any full-range stem routed *only* to LFE is also given a
    front (FL/FR) home so it is actually heard. Mutates and returns `config`.

    Catches the failure where the model dumps a whole drum kit into the sub —
    only the kick's low end survives and the rest of the kit goes missing.
    """
    routing = config.get("routing")
    if not isinstance(routing, dict) or "LFE" not in routing:
        return config
    in_main = {s.get("stem")
               for ch in CHANNELS if ch != "LFE" and ch in routing
               for s in _sources(routing[ch])}
    for s in _sources(routing["LFE"]):
        stem = s.get("stem")
        if stem in LFE_ONLY_ALLOWED or stem in in_main:
            continue
        w = _clamp(s.get("weight", 1.0)) or 0.7
        for ch, side in (("FL", "L"), ("FR", "R")):
            if ch in routing:
                _sources(routing[ch]).append(
                    {"stem": stem, "side": side, "weight": w})
        in_main.add(stem)
    return config


def build_filtergraph(config: dict, stem_index: dict[str, int]) -> str:
    """Build the ffmpeg filter_complex string. `stem_index` maps each
    available stem name to its ffmpeg input index. Sources whose stem is not
    in `stem_index` are dropped (e.g. crowd when no crowd pass ran)."""
    routing = config["routing"]
    prep = config.get("stem_prep", {})

    # Gather, per (stem, side, lowpass) tap, the channels that consume it.
    tap_consumers: dict[tuple, list[str]] = defaultdict(list)
    channel_sources: dict[str, list[tuple[tuple, float]]] = {}
    for ch in CHANNELS:
        lp = _lowpass(routing[ch])
        srcs = []
        for s in _sources(routing[ch]):
            stem = s["stem"]
            if stem not in stem_index:
                continue
            key = (stem, s.get("side"), lp)
            tap_consumers[key].append(ch)
            srcs.append((key, _clamp(s.get("weight", 1.0))))
        if not srcs:
            raise ValueError(f"channel {ch}: no usable sources (after dropping "
                             "unavailable stems)")
        channel_sources[ch] = srcs

    stmts: list[str] = []
    n = [0]

    def label(prefix: str) -> str:
        n[0] += 1
        return f"{prefix}{n[0]}"

    # Per stem: prepped (stereo) → one branch per distinct tap → pan/lowpass →
    # split across that tap's consumers.
    consumer_pool: dict[tuple, deque[str]] = {}
    for stem in sorted({k[0] for k in tap_consumers}):
        idx = stem_index[stem]
        prepped = f"P{idx}"
        if stem in prep:
            # The limiter is a safety device — always the fixed safe limit, never
            # a model value. A gain of <= 0 means "no change" (1.0), not "mute",
            # so stem prep can only boost/keep, never silence a stem.
            gain = float(prep[stem].get("gain", 1.0))
            gain = 1.0 if gain <= 0 else min(MAX_WEIGHT, gain)
            stmts.append(f"[{idx}]volume={gain},alimiter=limit={LIMIT}:level=disabled,"
                         f"aformat=channel_layouts=stereo[{prepped}]")
        else:
            stmts.append(f"[{idx}]aformat=channel_layouts=stereo[{prepped}]")

        taps = [k for k in tap_consumers if k[0] == stem]
        if len(taps) == 1:
            branches = [prepped]
        else:
            branches = [label(f"b{idx}_") for _ in taps]
            stmts.append(f"[{prepped}]asplit={len(taps)}"
                         + "".join(f"[{b}]" for b in branches))

        for key, branch in zip(taps, branches):
            _, side, lp = key
            pan = {"L": "pan=mono|c0=c0", "R": "pan=mono|c0=c1"}.get(
                side, "pan=mono|c0=0.5*c0+0.5*c1")
            chain = pan + (f",lowpass=f={int(lp)}" if lp else "")
            tap = label("t")
            stmts.append(f"[{branch}]{chain}[{tap}]")
            consumers = tap_consumers[key]
            if len(consumers) == 1:
                consumer_pool[key] = deque([tap])
            else:
                outs = [label("c") for _ in consumers]
                stmts.append(f"[{tap}]asplit={len(consumers)}"
                             + "".join(f"[{o}]" for o in outs))
                consumer_pool[key] = deque(outs)

    # Per channel: weight + sum its taps, then the safety bus.
    for ch in CHANNELS:
        srcs = channel_sources[ch]
        picks = [(consumer_pool[key].popleft(), w) for key, w in srcs]
        if len(picks) == 1:
            lab, w = picks[0]
            stmts.append(f"[{lab}]volume={w:.4f},{_BUS}[{ch}]")
        else:
            ins = "".join(f"[{lab}]" for lab, _ in picks)
            weights = " ".join(f"{w:.4f}" for _, w in picks)
            stmts.append(f"{ins}amix=inputs={len(picks)}:weights={weights}:"
                         f"normalize=0,{_BUS}[{ch}]")

    stmts.append("".join(f"[{ch}]" for ch in CHANNELS) + "amerge=inputs=8")
    return ";".join(stmts)


def referenced_stems(config: dict) -> set[str]:
    out = set()
    for ch in CHANNELS:
        for s in _sources(config["routing"][ch]):
            out.add(s["stem"])
    return out


def mix_from_config(stems: dict[str, Path], crowd: Path | None, config: dict,
                    out_file: Path, ffmpeg_bin: str = "ffmpeg") -> Path:
    """Render a mix config to an 8-channel 7.1 FLAC."""
    validate_config(config)
    available: dict[str, Path] = dict(stems)
    if crowd is not None:
        available["crowd"] = crowd

    referenced = referenced_stems(config)
    inputs = [(name, p) for name, p in available.items() if name in referenced]
    if not inputs:
        raise ValueError("config references no available stems")
    stem_index = {name: i for i, (name, _) in enumerate(inputs)}

    graph = build_filtergraph(config, stem_index)
    cmd = [ffmpeg_bin, "-nostdin", "-y", "-loglevel", "error"]
    for _, p in inputs:
        cmd += ["-i", str(p)]
    cmd += ["-filter_complex", graph, "-c:a", "flac", "-sample_fmt", "s32", str(out_file)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mix failed:\n{proc.stderr.strip()[-2000:]}")
    return out_file

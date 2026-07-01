"""Natural Perspective Spatial Audio Process — the control layer.

After separation, a multimodal model looks at what can be cheaply learned
about a recording (metadata, the title/source, the cover art, the free-text
comments file, and the measured level of each stem) and emits a full mix
*configuration* — the scene, the perspective, and which stems feed each of the
eight 7.1 channels. The deterministic mixer (mixconfig.py) then renders it,
enforcing audio safety regardless of what the model returns.

Bring-your-own-key: needs an Anthropic API key (``ANTHROPIC_API_KEY``) and the
``anthropic`` package (``pip install 'spatial-standards[natural]'``). With no
key, the pipeline falls back to the default config. No audio is sent — only
metadata, the cover-art image, the comments text, and the stem levels.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.request
from pathlib import Path

from . import mixconfig
from .system_profile import CHANNELS as TRIM_CHANNELS  # FL..SR, for trim summary
from .system_profile import SystemProfile

DEFAULT_MODEL = "claude-sonnet-4-6"

SYSTEM_GUIDE = f"""\
You are the control layer for a perspective-based 7.1 spatial-audio renderer.
Given a recording's metadata, cover art, free-text notes, and the measured
level of each separated stem, you design a mix: invent a short, evocative
scene name, describe the listener's perspective in one line, and route the
stems across the eight 7.1 channels.

Channels (7.1): {", ".join(mixconfig.CHANNELS)}.
Stems available: {", ".join(mixconfig.STEMS)} (use "crowd" only if you also set
separate_crowd=true; crowd belongs only in the surround/rear channels, never
up front).

Principles:
- Vocals usually anchor the center (FC). Bass is largely non-directional; keep
  it out of the rear channels if the notes mention small or weak rears, and
  route low end into LFE with a lowpass around 120 Hz.
- LFE is the subwoofer feed and is low-passed on playback, so only bass-type
  low end belongs there alone. Drums are full-range: a kit routed only to LFE
  loses its snare, hats, and cymbals. Always give drums (and every other
  full-range stem) a home in at least one main channel; LFE is supplemental.
- A live/crowd recording puts the audience around and behind the listener; a
  studio recording places the players around them with no crowd.
- Honor the free-text notes and the playback-system description: they describe
  the desired vibe AND the actual speakers. If a speaker is small, send less
  (or no) heavy low end there.
- Every one of the eight channels must have at least one source. Weights are
  linear (1.0 = unity). Set optimized=true for live material and whenever a
  system profile is present.
- If you cannot tell the artist, the song, the venue, or whether the recording
  is live from what you were given, use the web_search tool to look it up
  before deciding (e.g. search the title for "live" vs studio, the venue, the
  genre). Do not guess when a quick search would settle it.

Return the decision in the required schema. Use side "mono" unless you want a
specific left/right channel of a stereo stem. Use lowpass_hz 0 for no lowpass.
"""

# Structured-output schema (array-shaped for reliability); converted to the
# internal mixconfig dict by to_config().
MODEL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scene", "perspective", "separate_crowd", "optimized", "routing"],
    "properties": {
        "scene": {"type": "string"},
        "perspective": {"type": "string"},
        "separate_crowd": {"type": "boolean"},
        "optimized": {"type": "boolean"},
        "routing": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["channel", "lowpass_hz", "sources"],
                "properties": {
                    "channel": {"type": "string", "enum": list(mixconfig.CHANNELS)},
                    "lowpass_hz": {"type": "integer"},
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["stem", "side", "weight"],
                            "properties": {
                                "stem": {"type": "string", "enum": list(mixconfig.STEMS)},
                                "side": {"type": "string", "enum": ["L", "R", "mono"]},
                                "weight": {"type": "number"},
                            },
                        },
                    },
                },
            },
        },
    },
}


def to_config(model_out: dict) -> dict:
    """Convert the model's array-shaped output into the internal mixconfig
    dict (channel-keyed routing, lowpass object form for LFE, prep dict)."""
    routing: dict = {}
    for entry in model_out.get("routing", []):
        ch = entry["channel"]
        sources = []
        for s in entry.get("sources", []):
            src = {"stem": s["stem"], "weight": float(s.get("weight", 1.0))}
            if s.get("side") in ("L", "R"):
                src["side"] = s["side"]
            sources.append(src)
        lp = int(entry.get("lowpass_hz") or 0)
        routing[ch] = {"lowpass_hz": lp, "sources": sources} if lp > 0 else sources

    prep = {p["stem"]: {"gain": float(p.get("gain", 1.0)),
                        "limit": float(p.get("limit", 0.95))}
            for p in model_out.get("stem_prep", [])}

    config = {
        "scene": model_out.get("scene", "Natural Perspective"),
        "perspective": model_out.get("perspective", ""),
        "separate_crowd": bool(model_out.get("separate_crowd", False)),
        "optimized": bool(model_out.get("optimized", True)),
        "routing": routing,
    }
    if prep:
        config["stem_prep"] = prep
    # Guardrail: never let a full-range stem (e.g. the whole drum kit) live
    # only in LFE, where a bass-managed system would mute everything but the
    # kick's low end. Give it a front home so it's actually heard.
    mixconfig.ensure_fullrange_homes(config)
    return config


def tag_fields(config: dict) -> tuple[str, str, str]:
    """Plex-facing tags derived from a mix config: (comment, genre, lyrics).
    The comment is a concise scene/perspective line; the lyrics field carries
    the full scene + perspective + channel routing so it's readable in Plex."""
    scene = (config.get("scene") or "").strip()
    persp = (config.get("perspective") or "").strip()
    head = " — ".join(x for x in (scene, persp) if x)
    comment = (head + " · " if head else "") + "Natural Perspective Spatial Audio (7.1)"
    genre = "Spatial Audio"

    lines: list[str] = []
    if scene:
        lines.append(f"Scene: {scene}")
    if persp:
        lines.append(f"Perspective: {persp}")
    lines += ["", "7.1 channel routing:"]
    routing = config.get("routing", {})
    for ch in mixconfig.CHANNELS:
        spec = routing.get(ch)
        if not spec:
            continue
        srcs = spec["sources"] if isinstance(spec, dict) else spec
        lp = (f" (lowpass {spec['lowpass_hz']} Hz)"
              if isinstance(spec, dict) and spec.get("lowpass_hz") else "")
        parts = ", ".join(
            f"{s['stem']}{('/' + s['side']) if s.get('side') else ''} ×{s.get('weight', 1)}"
            for s in srcs)
        lines.append(f"  {ch}: {parts}{lp}")
    lines += ["", "Natural Perspective Spatial Audio Process"]
    return comment, genre, "\n".join(lines)


def _system_summary(profile: SystemProfile | None) -> str:
    if profile is None:
        return "No playback-system profile was provided."
    trims = [f"{TRIM_CHANNELS[i]} {profile.offsets[i]:+g} dB"
             for i in range(8) if profile.offsets[i] != 0.0]
    parts = ["Per-channel trims: " + ", ".join(trims) + "." if trims
             else "No per-channel trims set."]
    if profile.target is not None:
        parts.append(f"Target loudness {profile.target:g} dB.")
    return " ".join(parts)


def _stem_levels_text(stem_levels: dict[str, float] | None) -> str:
    if not stem_levels:
        return "Stem levels: not measured."
    bits = []
    for name, db in stem_levels.items():
        bits.append(f"{name} {'silent' if db == float('-inf') else f'{db:.1f} dB'}")
    return "Measured stem levels (mean): " + ", ".join(bits) + "."


def _image_block(cover_art):
    if not cover_art:
        return None
    p = Path(cover_art)
    if not p.exists():
        return None
    media = mimetypes.guess_type(str(p))[0] or "image/jpeg"
    data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    return {"type": "image",
            "source": {"type": "base64", "media_type": media, "data": data}}


def build_decision_content(*, artist=None, title=None, source_title=None, source=None,
                           cover_art=None, comments_text=None,
                           system_profile=None, stem_levels=None) -> list[dict]:
    facts = []
    if artist:
        facts.append(f"Artist: {artist}")
    if title:
        facts.append(f"Title: {title}")
    if source_title and source_title != title:
        facts.append(f"Source/recording title: {source_title}")
    if source:
        if source.startswith(("http://", "https://")):
            facts.append(f"Source URL: {source}")
        else:
            facts.append(f"Filename: {Path(source).name}")
    facts.append(_stem_levels_text(stem_levels))
    facts.append("Playback system: " + _system_summary(system_profile))

    text = "Design the spatial mix for this recording.\n\nWhat is known:\n- " + \
        "\n- ".join(facts)
    if comments_text and comments_text.strip():
        text += "\n\nUser notes (comments file):\n" + comments_text.strip()
    text += "\n\nIf cover art is shown, use it as a cue. Return the mix design " \
            "in the required schema; every channel must have a source."

    content: list[dict] = [{"type": "text", "text": text}]
    img = _image_block(cover_art)
    if img:
        content.insert(0, img)
    return content


def _make_client(api_key):
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover - optional extra
        raise RuntimeError(
            "Natural Perspective needs the 'anthropic' package. Install it with: "
            "pip install 'spatial-standards[natural]'") from e
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def _research_query(artist, title, source_title) -> str | None:
    """A concise question for a web-search provider to pin down the recording."""
    name = " — ".join(x for x in (artist, title) if x) or source_title
    if not name:
        return None
    return (f'"{name}": is this a live or a studio recording? What is the venue '
            f"or event, and the year? Reply with only the facts.")


def _perplexity_research(query: str, api_key: str, timeout: float = 45) -> str | None:
    """Best-effort web research via the Perplexity API (OpenAI-style chat
    completions over plain HTTPS — no extra dependency). Returns a short factual
    summary, or None on any failure so the caller can fall back."""
    body = {
        "model": "sonar",
        "messages": [
            {"role": "system", "content":
             "You research music recordings. Reply in 2-4 sentences with only the "
             "facts useful for spatial mixing: the artist, whether it is a live or "
             "studio recording, the venue/event, and the year. No preamble, no citations."},
            {"role": "user", "content": query},
        ],
        "max_tokens": 300,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        "https://api.perplexity.ai/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        txt = (d.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        return txt or None
    except Exception:
        return None


def decide(*, artist=None, title=None, source_title=None, source=None,
           cover_art=None, comments_text=None, system_profile=None,
           stem_levels=None, model=DEFAULT_MODEL, api_key=None,
           web_search: bool = True, trace: dict | None = None, client=None) -> dict:
    """Ask the model for a full mix config (validated). Raises on any failure
    (no key, bad output, …) so the caller can fall back to the default config.

    If `trace` is given, it is filled with the exchange for inspection:
    model, system prompt, user prompt, raw response, and token usage.
    `client` is injectable for testing."""
    content = build_decision_content(
        artist=artist, title=title, source_title=source_title, source=source,
        cover_art=cover_art, comments_text=comments_text,
        system_profile=system_profile, stem_levels=stem_levels)

    # Optional: if PERPLEXITY_API_KEY is set, do the web research with Perplexity
    # and feed the result to the model instead of Anthropic's built-in web_search
    # tool (which costs extra and can be flaky with structured output). Best-effort
    # — on any failure we fall back to Anthropic's tool.
    px_key = os.environ.get("PERPLEXITY_API_KEY")
    if web_search and px_key:
        q = _research_query(artist, title, source_title)
        research = _perplexity_research(q, px_key) if q else None
        if research:
            content.append({"type": "text",
                            "text": "Web research (via Perplexity) — use it to judge "
                                    f"live vs studio, venue, and era:\n{research}"})
            web_search = False  # Perplexity did the research; skip Anthropic's tool
            if trace is not None:
                trace["searches"] = [f"(Perplexity) {q}"]

    if trace is not None:
        trace["model"] = model
        trace["system"] = SYSTEM_GUIDE
        trace["user"] = next((b["text"] for b in content if b.get("type") == "text"), "")
        trace["cover_art_attached"] = any(b.get("type") == "image" for b in content)
    client = client or _make_client(api_key)

    # The model may use web search to identify an unfamiliar artist/song/venue.
    # That runs as a server-side loop: re-send on "pause_turn" until it returns
    # the final structured config.
    tools = ([{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}]
             if web_search else None)
    messages = [{"role": "user", "content": content}]
    searches: list[str] = []
    text = None
    resp = None
    for _ in range(6 if web_search else 1):
        kwargs = dict(
            model=model,
            max_tokens=2048,
            system=SYSTEM_GUIDE,
            messages=messages,
            output_config={"format": {"type": "json_schema", "schema": MODEL_SCHEMA}},
        )
        if tools:
            kwargs["tools"] = tools
        resp = client.messages.create(**kwargs)
        for b in resp.content:
            if getattr(b, "type", None) == "server_tool_use" and getattr(b, "name", None) == "web_search":
                q = getattr(b, "input", None)
                if isinstance(q, dict) and q.get("query"):
                    searches.append(q["query"])
        if web_search and resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        break

    if trace is not None:
        if searches:
            trace["searches"] = searches
        usage = getattr(resp, "usage", None)
        if usage is not None:
            trace["usage"] = {"input_tokens": getattr(usage, "input_tokens", None),
                              "output_tokens": getattr(usage, "output_tokens", None)}
        trace["response"] = text
    if text is None:
        raise RuntimeError("Natural Perspective: model returned no text block")
    config = to_config(json.loads(text))
    mixconfig.validate_config(config)
    return config

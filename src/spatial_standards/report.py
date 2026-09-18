"""Album documentation page for Natural Perspective.

Each rendered track drops a ``<title>.config.json`` record next to its FLAC.
``write_album_index`` scans those records and renders ``index.html`` for the
album directory — the scene, perspective, routing, stem levels, and the
Optimized channel gains for every track. Built deterministically from the
records (no model call); an optional narrative pass can enrich it.
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

from .mixconfig import CHANNELS

_CSS = """
body{font-family:Inter,system-ui,sans-serif;background:#0A1628;color:#F0F4F8;margin:0;padding:2rem;}
h1{color:#F0F4F8;font-size:1.6rem;margin:0 0 .25rem;}
h2{color:#F0F4F8;font-size:1.2rem;margin:2rem 0 .25rem;border-bottom:1px solid #1E3A5F;padding-bottom:.3rem;}
.muted{color:#8B9DC3;}
.scene{color:#0D9488;font-weight:600;}
.card{background:#112240;border:1px solid #1E3A5F;border-radius:8px;padding:1rem 1.25rem;margin:1rem 0;}
table{border-collapse:collapse;width:100%;margin:.5rem 0;font-size:.9rem;}
th,td{text-align:left;padding:.3rem .6rem;border-bottom:1px solid #1E3A5F;}
th{color:#8B9DC3;font-weight:500;}
code{color:#8B9DC3;}
details{margin:.6rem 0;}
summary{cursor:pointer;color:#0D9488;}
pre{white-space:pre-wrap;background:#0A1628;border:1px solid #1E3A5F;border-radius:6px;
    padding:.6rem;font-size:.8rem;overflow:auto;color:#C9D6E8;}
.badges{float:right;display:flex;gap:.4rem;}
.badge{font-size:.7rem;letter-spacing:.05em;border-radius:999px;padding:.15rem .7rem;}
.badge.live{background:#0D9488;color:#04201d;font-weight:600;}
.badge.opt{border:1px solid #2A4A7F;color:#8B9DC3;}
.dome{display:block;margin:1rem auto;max-width:380px;width:100%;height:auto;}
.levels{list-style:none;padding:0;margin:.5rem 0;font-size:.9rem;}
.levels li{display:flex;align-items:center;padding:.2rem 0;border-bottom:1px solid #1E3A5F;}
.levels .dot{width:.7rem;height:.7rem;border-radius:50%;margin-right:.6rem;flex:none;}
.levels .db{margin-left:auto;color:#8B9DC3;}
"""

# Stem colors — shared by the soundstage map and the level dots. Identical to
# the luttrell.ai /spatial palette so the repo and the site read as one project.
_STEM_COLORS = {
    "vocals": "#F4B860", "guitar": "#E07A5F", "piano": "#81B29A",
    "bass": "#6C8EBF", "drums": "#C9697A", "other": "#9AA8C7", "crowd": "#3FB8AF",
}

# Top-down stage layout, x -1(left)..+1(right), y -1(behind)..+1(front).
# Same speaker geometry as the site's soundstage map.
_SPEAKERS = {
    "FL": (-0.70, 0.85), "FR": (0.70, 0.85), "FC": (0.0, 1.0), "LFE": (0.0, 0.10),
    "SL": (-1.0, 0.0), "SR": (1.0, 0.0), "BL": (-0.65, -0.85), "BR": (0.65, -0.85),
}


def _e(x) -> str:
    return html.escape(str(x))


def _src_list(spec) -> list:
    return spec["sources"] if isinstance(spec, dict) else spec


def _sources_str(spec) -> str:
    srcs = _src_list(spec)
    lp = f" (lowpass {spec['lowpass_hz']} Hz)" if isinstance(spec, dict) and spec.get("lowpass_hz") else ""
    parts = []
    for s in srcs:
        side = f" {s['side']}" if s.get("side") in ("L", "R") else ""
        parts.append(f"{_e(s['stem'])}{side} ×{float(s.get('weight', 1.0)):g}")
    return ", ".join(parts) + lp


def _fmt_db(x) -> str:
    if x is None or x == float("-inf"):
        return "—"
    return f"{float(x):+.1f}"


def _spatial_svg(rec: dict) -> str:
    """Render the mix as a top-down soundstage: each stem is a glowing blob at
    the weighted centroid of the speakers it feeds, sized by how wide it is
    spread and how loud it is. Listener at center, facing front. Pure inline
    SVG — no scripts, opens in any browser. Matches the luttrell.ai /spatial map.
    """
    routing = (rec.get("config") or {}).get("routing") or {}
    # Per stem, collect (speaker_x, speaker_y, weight) over the channels it feeds.
    agg: dict[str, list[tuple]] = {}
    for ch, spec in routing.items():
        if ch not in _SPEAKERS:
            continue
        spx, spy = _SPEAKERS[ch]
        for s in _src_list(spec):
            stem = s.get("stem")
            w = float(s.get("weight", 1.0))
            if stem and w > 0:
                agg.setdefault(stem, []).append((spx, spy, w))

    stems: dict[str, dict] = {}
    for stem, pts in agg.items():
        tw = sum(w for _, _, w in pts)
        x = sum(p * w for p, _, w in pts) / tw
        y = sum(p * w for _, p, w in pts) / tw
        sx = math.sqrt(sum(w * (p - x) ** 2 for p, _, w in pts) / tw)
        sy = math.sqrt(sum(w * (p - y) ** 2 for _, p, w in pts) / tw)
        stems[stem] = {"x": x, "y": y, "sx": sx, "sy": sy, "w": tw}
    maxw = max((v["w"] for v in stems.values()), default=1.0)

    SIZE, C, SCALE = 360, 180, 120

    def px(x):
        return C + x * SCALE

    def py(y):
        return C - y * SCALE

    blobs, labels = [], []
    for stem, v in stems.items():
        cx, cy = px(v["x"]), py(v["y"])
        rx = min(112.0, 16 + v["sx"] * 110)
        ry = min(92.0, 14 + v["sy"] * 110)
        op = 0.16 + 0.34 * (v["w"] / maxw)
        col = _STEM_COLORS.get(stem, "#9AA8C7")
        blobs.append((cx, cy, rx, ry, op, col))
        labels.append({"cx": cx, "cy": cy, "ly": cy - 8, "col": col,
                       "text": _e(stem.capitalize())})

    # De-collide labels: stems pan symmetrically, so labels stack in the center
    # column. Push them apart (and off the top edge) so none overlap.
    labels.sort(key=lambda l: l["ly"])
    prev = -1e9
    for l in labels:
        if l["ly"] < 16:
            l["ly"] = 16
        if l["ly"] - prev < 15:
            l["ly"] = prev + 15
        prev = l["ly"]

    blob_svg = "".join(
        f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" '
        f'fill="{col}" opacity="{op:.3f}" filter="url(#glow)"/>'
        f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" '
        f'fill="none" stroke="{col}" stroke-opacity="0.55"/>'
        for cx, cy, rx, ry, op, col in blobs)
    label_svg = "".join(
        f'<circle cx="{l["cx"]:.1f}" cy="{l["cy"]:.1f}" r="3.2" fill="{l["col"]}"/>'
        + (f'<line x1="{l["cx"]:.1f}" y1="{l["cy"]:.1f}" x2="{l["cx"]:.1f}" '
           f'y2="{l["ly"] + 2:.1f}" stroke="{l["col"]}" stroke-opacity="0.35"/>'
           if abs(l["ly"] + 8 - l["cy"]) > 4 else "")
        + f'<text x="{l["cx"]:.1f}" y="{l["ly"]:.1f}" class="bl" fill="{l["col"]}">'
          f'{l["text"]}</text>'
        for l in labels)
    spk_svg = "".join(
        f'<rect x="{px(x) - 9:.1f}" y="{py(y) - 9:.1f}" width="18" height="18" rx="3" '
        f'class="spk"/><text x="{px(x):.1f}" y="{py(y) + 3.5:.1f}" class="sl">{ch}</text>'
        for ch, (x, y) in _SPEAKERS.items())

    return f'''<svg class="dome" viewBox="0 0 {SIZE} {SIZE}" xmlns="http://www.w3.org/2000/svg">
  <style>
    .dome text{{font-family:Inter,system-ui,sans-serif;}}
    .ring{{fill:none;stroke:#1E3A5F;stroke-dasharray:3 5;}}
    .axis{{fill:#5d7299;font-size:10px;letter-spacing:.12em;text-anchor:middle;}}
    .spk{{fill:#0A1628;stroke:#4a6a99;}}
    .sl{{fill:#8b9dc3;font-size:8px;font-weight:600;text-anchor:middle;}}
    .bl{{font-size:11px;font-weight:600;text-anchor:middle;paint-order:stroke;
        stroke:#0A1628;stroke-width:2.5px;}}
  </style>
  <defs>
    <radialGradient id="floor" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#16335c"/><stop offset="100%" stop-color="#0A1628"/>
    </radialGradient>
    <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="6"/>
    </filter>
  </defs>
  <circle cx="{C}" cy="{C}" r="{SCALE + 24}" fill="url(#floor)"/>
  <circle cx="{C}" cy="{C}" r="{SCALE * 0.92:.0f}" class="ring"/>
  <circle cx="{C}" cy="{C}" r="{SCALE * 0.55:.0f}" class="ring"/>
  <text x="{C}" y="26" class="axis">FRONT (STAGE)</text>
  <text x="{C}" y="{SIZE - 16}" class="axis">BEHIND (ROOM)</text>
  {blob_svg}
  {label_svg}
  {spk_svg}
  <circle cx="{C}" cy="{C}" r="8" fill="#F0F4F8"/>
  <path d="M {C} {C - 16} L {C - 5} {C - 7} L {C + 5} {C - 7} Z" fill="#F0F4F8"/>
</svg>'''


def _track_card(rec: dict) -> str:
    track = rec.get("track", {})
    cfg = rec.get("config", {})
    measured = rec.get("measured", {})
    routing = cfg.get("routing", {})

    rows = "".join(
        f"<tr><td><b>{ch}</b></td><td>{_sources_str(routing[ch])}</td></tr>"
        for ch in CHANNELS if ch in routing
    )

    levels = measured.get("channel_levels")
    gains = measured.get("channel_gains")
    chan_block = ""
    if levels and gains:
        lr = "".join(
            f"<tr><td><b>{ch}</b></td><td>{_fmt_db(levels[i])} dB</td>"
            f"<td>{_fmt_db(gains[i])} dB</td></tr>"
            for i, ch in enumerate(CHANNELS)
        )
        chan_block = ("<p class='muted'>Optimized channel levels (measured → "
                      "applied gain):</p><table><tr><th>Channel</th>"
                      f"<th>Measured</th><th>Gain</th></tr>{lr}</table>")

    stem_levels = measured.get("stem_levels") or {}
    level_items = "".join(
        f'<li><span class="dot" style="background:{_STEM_COLORS.get(k, "#9FB0C9")}"></span>'
        f'{_e(k.capitalize())}<span class="db">{_fmt_db(v)} dB</span></li>'
        for k, v in stem_levels.items())
    levels_block = (f"<p class='muted'>Measured stem levels</p>"
                    f"<ul class='levels'>{level_items}</ul>") if level_items else ""

    badges = ""
    if cfg.get("separate_crowd"):
        badges += "<span class='badge live'>LIVE</span>"
    if cfg.get("optimized"):
        badges += "<span class='badge opt'>OPTIMIZED</span>"
    badges = f"<span class='badges'>{badges}</span>" if badges else ""

    llm = rec.get("llm")
    llm_block = ""
    if llm:
        u = llm.get("usage") or {}
        usage = (f" — {u.get('input_tokens', '?')} in / {u.get('output_tokens', '?')} out tokens"
                 if u else "")
        cover = " (cover-art frame attached)" if llm.get("cover_art_attached") else ""
        searches = llm.get("searches") or []
        search_count = f" · {len(searches)} web search(es)" if searches else ""
        search_html = ("<p class='muted'>Web searches</p><pre>"
                       + _e("\n".join(searches)) + "</pre>") if searches else ""
        llm_block = (
            f"<details><summary>LLM prompt &amp; response{_e(usage)}{_e(search_count)}</summary>"
            f"{search_html}"
            f"<p class='muted'>System prompt</p><pre>{_e(llm.get('system', ''))}</pre>"
            f"<p class='muted'>User prompt{cover}</p><pre>{_e(llm.get('user', ''))}</pre>"
            f"<p class='muted'>Model response (raw)</p><pre>{_e(llm.get('response', ''))}</pre>"
            "</details>")

    return f"""
    <div class="card">
      {badges}
      <h3>{_e(track.get('title', 'Untitled'))}</h3>
      <p class="scene">{_e(cfg.get('scene', ''))}</p>
      <p>{_e(cfg.get('perspective', ''))}</p>
      {_spatial_svg(rec)}
      <p class="muted">7.1 channel routing · decided by
         <code>{_e(rec.get('model', 'default'))}</code></p>
      <table><tr><th>Channel</th><th>Sources</th></tr>{rows}</table>
      {chan_block}
      {levels_block}
      {llm_block}
    </div>"""


def render_index(records: list[dict], *, artist: str = "", album: str = "",
                 narrative: str = "") -> str:
    cards = "\n".join(_track_card(r) for r in records)
    intro = f"<p>{_e(narrative)}</p>" if narrative else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(album)} — {_e(artist)}</title>
<style>{_CSS}</style></head>
<body>
  <h1>{_e(album)}</h1>
  <p class="muted">{_e(artist)} · {len(records)} track(s) · Natural Perspective Spatial Audio Process</p>
  {intro}
  {cards}
</body></html>
"""


def write_album_index(album_dir: Path, *, artist: str = "", album: str = "",
                      narrative: str = "") -> Path:
    """Scan ``*.config.json`` records in the directory and (re)write index.html."""
    records = []
    for f in sorted(album_dir.glob("*.config.json")):
        try:
            records.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    if not artist or not album:
        for r in records:
            artist = artist or r.get("track", {}).get("artist", "")
            album = album or r.get("track", {}).get("album", "")
    out = album_dir / "index.html"
    out.write_text(render_index(records, artist=artist, album=album, narrative=narrative),
                   encoding="utf-8")
    return out

# Changelog

## v0.2.0 — unreleased

- **YouTube playlist support.** A playlist URL is expanded to every video in it,
  and each is processed as its own track (CLI and GUI). A `watch?v=…&list=…`
  link still processes just that one video, so sharing a video that happens to be
  in a playlist doesn't pull the whole list.
- **Optional Perplexity web search.** Set `PERPLEXITY_API_KEY` and Natural
  Perspective does its recording research (live vs studio, venue, era) via
  Perplexity, then designs the mix on Anthropic's reliable no-tool path. Unset,
  it uses Anthropic's built-in web search as before. Best-effort — falls back if
  Perplexity errors. No new dependencies (stdlib HTTP).
- `.env` is now auto-loaded from the current dir, the project root, or
  `~/.config/spatial-standards/.env` (previously only the current dir).

## v0.1.0 — first public release

Turn any recording into a **7.1 surround mix**. Stems are separated, their
levels measured, and a model invents a scene and routes each instrument around
the listener; a deterministic, safety-clamped renderer writes a lossless
8-channel (7.1) 24-bit FLAC for your media server.

Highlights:
- **Natural Perspective** — a model designs a per-song mix from metadata, cover
  art, and the measured stem levels (no audio is uploaded). With no API key it
  falls back to a built-in mix and runs fully offline.
- **On Stage / Front Row** templates, plus an **Optimized** per-channel leveling
  tier.
- **CLI and GUI**; input is a file, a folder, a URL, or a pre-separated stems
  folder. Video in → video out (MKV) is supported.
- **Per-album `index.html`** documenting each track's scene, soundstage,
  routing, stem levels, and the exact model prompt/response.
- **Turnkey install**: `pip install '.[full]'` brings FFmpeg (via static-ffmpeg),
  Demucs, the crowd model (audio-separator), and yt-dlp. Base install has no
  required Python packages.

Apache-2.0. Builds on FFmpeg, Demucs, audio-separator, yt-dlp, and the Mel-Band
RoFormer crowd model — see `NOTICE` for full credits.

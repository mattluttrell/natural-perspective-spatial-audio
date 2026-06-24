# Changelog

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

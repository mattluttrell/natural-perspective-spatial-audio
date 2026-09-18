# Changelog

## v0.3.0 — unreleased

- **Embedded tags outrank filenames.** Artist, title, track number and year
  are read from a local file's tags (ffprobe); the filename pattern is only
  the fallback, and a leading track number ("01 - Artist - Title") is
  stripped before it is parsed. Previously a ripped album named that way
  was filed under an artist called "01". The source album name
  ("Live at Loft 150") is also given to the model as a live/studio cue, and
  the track number carries through so the spatial album sorts correctly.

## v0.2.0 — 2026-09-10

- **Live progress.** Downloads, the crowd split and Demucs report percent
  done and time remaining as they run (one line that updates, in the GUI and
  in a terminal) instead of a single message followed by minutes of silence.
- **Cancel.** The GUI has a Cancel button that stops the current tool and the
  rest of the batch; closing the window now also stops a running Demucs rather
  than leaving it going in the background.
- **yt-dlp keeps itself current.** A pip/pipx-installed yt-dlp cannot
  self-update, and YouTube breaks old releases every few weeks. The app now
  upgrades the yt-dlp it installed (in its own environment) when PyPI has a
  newer release — checked before downloads, at most once a day — and on a
  failed download it updates and retries once. Manual: **Update yt-dlp** in
  the GUI or `spatial-standards --update-ytdlp`; opt out with
  `SPATIAL_STANDARDS_NO_YTDLP_UPDATE=1`. A yt-dlp from elsewhere is left
  alone, with the update command shown in the error instead.
- `[full]` now installs `yt-dlp[default,deno]` — the JavaScript runtime and
  challenge scripts YouTube requires; a bare `yt-dlp` no longer works there.
- **Sound:** every channel limiter now really caps at 0.95. ffmpeg's
  `alimiter` auto-level (on by default) scaled the output back up by 1/limit,
  so the ceiling had been 0 dBFS (measured +0.45 dB). Demucs now writes
  24-bit stems (`--int24`) instead of 16-bit into the 24-bit mix. The rest
  of the listening review — LFE level, BS.1770 loudness, stereo stems,
  a better vocal model — is in `docs/listening-review-2026-09.md`.
- **GUI:** paste an Anthropic API key straight into the window (saved to
  `~/.config/spatial-standards/.env`, no relaunch); a tool status line under
  the log (which tools resolved, which yt-dlp); an "Open output folder"
  button; Delete/Backspace remove selected inputs; per-track and total
  elapsed times; a shorter input list so Go and the log stay on screen on
  HiDPI displays.
- `scripts/release_check.py`: a monthly "does this need a release?" report —
  unreleased work on main, plus a fresh `pip install` of the *published*
  package run end to end on CPU (and one model call) so a dependency that
  breaks new users is noticed within the month.
- Faster: stem-level measurement and stem caching run their seven ffmpeg
  passes concurrently instead of one after another.
- Fixed: an unexpected error while reading inputs left the GUI stuck on
  "Working…"; a failed tag write is now reported with FFmpeg's message.
- yt-dlp is told which FFmpeg to use (`--ffmpeg-location`) when one is
  configured, so its WAV/MKV conversion matches the rest of the pipeline.
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

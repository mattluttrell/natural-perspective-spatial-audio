# Natural Perspective Spatial Audio

Turn any recording into a 7.1 surround mix. After a song is split into its
instrument stems, a model invents a scene and decides where each instrument
sits around you — then a deterministic renderer builds a lossless 8-channel
(7.1) FLAC for your media server.

![Natural Perspective — a different soundstage designed per song](https://raw.githubusercontent.com/mattluttrell/natural-perspective-spatial-audio/main/examples/demo.gif)

*One real mix: every stem placed on the stage, the crowd behind you.*

## See it — no install

Open **[`examples/index.html`](examples/index.html)** in any browser, on any OS.
It's a finished mix's scene, soundstage, routing, and stem levels — the actual
output of the tool.

## Run it

Easiest — install from PyPI with [pipx](https://pipx.pypa.io), no clone needed.
Needs **Python 3.10+** and a **system FFmpeg**:

```bash
brew install ffmpeg                   # macOS; Linux: sudo apt install ffmpeg
pipx install 'natural-perspective-spatial-audio[full]'
spatial-standards-gui                # or:  spatial-standards song.flac
```

Or from a clone — **one command** (macOS/Linux) sets up a private virtualenv and
installs everything; on a Mac with Homebrew it installs Python for you too:

```bash
./install.sh
./gui                                # the GUI   (or: .venv/bin/spatial-standards song.flac)
```

<details><summary>Manual / Windows</summary>

Needs **Python 3.10+** (3.12 recommended — widest wheel coverage):

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install '.[full]'                # quote it — the [..] is a shell glob otherwise
spatial-standards song.flac          # also: a folder, or a URL
spatial-standards-gui                # or the GUI
```
</details>

The GUI uses **Tkinter**: it ships with the python.org installer (recommended on
macOS), with Homebrew add `python-tk`, on Debian/Ubuntu `apt install python3-tk`.

`[full]` brings everything as Python packages — FFmpeg + ffprobe (via
`static-ffmpeg`), Demucs, the crowd model (`audio-separator`), and `yt-dlp` —
so a fresh machine works after one install, no system setup. It's **CPU by
default**; for an NVIDIA GPU install a CUDA build of PyTorch from pytorch.org
and `pip install 'audio-separator[gpu]'` (much faster). The **first run
downloads model weights** (a few hundred MB).

Prefer your own tools? `pip install .` has **no required Python packages** and
just calls `ffmpeg`, `demucs`, `audio-separator`, and `yt-dlp` from your PATH.

The model layer needs an `ANTHROPIC_API_KEY`; without one the tool falls back to
a built-in mix and runs fully offline.

Output drops straight into Plex/Jellyfin/Kodi:

```
<Artist>/Natural Perspective Spatial Audio/<Title> [...].flac   (+ per-album index.html)
```

## How it works

Separate stems → measure each stem's level → a model invents a scene and emits a
full mix [configuration](CONFIG_SCHEMA.md) → a safety-clamped renderer builds the
7.1 FLAC. **No audio is uploaded** — only metadata, cover art, and the measured
stem levels inform the design. Every track saves its config and an `index.html`
documenting the scene, routing, and the exact model prompt/response.

## Your responsibility, and credits

By processing a file or URL you affirm you have the right to it. This tool hosts
no content and ships no audio. See [`NOTICE`](NOTICE) for that and full credit to
the projects it builds on — FFmpeg, Demucs, audio-separator, yt-dlp, and the
Mel-Band RoFormer crowd model.

## License

Apache-2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE). Provided **as is**,
without warranty of any kind.

"""Input handling: a local audio file passes straight through; a URL is
downloaded to WAV with yt-dlp (an external command — never bundled)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .video import VIDEO_EXTENSIONS

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".alac"}

# yt-dlp leaves per-format DASH fragments like "Title.f636.mp4" / "Title.f140.m4a"
# (video-only or audio-only) next to the merged file. Skip them in folder scans.
_FRAGMENT_RE = re.compile(r"\.f\d+$")


def is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def has_directory(sources: list[str]) -> bool:
    """True if any source is a local directory (drives the GUI's recursive
    checkbox visibility)."""
    return any(not is_url(s) and Path(s).expanduser().is_dir() for s in sources)


def expand_inputs(sources: list[str], recursive: bool = True) -> list[str]:
    """Expand any directories into the audio files they contain (sorted);
    pass through files and URLs unchanged. `recursive` controls whether
    sub-folders are descended into (rglob) or only the top level (glob)."""
    out: list[str] = []
    for s in sources:
        p = Path(s).expanduser()
        if not is_url(s) and p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            found = sorted(
                str(f) for f in it
                if f.is_file() and f.suffix.lower() in (AUDIO_EXTENSIONS | VIDEO_EXTENSIONS)
                and not _FRAGMENT_RE.search(f.stem)
            )
            if not found:
                where = "directory (or its sub-folders)" if recursive else "top of directory"
                raise FileNotFoundError(f"No audio files found in {where}: {s}")
            out.extend(found)
        else:
            out.append(s)
    return out


def ingest(source: str, work_dir: Path, ytdlp_bin: str = "yt-dlp",
           want_video: bool = False) -> tuple[Path, str | None]:
    """Return (media-path, source-title-or-None) for a file path or URL.

    Local audio or video files pass straight through. For URLs, `want_video`
    downloads the full video (a single video — playlists are not expanded)
    instead of the default audio-only extraction."""
    if not is_url(source):
        p = Path(source).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"Input not found: {source}")
        if p.suffix.lower() not in AUDIO_EXTENSIONS and p.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError(f"Unrecognized media extension: {p.suffix} ({source})")
        return p, None

    work_dir.mkdir(parents=True, exist_ok=True)
    # --no-cookies-from-browser overrides a global `--cookies-from-browser` in
    # the user's yt-dlp config (which fails without a keyring / secretstorage);
    # our downloads don't need browser cookies for public content.
    if want_video:
        cmd = [
            ytdlp_bin, "--no-cookies-from-browser",
            "-f", "bv*+ba/b", "--merge-output-format", "mkv",
            "--no-playlist", "-o", str(work_dir / "%(id)s.%(ext)s"),
            "--print", "%(title)s", "--print", "after_move:filepath",
            "--no-simulate", "--quiet", source,
        ]
        exts = (".mkv", ".mp4", ".webm", ".mov", ".m4v")
    else:
        cmd = [
            ytdlp_bin, "--no-cookies-from-browser",
            "-x", "--audio-format", "wav", "--no-playlist",
            "-o", str(work_dir / "%(id)s.%(ext)s"),
            "--print", "%(title)s", "--print", "after_move:filepath",
            "--no-simulate", "--quiet", source,
        ]
        exts = (".wav",)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{proc.stderr.strip()}")
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    files = [ln for ln in lines if ln.lower().endswith(exts)]
    if not files:
        raise RuntimeError("yt-dlp reported success but produced no output file")
    title = lines[0] if lines and lines[0] != files[-1] else None
    return Path(files[-1]), title

"""FLAC tagging for media-server-friendly output. ALBUMARTIST is the tag
Plex actually groups on — its absence is the classic "scattered album"
failure. COMMENT records the standard + tool version so provenance survives
file moves. No artwork is fetched, ever.

Tags are written with FFmpeg — already required everywhere else — so the
package carries no mandatory third-party Python dependency.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import __version__


def tag_flac(path: Path, *, title: str, artist: str, album: str,
             track_number: int | None = None, date: str | None = None,
             comment: str | None = None, genre: str | None = None,
             lyrics: str | None = None, ffmpeg: str = "ffmpeg") -> None:
    """Replace the FLAC's Vorbis comments with the given tags, in place.

    FFmpeg cannot edit tags in place, so this stream-copies to a temporary
    file (``-c copy`` — no re-encode, cover art preserved) with fresh metadata,
    then atomically replaces the original. ``os.replace`` is atomic on Windows,
    macOS, and Linux alike.
    """
    path = Path(path)
    tags: dict[str, str] = {
        "TITLE": title,
        "ARTIST": artist,
        "ALBUMARTIST": artist,
        "ALBUM": album,
        "COMMENT": comment or f"{album} — spatial-standards v{__version__}",
    }
    if track_number is not None:
        tags["TRACKNUMBER"] = str(track_number)
    if date:
        tags["DATE"] = date
    if genre:
        tags["GENRE"] = genre
    if lyrics:
        # Plex reads either tag; set both so the scene/perspective shows in-app.
        tags["LYRICS"] = lyrics
        tags["UNSYNCEDLYRICS"] = lyrics

    # Temp file beside the original (same filesystem → atomic replace), .flac
    # suffix so FFmpeg picks the FLAC muxer.
    tmp = path.with_name(path.name + ".tagtmp.flac")
    cmd = [ffmpeg, "-nostdin", "-y", "-loglevel", "error",
           "-i", str(path), "-map", "0", "-c", "copy", "-map_metadata", "-1"]
    for key, value in tags.items():
        cmd += ["-metadata", f"{key}={value}"]
    cmd.append(str(tmp))
    try:
        subprocess.run(cmd, check=True)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

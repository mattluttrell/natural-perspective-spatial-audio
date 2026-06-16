"""Video helpers for Natural Perspective.

When the input carries a real video stream, the pipeline keeps it: it extracts
the audio for separation, grabs a frame as the model's scene cue, and muxes the
finished 7.1 FLAC back with the original video copied (no re-encode) into an
MKV — Matroska carries FLAC natively and plays in Plex/Jellyfin/Kodi/VLC.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v",
                    ".mpg", ".mpeg", ".ts", ".wmv", ".flv", ".m2ts"}


def _ffprobe_bin(ffmpeg_bin: str) -> str:
    d, b = os.path.split(ffmpeg_bin)
    probe = b.replace("ffmpeg", "ffprobe")
    return os.path.join(d, probe) if d else probe


def has_video(path: Path | str, ffmpeg_bin: str = "ffmpeg") -> bool:
    """True if the file has a real (non-cover-art) video stream. Uses ffprobe
    when available, else falls back to the file extension."""
    p = Path(path)
    try:
        proc = subprocess.run(
            [_ffprobe_bin(ffmpeg_bin), "-v", "error", "-select_streams", "v",
             "-show_entries", "stream_disposition=attached_pic",
             "-of", "csv=p=0", str(p)],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            # One value per video stream: "0" = real video, "1" = cover art.
            return any(ln.strip() == "0" for ln in proc.stdout.splitlines())
    except FileNotFoundError:
        pass
    return p.suffix.lower() in VIDEO_EXTENSIONS


def has_audio(path: Path | str, ffmpeg_bin: str = "ffmpeg") -> bool:
    """True if the file has a readable audio stream. A corrupt/unreadable file
    (ffprobe error) or a stream-less file returns False. When ffprobe is
    unavailable, assume audio is present and let downstream tools decide."""
    try:
        proc = subprocess.run(
            [_ffprobe_bin(ffmpeg_bin), "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return True
    if proc.returncode != 0:
        return False
    return "audio" in proc.stdout


def extract_audio(video: Path, out_wav: Path, ffmpeg_bin: str = "ffmpeg") -> Path:
    """Extract the audio track to a 24-bit WAV for separation."""
    proc = subprocess.run(
        [ffmpeg_bin, "-nostdin", "-y", "-loglevel", "error", "-i", str(video),
         "-vn", "-c:a", "pcm_s24le", str(out_wav)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"audio extraction failed:\n{proc.stderr.strip()[-1000:]}")
    return out_wav


def extract_cover_frame(video: Path, out_jpg: Path, ffmpeg_bin: str = "ffmpeg") -> Path | None:
    """Grab one frame as the Natural Perspective scene cue (best-effort)."""
    for ss in ("3", "0"):
        proc = subprocess.run(
            [ffmpeg_bin, "-nostdin", "-y", "-loglevel", "error",
             "-ss", ss, "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out_jpg)],
            capture_output=True, text=True,
        )
        if proc.returncode == 0 and Path(out_jpg).exists():
            return out_jpg
    return None


def mux(video: Path, audio_flac: Path, out_file: Path, ffmpeg_bin: str = "ffmpeg",
        *, title: str | None = None, artist: str | None = None,
        album: str | None = None, comment: str | None = None,
        genre: str | None = None) -> Path:
    """Copy the original video and replace its audio with the 7.1 FLAC, into MKV."""
    cmd = [ffmpeg_bin, "-nostdin", "-y", "-loglevel", "error",
           "-i", str(video), "-i", str(audio_flac),
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "copy",
           "-shortest"]
    for key, val in (("title", title), ("artist", artist), ("album", album),
                     ("comment", comment), ("genre", genre)):
        if val:
            cmd += ["-metadata", f"{key}={val}"]
    cmd += [str(out_file)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"video mux failed:\n{proc.stderr.strip()[-1500:]}")
    return out_file

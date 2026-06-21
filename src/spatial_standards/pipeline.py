"""Orchestration: input → stems → spatial mix → optional Optimized pass →
tagged FLAC in the media-server layout.

Separation results (the expensive model passes) are cached under
~/.cache/spatial-standards keyed by the audio content hash, so re-running
the same song under a different standard or Optimized setting costs seconds.
Set SPATIAL_STANDARDS_NO_CACHE=1 to disable."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from . import ingest as ingest_mod
from . import mix, mixconfig, natural, optimize, report, separate, video
from .system_profile import SystemProfile, parse_system_profile, parse_system_profile_text
from .tag import tag_flac

STANDARDS = {"onstage": "On Stage", "frontrow": "Front Row"}

NATURAL_ALBUM = "Natural Perspective Spatial Audio"


class SkippedInput(Exception):
    """Input that can't be processed (corrupt / no readable audio) — the batch
    should skip it cleanly rather than treat it as a hard failure."""


def _finite(x: float | None) -> float | None:
    """JSON-safe level: -inf / NaN / None -> null, else rounded float."""
    if x is None or x != x or x == float("-inf"):
        return None
    return round(float(x), 2)


def _finite_list(xs: list[float] | None) -> list[float | None] | None:
    return None if xs is None else [_finite(x) for x in xs]


@dataclass
class Bins:
    ffmpeg: str = "ffmpeg"
    demucs: str = "demucs"
    separator: str = "audio-separator"
    ytdlp: str = "yt-dlp"


def resolve_bin(name: str) -> str:
    """Locate an external tool. Order: an absolute/explicit path or one on PATH,
    else the same directory as the running Python — which is where
    ``pip install '.[full]'`` drops ``demucs``/``audio-separator``/``yt-dlp``,
    so the GUI finds them even when the venv was never "activated". Falls back
    to the bare name so the clear "not found" error still fires."""
    found = shutil.which(name)
    if found:
        return found
    bindir = Path(sys.executable).resolve().parent
    for cand in (bindir / name, bindir / f"{name}.exe"):
        if cand.exists():
            return str(cand)
    return name


def ensure_ffmpeg_on_path(ffmpeg: str = "ffmpeg") -> None:
    """If ffmpeg isn't found, fall back to the pip-installed ``static-ffmpeg``
    (the ``[full]`` extra), which provides both ffmpeg and ffprobe and prepends
    them to PATH — so a fresh machine works with no system FFmpeg install. The
    binaries are fetched once on first use. Best-effort and silent: if neither
    a system ffmpeg nor static-ffmpeg is present, the normal "not found" error
    later guides the user to install FFmpeg."""
    if shutil.which(ffmpeg) or os.path.isfile(ffmpeg):
        return
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()  # downloads once, prepends ffmpeg+ffprobe to PATH
    except Exception:
        pass


@dataclass
class TrackMeta:
    artist: str | None = None
    title: str | None = None
    track_number: int | None = None
    date: str | None = None


def _cache_root() -> Path | None:
    if os.environ.get("SPATIAL_STANDARDS_NO_CACHE"):
        return None
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "spatial-standards"


def _audio_key(audio: Path) -> str:
    h = hashlib.sha1()
    with open(audio, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_flac(src: Path, dest: Path, ffmpeg_bin: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [ffmpeg_bin, "-nostdin", "-y", "-loglevel", "error",
         "-i", str(src), "-c:a", "flac", str(dest)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"flac cache write failed:\n{proc.stderr.strip()[-500:]}")


def _separated(standard: str, audio: Path, work_dir: Path, bins: Bins,
               step) -> tuple[dict[str, Path], Path | None]:
    """Return (stems, crowd-or-None) for `standard`, via the cache when
    possible. Front Row separates crowd-first; On Stage separates the
    original (its stems intentionally keep any ambience)."""
    cache_root = _cache_root()
    cdir = None
    if cache_root is not None:
        cdir = cache_root / _audio_key(audio) / standard
        stems = {n: cdir / f"{n}.flac" for n in separate.STEM_NAMES}
        crowd = cdir / "crowd.flac"
        complete = all(p.exists() for p in stems.values()) and \
            (standard != "frontrow" or crowd.exists())
        if complete:
            step("using cached separation…")
            return stems, (crowd if standard == "frontrow" else None)

    crowd_file = None
    if standard == "frontrow":
        step("splitting crowd from performance (crowd model)…")
        performance, crowd_file = separate.crowd_pass(audio, work_dir, bins.separator)
        step("separating instruments (Demucs)…")
        raw = separate.separate_stems(performance, work_dir, bins.demucs)
    else:
        step("separating instruments (Demucs)…")
        raw = separate.separate_stems(audio, work_dir, bins.demucs)

    if cdir is None:
        return raw, crowd_file

    for name, src in raw.items():
        _to_flac(src, cdir / f"{name}.flac", bins.ffmpeg)
    if crowd_file is not None:
        _to_flac(crowd_file, cdir / "crowd.flac", bins.ffmpeg)
    stems = {n: cdir / f"{n}.flac" for n in separate.STEM_NAMES}
    return stems, (cdir / "crowd.flac" if crowd_file is not None else None)


def _ingest_cached(source: str, work_dir: Path, bins: Bins, step,
                   want_video: bool = False) -> tuple[Path, str | None]:
    """Ingest with a download cache for URLs (keyed by URL hash); local
    files pass straight through. `want_video` downloads the video for URLs and
    is cached separately from the audio-only download."""
    cache_root = _cache_root()
    if not ingest_mod.is_url(source):
        return ingest_mod.ingest(source, work_dir / "audio", bins.ytdlp, want_video=want_video)

    msg = "downloading video…" if want_video else "downloading audio…"
    if cache_root is None:
        step(msg)
        return ingest_mod.ingest(source, work_dir / "audio", bins.ytdlp, want_video=want_video)

    ext = "mkv" if want_video else "wav"
    key = hashlib.sha1((("video:" if want_video else "") + source).encode()).hexdigest()
    cached = cache_root / "downloads" / f"{key}.{ext}"
    title_file = cached.with_suffix(".title")
    if cached.exists():
        step("using cached download…")
        title = title_file.read_text().strip() if title_file.exists() else None
        return cached, title or None

    step(msg)
    media, title = ingest_mod.ingest(source, work_dir / "audio", bins.ytdlp, want_video=want_video)
    cached.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(media, cached)
    if title:
        title_file.write_text(title + "\n")
    return cached, title


def _require_bin(command: str, what: str, flag: str) -> None:
    if shutil.which(command) is None:
        raise RuntimeError(
            f"{command!r} not found — {what} is required. "
            f"Install it, or point at it with {flag}."
        )


def album_name(standard: str, optimized: bool) -> str:
    base = STANDARDS[standard]
    return f"{base} Optimized Spatial Audio" if optimized else f"{base} Spatial Audio"


def _safe_filename(s: str) -> str:
    return re.sub(r'[/:\\*?"<>|]', "-", s)


def _split_artist_title(candidate: str) -> tuple[str | None, str | None]:
    """Split 'Artist - Title' on the first ' - ' that sits OUTSIDE brackets —
    titles like 'Belief (Live at the Nokia Theatre, CA - December 2007)'
    must not split on the dash inside the parenthetical."""
    depth = 0
    for i in range(len(candidate) - 2):
        c = candidate[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and candidate[i:i + 3] == " - ":
            return candidate[:i].strip(), candidate[i + 3:].strip()
    return None, None


def _derive_meta(source: str, src_title: str | None, meta: TrackMeta) -> tuple[str, str]:
    """Resolve (artist, title) from explicit flags, then 'Artist - Title'
    patterns in the source title/filename, then fallbacks."""
    artist, title = meta.artist, meta.title
    candidate = src_title or Path(source).stem
    if not artist or not title:
        left, right = _split_artist_title(candidate)
        if left and right:
            artist = artist or left
            title = title or right
    return artist or "Unknown Artist", title or candidate


def process(source: str, *, standard: str, optimized: bool, out_dir: Path,
            meta: TrackMeta | None = None, bins: Bins | None = None,
            system_profile: SystemProfile | None = None,
            collect: dict | None = None,
            keep_work: bool = False, progress=None) -> Path:
    """Process one input (file or URL) into a tagged spatial FLAC.

    `progress`, if given, is called with a short message as each stage
    starts (download, crowd split, stem separation, mix, leveling).

    Returns the final output path:
      <out_dir>/<Artist>/<Album>/<Title> [<Album>].flac
    """
    step = progress or (lambda msg: None)
    if standard not in STANDARDS:
        raise ValueError(f"unknown standard: {standard!r} (use one of {sorted(STANDARDS)})")
    meta = meta or TrackMeta()
    bins = bins or Bins()
    album = album_name(standard, optimized)

    _require_bin(bins.ffmpeg, "FFmpeg", "--ffmpeg-bin")
    _require_bin(bins.demucs, "Demucs (source separation)", "--demucs-bin")
    if standard == "frontrow":
        _require_bin(bins.separator, "audio-separator (Front Row crowd pass)", "--separator-bin")
    if ingest_mod.is_url(source):
        _require_bin(bins.ytdlp, "yt-dlp (URL inputs)", "--ytdlp-bin")

    work_dir = Path(tempfile.mkdtemp(prefix="spatial-standards-"))
    try:
        audio, src_title = _ingest_cached(source, work_dir, bins, step)
        if not video.has_audio(audio, bins.ffmpeg):
            raise SkippedInput(f"no readable audio in {Path(source).name}")
        artist, title = _derive_meta(source, src_title, meta)

        mix_file = work_dir / "mix.flac"
        # Crowd-first for Front Row: crowd is split from the FULL mix (the
        # model's training domain) before Demucs, so stems never contained
        # applause. Separation results are cached by audio content hash.
        stems, crowd = _separated(standard, audio, work_dir, bins, step)
        step("mixing 7.1 stage…")
        if standard == "frontrow":
            mix.mix_front_row(stems, [crowd], mix_file, bins.ffmpeg)
        else:
            mix.mix_on_stage(stems, mix_file, bins.ffmpeg)

        final_src = mix_file
        if optimized:
            step("leveling channels (Optimized pass)…")
            levels = optimize.measure_channel_rms(mix_file, bins.ffmpeg)
            if system_profile is not None:
                target = (system_profile.target if system_profile.target is not None
                          else optimize.TARGET_TOTAL_DB)
                gains = optimize.compute_gains(levels, offsets=system_profile.offsets, target=target)
            else:
                gains = optimize.compute_gains(levels)
            if collect is not None:
                collect["levels"] = levels
                collect["gains"] = gains
            final_src = optimize.apply_gains(mix_file, gains, work_dir / "mix_optimized.flac", bins.ffmpeg)

        dest_dir = out_dir / _safe_filename(artist) / album
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{_safe_filename(title)} [{album}].flac"
        shutil.move(str(final_src), dest)
        tag_flac(dest, title=title, artist=artist, album=album,
                 track_number=meta.track_number, date=meta.date, ffmpeg=bins.ffmpeg)
        if collect is not None:
            collect.update(standard=standard, optimized=optimized,
                           artist=artist, title=title, album=album, dest=dest)
        return dest
    finally:
        if not keep_work:
            shutil.rmtree(work_dir, ignore_errors=True)


def process_natural(source: str = "", *, out_dir: Path, meta: TrackMeta | None = None,
                    bins: Bins | None = None, comments: Path | None = None,
                    comments_text: str | None = None,
                    cover_art: Path | None = None, want_video: bool = False,
                    stems_dir: Path | None = None,
                    model: str = natural.DEFAULT_MODEL,
                    api_key: str | None = None, force_default: bool = False,
                    web_search: bool = True,
                    keep_work: bool = False, progress=None,
                    decision_client=None) -> tuple[Path, Path]:
    """The Natural Perspective process: separate (crowd-first) → measure stem
    levels → a model designs the full mix config (or the default config when
    no key / on any failure) → render it → optional Optimized leveling → tag,
    write the config sidecar, and (re)build the album index.html.

    `comments` is the optional comments.md: its per-channel dB trims feed the
    Optimized pass; its full text guides the model. Returns (flac, sidecar).

    `stems_dir`, if given, supplies pre-separated stems (``<name>.flac`` for
    each stem, plus optional ``crowd.flac``) — e.g. a crowd-first stems folder —
    so ingestion and separation are skipped and the mix is built straight from
    them (output is FLAC; artist/title come from `meta`)."""
    step = progress or (lambda msg: None)
    meta = meta or TrackMeta()
    bins = bins or Bins()

    _require_bin(bins.ffmpeg, "FFmpeg", "--ffmpeg-bin")
    if stems_dir is None:
        _require_bin(bins.demucs, "Demucs (source separation)", "--demucs-bin")
        _require_bin(bins.separator, "audio-separator (crowd pass)", "--separator-bin")
        if ingest_mod.is_url(source):
            _require_bin(bins.ytdlp, "yt-dlp (URL inputs)", "--ytdlp-bin")

    system_profile = None
    if comments is not None:
        system_profile = parse_system_profile(comments)
        try:
            comments_text = Path(comments).read_text(encoding="utf-8")
        except OSError:
            comments_text = None
    elif comments_text:
        # inline notes typed by the user (not a file): parse any trims from it too
        system_profile = parse_system_profile_text(comments_text)

    work_dir = Path(tempfile.mkdtemp(prefix="spatial-standards-"))
    try:
        src_title = None
        video_path = None
        if stems_dir is not None:
            # Pre-separated stems (e.g. a crowd-first stems folder): skip ingest
            # and separation and mix straight from them.
            stems = {n: stems_dir / f"{n}.flac" for n in separate.STEM_NAMES}
            missing = [n for n, p in stems.items() if not p.exists()]
            if missing:
                raise SkippedInput(f"stems folder missing {missing}: {stems_dir}")
            crowd_path = stems_dir / "crowd.flac"
            crowd = crowd_path if crowd_path.exists() else None
            artist = meta.artist or "Unknown Artist"
            title = meta.title or stems_dir.name
        else:
            media, src_title = _ingest_cached(source, work_dir, bins, step, want_video=want_video)
            if not video.has_audio(media, bins.ffmpeg):
                raise SkippedInput(f"no readable audio in {Path(source).name}")
            artist, title = _derive_meta(source, src_title, meta)

            # If the input carries video, keep it for muxing and process its audio.
            audio = media
            if video.has_video(media, bins.ffmpeg):
                video_path = media
                step("extracting audio from video…")
                audio = video.extract_audio(media, work_dir / "extracted.wav", bins.ffmpeg)
                if cover_art is None:
                    cover_art = video.extract_cover_frame(media, work_dir / "cover.jpg", bins.ffmpeg)

            # Always crowd-first, so the model sees the crowd stem's actual level.
            stems, crowd = _separated("frontrow", audio, work_dir, bins, step)

        step("measuring stem levels…")
        stem_levels = optimize.measure_stem_levels(stems, crowd, bins.ffmpeg)

        config = None
        llm_trace: dict = {}
        if not force_default:
            step("designing the mix (Natural Perspective)…")
            try:
                config = natural.decide(
                    artist=meta.artist or (None if artist == "Unknown Artist" else artist),
                    title=meta.title or title, source_title=src_title, source=source,
                    cover_art=cover_art, comments_text=comments_text,
                    system_profile=system_profile, stem_levels=stem_levels,
                    model=model, api_key=api_key, web_search=web_search,
                    trace=llm_trace, client=decision_client)
                step(f"  scene: {config.get('scene')}")
            except Exception as e:  # no key, no SDK, bad output, API error
                step(f"  model unavailable ({e}); using default config")
                llm_trace = {}
        used_model = model if config is not None else "default"
        if config is None:
            config = mixconfig.default_config()

        mix_file = work_dir / "mix.flac"
        step("mixing 7.1 stage from config…")
        mixconfig.mix_from_config(stems, crowd, config, mix_file, bins.ffmpeg)
        channel_levels = optimize.measure_channel_rms(mix_file, bins.ffmpeg)

        # A model config can occasionally yield a silent mix (e.g. all-zero
        # weights) — fall back to the known-good default rather than emit silence.
        if used_model != "default" and all(
                lv <= optimize.SILENCE_FLOOR_DB for lv in channel_levels):
            step("  model mix was silent — falling back to the default config")
            config = mixconfig.default_config()
            used_model = "default (model mix was silent)"
            mixconfig.mix_from_config(stems, crowd, config, mix_file, bins.ffmpeg)
            channel_levels = optimize.measure_channel_rms(mix_file, bins.ffmpeg)

        optimized = bool(config.get("optimized")) or (system_profile is not None)
        channel_gains = None
        final_src = mix_file
        if optimized:
            step("leveling channels (Optimized pass)…")
            if system_profile is not None:
                target = (system_profile.target if system_profile.target is not None
                          else optimize.TARGET_TOTAL_DB)
                channel_gains = optimize.compute_gains(
                    channel_levels, offsets=system_profile.offsets, target=target)
            else:
                channel_gains = optimize.compute_gains(channel_levels)
            final_src = optimize.apply_gains(mix_file, channel_gains,
                                             work_dir / "mix_optimized.flac", bins.ffmpeg)

        comment, genre, lyrics = natural.tag_fields(config)
        dest_dir = out_dir / _safe_filename(artist) / NATURAL_ALBUM
        dest_dir.mkdir(parents=True, exist_ok=True)
        if video_path is not None:
            dest = dest_dir / f"{_safe_filename(title)} [{NATURAL_ALBUM}].mkv"
            step("muxing spatial audio into video…")
            video.mux(video_path, final_src, dest, bins.ffmpeg,
                      title=title, artist=artist, album=NATURAL_ALBUM,
                      comment=comment, genre=genre)
        else:
            dest = dest_dir / f"{_safe_filename(title)} [{NATURAL_ALBUM}].flac"
            shutil.move(str(final_src), dest)
            tag_flac(dest, title=title, artist=artist, album=NATURAL_ALBUM,
                     track_number=meta.track_number, date=meta.date,
                     comment=comment, genre=genre, lyrics=lyrics, ffmpeg=bins.ffmpeg)

        record = {
            "track": {"artist": artist, "title": title, "album": NATURAL_ALBUM},
            "config": config,
            "measured": {
                "stem_levels": {k: _finite(v) for k, v in stem_levels.items()},
                "channel_levels": _finite_list(channel_levels),
                "channel_gains": _finite_list(channel_gains),
            },
            "system": ({"offsets": system_profile.offsets, "target": system_profile.target}
                       if system_profile is not None else None),
            "model": used_model,
            "llm": llm_trace or None,
            "tool_version": __version__,
        }
        sidecar = dest.with_name(f"{dest.stem}.config.json")
        sidecar.write_text(json.dumps(record, indent=2), encoding="utf-8")
        report.write_album_index(dest_dir, artist=artist, album=NATURAL_ALBUM)
        step("  documented: index.html")
        return dest, sidecar
    finally:
        if not keep_work:
            shutil.rmtree(work_dir, ignore_errors=True)


def retag_tree(root: Path, progress=None, ffmpeg: str = "ffmpeg") -> tuple[int, int]:
    """Re-apply Plex tags to the rendered FLACs under `root` from each
    ``.config.json`` sidecar (scene/perspective in the comment, a genre chip,
    and the full routing in the lyrics field) — no re-mixing. MKV outputs are
    left alone. Returns (retagged, skipped)."""
    step = progress or (lambda msg: None)
    suffix = ".config.json"
    done = skipped = 0
    for cfg_path in sorted(Path(root).rglob("*" + suffix)):
        flac = cfg_path.with_name(cfg_path.name[:-len(suffix)] + ".flac")
        if not flac.exists():
            skipped += 1
            continue
        try:
            rec = json.loads(cfg_path.read_text(encoding="utf-8"))
            track = rec.get("track", {})
            comment, genre, lyrics = natural.tag_fields(rec.get("config", {}))
            tag_flac(flac, title=track.get("title", flac.stem),
                     artist=track.get("artist", "Unknown Artist"),
                     album=track.get("album", NATURAL_ALBUM),
                     comment=comment, genre=genre, lyrics=lyrics, ffmpeg=ffmpeg)
            step(f"retagged: {flac.name}")
            done += 1
        except Exception as e:
            step(f"skip {flac.name}: {e}")
            skipped += 1
    return done, skipped

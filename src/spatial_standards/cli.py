"""spatial-standards CLI.

Examples:
  spatial-standards song.flac --standard onstage
  spatial-standards "https://…watch?v=…" --standard frontrow --optimized \\
      --artist "John Mayer" --title "Belief (Live)"
  spatial-standards album/*.mp3 --standard onstage --out ~/SpatialMusic
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from . import RIGHTS_NOTICE, __version__, proc, ytdlp_update
from .envfile import load_env
from .ingest import expand_inputs
from .pipeline import (STANDARDS, Bins, SkippedInput, TrackMeta,
                       ensure_ffmpeg_on_path, process, process_natural,
                       resolve_bin, retag_tree)
from .system_profile import parse_system_profile


class _Printer:
    """Step logger for the terminal. On a TTY each step's progress updates
    ("separating … 45% · about 1:02 left") redraw the step's own line, so a
    five-minute Demucs run doesn't scroll pages; piped output gets one line
    per update. Call `done()` before printing anything else directly."""

    def __init__(self):
        self.tty = sys.stdout.isatty()
        self.last: str | None = None  # the message currently on the open line

    def __call__(self, msg: str) -> None:
        if not self.tty:
            print(f"  {msg}", flush=True)
            return
        if self.last is not None and not proc.replaces(self.last, msg):
            print()  # finish the previous line
        width = shutil.get_terminal_size().columns - 1
        print(f"\r  {msg}"[:width].ljust(width), end="", flush=True)
        self.last = msg

    def done(self) -> None:
        if self.last is not None:
            print()
            self.last = None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="spatial-standards",
        description="Turn any recording into an 8-channel (7.1) 24-bit FLAC "
                    "spatial mix under the On Stage or Front Row Spatial Standard.",
        epilog=RIGHTS_NOTICE,
    )
    p.add_argument("inputs", nargs="*",
                   help="audio/video file(s), folder(s), or URL(s) (omit when using --stems-dir)")
    p.add_argument("--standard", default="natural", choices=sorted(STANDARDS) + ["natural"],
                   help="natural (default): Natural Perspective — a model designs the "
                        "mix per track (needs ANTHROPIC_API_KEY; falls back to the "
                        "default config without one). onstage / frontrow: the legacy "
                        "fixed mixes.")
    p.add_argument("--optimized", action="store_true",
                   help="legacy onstage/frontrow only: add the Optimized tier "
                        "(per-channel level smoothing + album-consistent loudness)")
    p.add_argument("--comments", type=Path, metavar="FILE",
                   help="markdown notes about the audio and your playback rig "
                        "(comments.md): the free text guides the Natural Perspective "
                        "model; any per-channel dB trims tune the Optimized pass")
    p.add_argument("--cover", type=Path, metavar="FILE",
                   help="cover-art image, a cue for the Natural Perspective design")
    p.add_argument("--model", default="claude-sonnet-4-6",
                   help="model for the Natural Perspective design (default: claude-sonnet-4-6, "
                        "which supports web research; pass claude-haiku-4-5 to save cost)")
    p.add_argument("--default-config", action="store_true",
                   help="Natural Perspective: skip the model and use the default config")
    p.add_argument("--no-research", action="store_true",
                   help="Natural Perspective: disable the model's web research "
                        "(it identifies artist/venue/live-vs-studio). Web search is "
                        "the bulk of the per-song cost, so this is the cheapest way "
                        "to keep the model designing the mix.")
    p.add_argument("--stems-dir", type=Path, metavar="DIR",
                   help="Natural Perspective from a pre-separated stems folder "
                        "(<name>.flac per stem + optional crowd.flac); skips separation. "
                        "Use --artist/--title for naming.")
    p.add_argument("--retag", type=Path, metavar="DIR",
                   help="re-apply Plex tags (scene/perspective in the comment, a genre "
                        "chip, full routing in lyrics) to FLACs under DIR from their "
                        ".config.json sidecars, then exit — no re-mixing")
    p.add_argument("--video", action="store_true",
                   help="output video (MKV with 7.1 FLAC, picture copied): for URL "
                        "inputs, download the video; local video files are detected "
                        "automatically")
    p.add_argument("--out", type=Path, default=Path.cwd() / "spatial-audio",
                   help="output library root (default: ./spatial-audio)")
    p.add_argument("--no-recursive", action="store_true",
                   help="for folder inputs, only the top level, not sub-folders")
    p.add_argument("--artist", help="override artist tag (single input only)")
    p.add_argument("--title", help="override title tag (single input only)")
    p.add_argument("--track-number", type=int, help="TRACKNUMBER tag (single input only)")
    p.add_argument("--date", help="DATE tag, e.g. 2007 (single input only)")
    p.add_argument("--ffmpeg-bin", default="ffmpeg")
    p.add_argument("--demucs-bin", default="demucs")
    p.add_argument("--separator-bin", default="audio-separator",
                   help="audio-separator command (Front Row crowd pass)")
    p.add_argument("--ytdlp-bin", default="yt-dlp", help="yt-dlp command (URL inputs)")
    p.add_argument("--update-ytdlp", action="store_true",
                   help="update the yt-dlp this app installed (pip, in its own "
                        "environment) and exit — the fix when YouTube URLs stop "
                        "working. This also happens automatically before downloads.")
    p.add_argument("--no-ytdlp-update", action="store_true",
                   help="never update yt-dlp automatically (same as "
                        "SPATIAL_STANDARDS_NO_YTDLP_UPDATE=1)")
    p.add_argument("--keep-work", action="store_true",
                   help="keep the temporary work directory (stems, intermediate mixes)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    load_env()  # pick up ANTHROPIC_API_KEY etc. from .env if present
    args = build_parser().parse_args(argv)
    say = _Printer()
    ensure_ffmpeg_on_path(args.ffmpeg_bin, progress=say)  # fall back to static-ffmpeg if needed
    if args.no_ytdlp_update:
        os.environ["SPATIAL_STANDARDS_NO_YTDLP_UPDATE"] = "1"

    if args.update_ytdlp:
        ytdlp = resolve_bin(args.ytdlp_bin)
        if ytdlp_update.managed(ytdlp):
            ytdlp_update.upgrade(ytdlp, say)
            return 0
        v = ytdlp_update.installed_version(ytdlp) or "not found"
        print(f"yt-dlp ({v}) at {ytdlp} was not installed by this app, so it is left "
              f"alone. To update it: {ytdlp_update.manual_hint(ytdlp)}")
        return 1

    if args.retag is not None:
        done, skipped = retag_tree(args.retag, progress=say, ffmpeg=args.ffmpeg_bin)
        say.done()
        print(f"retagged {done}, skipped {skipped}")
        return 0

    try:
        args.inputs = expand_inputs(args.inputs, recursive=not args.no_recursive,
                                    ytdlp=resolve_bin(args.ytdlp_bin), progress=say)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if len(args.inputs) > 1 and (args.artist or args.title or args.track_number or args.date):
        print("error: --artist/--title/--track-number/--date apply to a single input only",
              file=sys.stderr)
        return 2

    if args.comments is not None and args.standard != "natural" and not args.optimized:
        print("error: for legacy onstage/frontrow, --comments only affects the mix "
              "with --optimized", file=sys.stderr)
        return 2

    print(f"spatial-standards v{__version__}")
    print(RIGHTS_NOTICE)
    print()

    # resolve_bin: a bare default (e.g. "demucs") resolves from PATH or the
    # venv bin; an explicit --*-bin path is returned unchanged.
    bins = Bins(ffmpeg=resolve_bin(args.ffmpeg_bin), demucs=resolve_bin(args.demucs_bin),
                separator=resolve_bin(args.separator_bin), ytdlp=resolve_bin(args.ytdlp_bin))
    meta = TrackMeta(artist=args.artist, title=args.title,
                     track_number=args.track_number, date=args.date)

    # Legacy onstage/frontrow read the trims here; Natural Perspective reads the
    # whole comments file itself (text for the model + trims for leveling).
    profile = None
    if args.comments is not None and args.standard != "natural":
        try:
            profile = parse_system_profile(args.comments)
        except OSError as e:
            print(f"error: cannot read {args.comments}: {e}", file=sys.stderr)
            return 2

    # Natural Perspective: --comments may be a file path or inline notes.
    nat_comments = nat_comments_text = None
    if args.standard == "natural" and args.comments is not None:
        if args.comments.is_file():
            nat_comments = args.comments
        else:
            nat_comments_text = str(args.comments)

    # Natural Perspective from a pre-separated stems folder (one render).
    if args.stems_dir is not None:
        print(f"Processing stems: {args.stems_dir}")
        try:
            dest, sidecar = process_natural(
                stems_dir=args.stems_dir, out_dir=args.out, meta=meta, bins=bins,
                comments=nat_comments, comments_text=nat_comments_text, cover_art=args.cover,
                model=args.model, force_default=args.default_config,
                web_search=not args.no_research,
                keep_work=args.keep_work, progress=say)
            say(f"-> {dest}")
            say(f"docs -> {sidecar.parent / 'index.html'}")
            say.done()
            return 0
        except SkippedInput as e:
            say(f"skipped: {e}")
            say.done()
            return 0
        except Exception as e:
            say.done()
            print(f"  FAILED: {e}", file=sys.stderr)
            return 1

    if not args.inputs:
        print("error: no inputs — give audio/video files, folders, URLs, or --stems-dir",
              file=sys.stderr)
        return 2

    failures = skipped = 0
    for source in args.inputs:
        say.done()
        print(f"Processing: {source}")
        try:
            if args.standard == "natural":
                dest, sidecar = process_natural(
                    source, out_dir=args.out, meta=meta, bins=bins,
                    comments=nat_comments, comments_text=nat_comments_text,
                    cover_art=args.cover, want_video=args.video,
                    model=args.model, force_default=args.default_config,
                    web_search=not args.no_research,
                    keep_work=args.keep_work,
                    progress=say)
                say(f"-> {dest}")
                say(f"docs -> {sidecar.parent / 'index.html'}")
            else:
                dest = process(source, standard=args.standard, optimized=args.optimized,
                               out_dir=args.out, meta=meta, bins=bins,
                               system_profile=profile,
                               keep_work=args.keep_work,
                               progress=say)
                say(f"-> {dest}")
        except SkippedInput as e:
            skipped += 1
            say(f"skipped: {e}")
        except proc.Cancelled:
            say.done()
            print("Cancelled.")
            return 130
        except Exception as e:  # keep batch going; report at the end
            failures += 1
            say.done()
            print(f"  FAILED: {e}", file=sys.stderr)
    say.done()

    if skipped:
        print(f"\n{skipped} input(s) skipped (no readable audio).")
    if failures:
        print(f"{failures} of {len(args.inputs)} input(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

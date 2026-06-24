#!/usr/bin/env bash
# One-command setup. Finds (or, on a Mac with Homebrew, installs) Python 3.10+,
# creates a private virtualenv, and installs everything in the [full] extra.
# Re-runnable. Usage:  ./install.sh
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

find_python() {
  for c in python3.12 python3.13 python3.11 python3.10 python3 python; do
    command -v "$c" >/dev/null 2>&1 || continue
    local v major minor
    v=$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo 0.0)
    major=${v%%.*}; minor=${v#*.}
    if [ "${major:-0}" -eq 3 ] && [ "${minor:-0}" -ge 10 ]; then echo "$c"; return 0; fi
  done
  return 1
}

PY="$(find_python || true)"

# Nothing suitable? On a Mac with Homebrew, install it; otherwise guide the user.
if [ -z "$PY" ]; then
  if command -v brew >/dev/null 2>&1; then
    say "No Python 3.10+ found — installing python@3.12 via Homebrew…"
    brew install python@3.12 python-tk@3.12
    hash -r
    PY="$(find_python || true)"
  fi
fi
if [ -z "$PY" ]; then
  say "Need Python 3.10+ (3.12 recommended). Install one, then re-run ./install.sh:"
  echo "  macOS:        install Homebrew (brew.sh), or grab Python 3.12 from python.org"
  echo "  Debian/Ubuntu: sudo apt install python3.12 python3.12-venv python3-tk"
  exit 1
fi
say "Using $("$PY" --version) ($(command -v "$PY"))"

# FFmpeg (+ffprobe) is required and is NOT a Python package. Install via brew if
# it's missing; on other systems, guide the user.
if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    say "Installing FFmpeg via Homebrew…"
    brew install ffmpeg
    hash -r
  else
    say "FFmpeg not found — install it, then re-run ./install.sh:"
    echo "  macOS: brew install ffmpeg   |   Debian/Ubuntu: sudo apt install ffmpeg"
    exit 1
  fi
fi

say "Creating virtualenv (.venv) and installing — this pulls PyTorch, so it's a few GB…"
"$PY" -m venv .venv
./.venv/bin/python -m pip install --quiet --upgrade pip
# Editable: a later `git pull` picks up code changes with no reinstall.
./.venv/bin/python -m pip install -e '.[full]'

# Verify every tool actually resolves (the dependency check), using the app's
# own resolver so it matches runtime.
say "Verifying tools…"
if ! ./.venv/bin/python - <<'PY'
import sys
from spatial_standards.pipeline import resolve_bin, ensure_ffmpeg_on_path
ensure_ffmpeg_on_path()
missing = []
for n in ("ffmpeg", "demucs", "audio-separator", "yt-dlp"):
    p = resolve_bin(n)
    ok = p != n
    print(f"  {'ok  ' if ok else 'MISS'}  {n}{('  ' + p) if ok else ''}")
    if not ok:
        missing.append(n)
try:
    import tkinter  # noqa: F401
    print("  ok    tkinter (desktop GUI)")
except Exception:
    print("  --    tkinter unavailable — the CLI works, the GUI won't")
sys.exit(1 if missing else 0)
PY
then
  say "A required tool failed to install. Re-run ./install.sh; if it persists, paste the output above."
  exit 1
fi
if ! ./.venv/bin/python -c 'import tkinter' 2>/dev/null; then
  echo "  For the GUI, add Tkinter: macOS 'brew install python-tk@3.12', Ubuntu 'sudo apt install python3-tk', then re-run."
fi

say "Done. Run it:"
echo "  ./gui                                  # the desktop GUI"
echo "  .venv/bin/spatial-standards song.flac  # the CLI (file, folder, or URL)"
echo
echo "Optional, for Natural Perspective (the model layer):"
echo "  export ANTHROPIC_API_KEY=sk-...        # without it, a built-in mix runs offline"

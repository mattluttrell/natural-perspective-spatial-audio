#!/usr/bin/env bash
# One-command setup. Finds (or, on a Mac with Homebrew, installs) Python 3.10+,
# creates a private virtualenv, and installs everything in the [full] extra.
# Re-runnable. Usage:  ./install.sh
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

find_python() {
  for c in python3.12 python3.11 python3.13 python3.10 python3 python; do
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
  echo "  macOS:        install Homebrew (brew.sh), or grab Python from python.org"
  echo "  Debian/Ubuntu: sudo apt install python3.12 python3.12-venv python3-tk"
  exit 1
fi
say "Using $("$PY" --version) ($(command -v "$PY"))"

say "Creating virtualenv (.venv) and installing — this pulls PyTorch, so it's a few GB…"
"$PY" -m venv .venv
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install '.[full]'

# The GUI needs Tkinter; warn (don't fail) if this Python lacks it.
if ! ./.venv/bin/python -c 'import tkinter' 2>/dev/null; then
  say "Note: this Python has no Tkinter, so only the CLI will run."
  echo "  macOS+Homebrew: brew install python-tk@3.12   (then re-run ./install.sh)"
  echo "  Debian/Ubuntu:  sudo apt install python3-tk"
fi

say "Done. Run it:"
echo "  ./gui                                  # the desktop GUI"
echo "  .venv/bin/spatial-standards song.flac  # the CLI (file, folder, or URL)"
echo
echo "Optional, for Natural Perspective (the model layer):"
echo "  export ANTHROPIC_API_KEY=sk-...        # without it, a built-in mix runs offline"

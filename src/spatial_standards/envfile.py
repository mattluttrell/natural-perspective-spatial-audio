"""Tiny .env loader (no dependency).

Sets environment variables from a `.env` file so a key dropped in `.env` is
picked up by the CLI and GUI without the user having to `export`/source it.
Looked up in the current directory, the project root (for a source/editable
install, so it works no matter where you launch from), and the user config dir.
Never overrides a variable already present in the environment (an explicit
export wins).
"""
from __future__ import annotations

import os
from pathlib import Path

_CANDIDATES = (
    Path.cwd() / ".env",
    # Repo root of a source/editable checkout: src/spatial_standards/envfile.py
    # -> up 3 to the checkout root. Harmless (just absent) for a wheel install.
    Path(__file__).resolve().parents[2] / ".env",
    Path.home() / ".config" / "spatial-standards" / ".env",
)


def load_env() -> None:
    for path in _CANDIDATES:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

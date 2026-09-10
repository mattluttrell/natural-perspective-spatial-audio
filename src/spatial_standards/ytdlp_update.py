"""Keep yt-dlp current — it is the one dependency that rots.

YouTube changes its player every few weeks and each change breaks the yt-dlp
release before it: downloads start failing with "Sign in to confirm you're not
a bot", "nsig extraction failed", HTTP 403, or "Requested format is not
available". The fix is always the same — update yt-dlp — but the copy that
``pip install '…[full]'`` (or pipx) put in the app's environment cannot update
itself: ``yt-dlp -U`` answers "You installed yt-dlp with pip … use that to
update", and nothing in a normal user's day runs pip inside that venv. So a
user who installed in June has a tool that quietly stops accepting URLs in
August, with no button to press.

This module is that button. When the yt-dlp we resolved lives in *our own*
Python environment (a venv or a pipx venv — the ``[full]`` install), it is ours
to manage, and we upgrade it with this interpreter's pip:

- proactively, before a download, at most once a day, if PyPI has a newer
  release;
- reactively, when a download fails: check again, upgrade, retry once.

A yt-dlp that came from somewhere else (Homebrew, apt, the user's own venv) is
left alone and the error message says how to update it instead. Set
``SPATIAL_STANDARDS_NO_YTDLP_UPDATE=1`` to turn all of this off.

The upgrade spec is ``yt-dlp[default,deno]``: ``default`` brings the JS
challenge scripts YouTube now requires, ``deno`` a JavaScript runtime as a pip
wheel (yt-dlp looks for it beside the interpreter). Without both, a current
yt-dlp still fails on YouTube."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import sysconfig
import time
import urllib.request
from datetime import date
from pathlib import Path

from . import proc

PYPI_JSON = "https://pypi.org/pypi/yt-dlp/json"
SPEC = "yt-dlp[default,deno]"
FALLBACK_SPEC = "yt-dlp[default]"  # if no deno wheel exists for this platform
STAMP = Path.home() / ".config" / "spatial-standards" / "ytdlp-checked"
CHECK_EVERY_S = 24 * 3600
STALE_AFTER_DAYS = 21  # older than this, a YouTube failure is blamed on age

_OUTDATED_SIGNS = ("yt-dlp -U", "latest version", "Sign in to confirm", "nsig",
                   "n challenge", "JS challenge", "JavaScript runtime", "player",
                   "HTTP Error 403", "Requested format is not available",
                   "Unable to extract", "Precondition check failed")


def disabled() -> bool:
    return bool(os.environ.get("SPATIAL_STANDARDS_NO_YTDLP_UPDATE"))


def installed_version(ytdlp_bin: str) -> str | None:
    """'2026.08.19' or None if yt-dlp can't be run."""
    try:
        out = subprocess.run([ytdlp_bin, "--version"], capture_output=True, text=True,
                             timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.splitlines()[-1].strip() if out else None


def version_key(v: str) -> tuple[int, ...]:
    """Comparable form: '2026.08.19' and PyPI's '2026.8.19' are equal."""
    return tuple(int(x) for x in re.findall(r"\d+", v))


def version_date(v: str | None) -> date | None:
    """yt-dlp versions are release dates — recover the date."""
    if not v:
        return None
    k = version_key(v)
    try:
        return date(k[0], k[1], k[2]) if len(k) >= 3 else None
    except ValueError:
        return None


def is_stale(v: str | None, days: int = STALE_AFTER_DAYS) -> bool:
    d = version_date(v)
    return d is not None and (date.today() - d).days > days


def latest_version(timeout: float = 6) -> str | None:
    """Newest release on PyPI, or None if offline."""
    try:
        with urllib.request.urlopen(PYPI_JSON, timeout=timeout) as r:
            return json.load(r)["info"]["version"]
    except Exception:
        return None


def managed(ytdlp_bin: str) -> bool:
    """True when this yt-dlp was installed into *our* Python environment (the
    venv / pipx venv that `[full]` populated) and pip is importable there —
    the only case where upgrading it is both possible and touches nothing
    outside the app."""
    try:
        where = Path(ytdlp_bin).resolve().parent
    except OSError:
        return False
    if not Path(ytdlp_bin).is_absolute() and not Path(ytdlp_bin).exists():
        return False
    ours = {Path(sysconfig.get_path("scripts")).resolve(),
            Path(sys.executable).resolve().parent}
    return where in ours and importlib.util.find_spec("pip") is not None


def manual_hint(ytdlp_bin: str = "yt-dlp") -> str:
    """The command a person should run by hand to update *this* yt-dlp."""
    if "pipx" in Path(sys.prefix).parts:
        return f'pipx runpip natural-perspective-spatial-audio install -U "{SPEC}"'
    if managed(ytdlp_bin) or sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return f'{sys.executable} -m pip install -U "{SPEC}"'
    return ('update yt-dlp the way you installed it — e.g. "yt-dlp -U" for the '
            f'standalone binary, pip install -U "{SPEC}", or brew upgrade yt-dlp')


def looks_outdated(error_text: str, version: str | None) -> bool:
    """Should a yt-dlp failure be blamed on an old yt-dlp? Yes if the message
    carries one of the usual site-change signatures, or the release is old."""
    return any(s.lower() in error_text.lower() for s in _OUTDATED_SIGNS) or is_stale(version)


def stale_hint(ytdlp_bin: str) -> str:
    v = installed_version(ytdlp_bin) or "unknown version"
    return (f"yt-dlp is {v}. YouTube changes often and an out-of-date yt-dlp is the "
            f"usual cause of URL failures — update it:  {manual_hint(ytdlp_bin)}")


def _pip_upgrade(spec: str) -> proc.Result:
    return proc.run([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
                     "--disable-pip-version-check", spec])


def upgrade(ytdlp_bin: str, step) -> bool:
    """Upgrade yt-dlp inside this environment with pip. Returns True if the
    installed version changed. Logs through `step`; never raises for a pip
    failure (the caller carries on with the yt-dlp it has)."""
    before = installed_version(ytdlp_bin)
    step(f"updating yt-dlp ({before or 'unknown version'})…")
    res = _pip_upgrade(SPEC)
    if res.returncode != 0:
        res = _pip_upgrade(FALLBACK_SPEC)
    if res.returncode != 0:
        step(f"  yt-dlp update failed:\n{res.stderr.strip()[-800:]}")
        return False
    after = installed_version(ytdlp_bin)
    if after != before:
        step(f"  yt-dlp {before} → {after}")
    else:
        step(f"  yt-dlp is already current ({after})")
    _touch_stamp()
    return after != before


def _stamp_recent() -> bool:
    try:
        return time.time() - STAMP.stat().st_mtime < CHECK_EVERY_S
    except OSError:
        return False


def _touch_stamp() -> None:
    try:
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"))
    except OSError:
        pass


def ensure_current(ytdlp_bin: str, step, force: bool = False) -> bool:
    """If PyPI has a newer yt-dlp than the one we manage, install it. At most
    one PyPI lookup a day unless `force`. Never raises; True if upgraded."""
    if disabled() or not managed(ytdlp_bin):
        return False
    if not force and _stamp_recent():
        return False
    current, latest = installed_version(ytdlp_bin), latest_version()
    _touch_stamp()
    if not current or not latest or version_key(latest) <= version_key(current):
        return False
    step(f"a newer yt-dlp is available ({current} → {latest})")
    return upgrade(ytdlp_bin, step)


def retry_after_update(fn, ytdlp_bin: str, step):
    """Run `fn()` (a yt-dlp call). If it fails: when a newer yt-dlp is ours to
    install, install it and try once more; otherwise re-raise with a hint on
    how to update, when the failure looks like an out-of-date yt-dlp."""
    try:
        return fn()
    except RuntimeError as e:
        if proc.cancelled():
            raise
        if ensure_current(ytdlp_bin, step, force=True):
            step("  retrying with the updated yt-dlp…")
            return fn()
        version = installed_version(ytdlp_bin)
        if looks_outdated(str(e), version):
            raise RuntimeError(f"{e}\n{stale_hint(ytdlp_bin)}") from e
        raise

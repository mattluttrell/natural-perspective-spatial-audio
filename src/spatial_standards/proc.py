"""Run an external tool while streaming its progress, and let it be cancelled.

The three long steps — yt-dlp, Demucs, audio-separator — each run for minutes
and each prints a progress bar: tqdm bars on stderr (``45%|████ | 12/27
[00:03<00:04, 3.2it/s]``) for the two separators, ``[download]  45.2% of
3.5MiB at 1.2MiB/s ETA 00:02`` lines for yt-dlp with ``--progress --newline``.
``capture_output=True`` swallowed all of it, so the GUI showed one line and
then nothing for minutes. ``run`` reads both streams as they arrive, turns
bar updates into throttled ``progress(pct, eta)`` calls, and hands back the
non-progress output the callers already parsed.

Cancellation: every live child is registered; ``cancel()`` terminates them all
and makes ``run`` raise ``Cancelled`` instead of a tool error, so a batch can
be stopped from the GUI (and the window can close without orphaning a Demucs
that keeps the CPU busy for ten more minutes)."""
from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass

# tqdm:   " 45%|████      | 12/27 [00:03<00:04, 3.2it/s]"
_TQDM = re.compile(r"^\s*(\d{1,3})%\|.*?\[\d+:\d+(?::\d+)?<(\S+?),")
# yt-dlp: "[download]  45.2% of  3.50MiB at  1.20MiB/s ETA 00:02"
_YTDLP = re.compile(r"^\[download\]\s+(\d{1,3}(?:\.\d+)?)%.*?(?:ETA\s+(\S+))?\s*$")
_YTDLP_ANY = re.compile(r"^\[download\]")


class Cancelled(Exception):
    """The child was terminated by ``cancel()`` — not a failure of the tool."""


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str  # progress bars removed; the tail is what an error message needs


_live: set[subprocess.Popen] = set()
_lock = threading.Lock()
_cancel = threading.Event()


def cancel() -> None:
    """Stop every running child and make in-flight ``run`` calls raise
    ``Cancelled``. Stays in effect until ``reset()``."""
    _cancel.set()
    with _lock:
        procs = list(_live)
    for p in procs:
        try:
            p.terminate()
        except OSError:
            pass


def cancelled() -> bool:
    return _cancel.is_set()


def reset() -> None:
    """Clear a previous ``cancel()`` before starting new work."""
    _cancel.clear()


def check() -> None:
    """Raise ``Cancelled`` if a cancel is pending — for the pipeline to call
    between steps that don't go through ``run`` (the model call, mixing)."""
    if _cancel.is_set():
        raise Cancelled()


_PROGRESS_MSG = re.compile(r" \d{1,3}%( · about \S+ left)?$")


def progress_message(label: str, pct: float, eta: str | None) -> str:
    """The one format for a progress line, e.g.
    'separating instruments (Demucs)… 45% · about 1:02 left'."""
    show_eta = eta and pct < 100 and eta.strip("0:") != ""
    return f"{label} {pct:.0f}%" + (f" · about {eta} left" if show_eta else "")


def split_progress_message(msg: str) -> str | None:
    """The label of a progress line (so a log can overwrite the previous
    update for the same step), or None for an ordinary message."""
    m = _PROGRESS_MSG.search(msg)
    return msg[:m.start()] if m else None


def replaces(previous: str | None, msg: str) -> bool:
    """Should `msg` overwrite `previous` in a log? Yes when it is a progress
    update for the step `previous` announced — either the bare label
    ('separating…') or an earlier update of the same step ('separating… 20%')."""
    if previous is None:
        return False
    label = split_progress_message(msg)
    return label is not None and (previous == label or split_progress_message(previous) == label)


def parse_progress(line: str) -> tuple[float, str | None] | None:
    """(percent, eta-or-None) if `line` is a progress-bar update, else None."""
    m = _TQDM.match(line)
    if m:
        eta = m.group(2)
        return float(m.group(1)), (None if eta == "?" else eta)
    m = _YTDLP.match(line)
    if m:
        eta = m.group(2)
        return float(m.group(1)), (None if eta in (None, "Unknown") else eta)
    return None


def _is_progress_noise(line: str) -> bool:
    """Lines to drop from captured output: bar updates and yt-dlp's other
    ``[download]`` chatter (destination, "100% of …" totals)."""
    return parse_progress(line) is not None or bool(_YTDLP_ANY.match(line))


class _Throttle:
    """Forward progress at most every few seconds or every few percent —
    enough to show life on a slow CPU without flooding the log on a GPU."""

    def __init__(self, cb, min_pct: float = 5.0, min_secs: float = 4.0):
        self.cb, self.min_pct, self.min_secs = cb, min_pct, min_secs
        self.last_pct = -1e9
        self.last_t = 0.0
        self.lock = threading.Lock()

    def __call__(self, pct: float, eta: str | None) -> None:
        now = time.monotonic()
        with self.lock:
            # A new bar (e.g. a model download, then the separation) restarts
            # from 0 — always show that.
            fresh = pct < self.last_pct
            due = (pct - self.last_pct >= self.min_pct
                   or now - self.last_t >= self.min_secs or pct >= 100)
            if not (fresh or due) or pct == self.last_pct:
                return
            self.last_pct, self.last_t = pct, now
        try:
            self.cb(pct, eta)
        except Exception:
            pass


def _reader(stream, out_lines: list[str], on_progress) -> None:
    """Split a stream on both \\n and \\r (tqdm redraws with \\r), route bar
    updates to `on_progress`, keep everything else."""
    buf = b""
    while True:
        chunk = stream.read1(8192) if hasattr(stream, "read1") else stream.read(8192)
        if not chunk:
            break
        buf += chunk
        while True:
            i = min((j for j in (buf.find(b"\n"), buf.find(b"\r")) if j >= 0), default=-1)
            if i < 0:
                break
            raw, buf = buf[:i], buf[i + 1:]
            _handle(raw, out_lines, on_progress)
    if buf.strip():
        _handle(buf, out_lines, on_progress)


def _handle(raw: bytes, out_lines: list[str], on_progress) -> None:
    line = raw.decode("utf-8", "replace").rstrip()
    if not line:
        return
    prog = parse_progress(line)
    if prog is not None:
        if on_progress is not None:
            on_progress(*prog)
        return
    if _is_progress_noise(line):
        return
    out_lines.append(line)
    if len(out_lines) > 400:  # keep memory bounded on chatty tools
        del out_lines[:100]


def run(cmd: list[str], progress=None) -> Result:
    """Run `cmd` to completion. `progress(pct, eta)` is called (throttled) for
    each bar update the tool prints. Raises ``Cancelled`` if ``cancel()`` was
    called; otherwise returns a Result whose stdout/stderr exclude the bars."""
    if _cancel.is_set():
        raise Cancelled()
    on_progress = _Throttle(progress) if progress is not None else None
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with _lock:
        _live.add(proc)
    out: list[str] = []
    err: list[str] = []
    try:
        threads = [threading.Thread(target=_reader, args=(proc.stdout, out, on_progress), daemon=True),
                   threading.Thread(target=_reader, args=(proc.stderr, err, on_progress), daemon=True)]
        for t in threads:
            t.start()
        proc.wait()
        for t in threads:
            t.join()
    finally:
        with _lock:
            _live.discard(proc)
    if _cancel.is_set():
        raise Cancelled()
    return Result(proc.returncode, "\n".join(out), "\n".join(err))

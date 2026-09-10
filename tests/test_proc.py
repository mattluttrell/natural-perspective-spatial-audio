"""proc: progress-bar parsing and the message format the UIs overwrite."""
import subprocess
import sys

import pytest

from spatial_standards import proc


@pytest.mark.parametrize("line, pct, eta", [
    (" 45%|████▌     | 29.25/64.35 [00:01<00:01, 26.54seconds/s]", 45.0, "00:01"),
    ("  0%|          | 0/8 [00:00<?, ?it/s]", 0.0, None),
    ("100%|██████████| 8/8 [00:02<00:00,  3.40it/s]", 100.0, "00:00"),
    ("[download]  45.2% of  3.50MiB at  1.20MiB/s ETA 00:02", 45.2, "00:02"),
    ("[download]   0.4% of  246.27KiB at  Unknown B/s ETA Unknown", 0.4, None),
    ("[download] 100% of  246.27KiB in 00:00:00 at 2.76MiB/s", 100.0, None),
])
def test_parse_progress(line, pct, eta):
    assert proc.parse_progress(line) == (pct, eta)


@pytest.mark.parametrize("line", [
    "Separating track clip.wav",
    "2026-09-10 16:46:55 - INFO - separator - Loading model x.ckpt...",
    "Me at the zoo",
    "/tmp/x/jNQXAC9IVRw.wav",
    "ERROR: [youtube] abc: Sign in to confirm you're not a bot",
])
def test_non_progress_lines_pass_through(line):
    assert proc.parse_progress(line) is None


def test_progress_message_round_trip():
    msg = proc.progress_message("separating instruments (Demucs)…", 45, "1:02")
    assert msg == "separating instruments (Demucs)… 45% · about 1:02 left"
    assert proc.split_progress_message(msg) == "separating instruments (Demucs)…"
    assert proc.split_progress_message(proc.progress_message("downloading audio…", 100, None)) \
        == "downloading audio…"
    assert proc.split_progress_message("  scene: Front Stage") is None
    assert proc.split_progress_message("-> /music/Artist/Track [x].flac") is None


def test_run_streams_bars_and_keeps_other_output():
    script = (
        "import sys,time\n"
        "sys.stdout.write('Me at the zoo\\n'); sys.stdout.flush()\n"
        "for p in (0, 25, 50, 75, 100):\n"
        "    sys.stderr.write(f'\\r{p:3d}%|###| {p}/100 [00:01<00:02, 1it/s]'); sys.stderr.flush()\n"
        "sys.stderr.write('\\nSeparating track done\\n')\n"
        "sys.stdout.write('[download] 100% of 1MiB in 00:00:01\\n/tmp/out.wav\\n')\n"
    )
    seen = []
    res = proc.run([sys.executable, "-c", script], progress=lambda p, e: seen.append(p))
    assert res.returncode == 0
    assert res.stdout.splitlines() == ["Me at the zoo", "/tmp/out.wav"]
    assert res.stderr.splitlines() == ["Separating track done"]
    # stderr bars arrive in order; the stdout "[download] 100%" line is also a
    # progress update but races the stderr reader, so don't pin its position.
    assert [p for p in seen if p != 100] == [0, 25, 50, 75]
    assert 100 in seen


def test_cancel_kills_child_and_raises():
    proc.reset()
    import threading
    threading.Timer(0.3, proc.cancel).start()
    with pytest.raises(proc.Cancelled):
        proc.run([sys.executable, "-c", "import time; time.sleep(30)"])
    assert proc.cancelled()
    with pytest.raises(proc.Cancelled):
        proc.check()
    proc.reset()
    assert not proc.cancelled()
    assert proc.run([sys.executable, "-c", "pass"]).returncode == 0


def test_replaces_only_updates_of_the_same_step():
    label = "separating instruments (Demucs)…"
    p20 = proc.progress_message(label, 20, "1:00")
    p40 = proc.progress_message(label, 40, "0:30")
    assert proc.replaces(label, p20)          # bare label → first update
    assert proc.replaces(p20, p40)            # update → later update
    assert not proc.replaces(None, p20)
    assert not proc.replaces("measuring stem levels…", p20)   # a different step
    assert not proc.replaces(p40, "mixing 7.1 stage…")       # ordinary message never overwrites
    assert not proc.replaces(proc.progress_message("downloading audio…", 100, None), p20)


def test_progress_message_hides_meaningless_eta():
    assert proc.progress_message("x…", 100, "00:00") == "x… 100%"
    assert proc.progress_message("x…", 62, "00:00") == "x… 62%"
    assert proc.progress_message("x…", 62, "00:03") == "x… 62% · about 00:03 left"

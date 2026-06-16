"""Source separation via external commands.

Demucs (htdemucs_6s) produces the six instrument stems; the MelBand-RoFormer
crowd checkpoint (via audio-separator) optionally splits audience noise out of
the vocal stem for the Front Row standard. Model weights are downloaded by
those tools from their original hosts on first run — never bundled here.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

STEM_NAMES = ("vocals", "guitar", "piano", "bass", "drums", "other")
DEMUCS_MODEL = "htdemucs_6s"
CROWD_MODEL = "mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt"


def separate_stems(audio: Path, work_dir: Path, demucs_bin: str = "demucs") -> dict[str, Path]:
    """Run Demucs once; return {stem name: wav path}."""
    out_dir = work_dir / "stems"
    proc = subprocess.run(
        [demucs_bin, "-n", DEMUCS_MODEL, "-o", str(out_dir), str(audio)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"demucs failed:\n{proc.stderr.strip()[-2000:]}")

    stem_dir = out_dir / DEMUCS_MODEL / audio.stem
    stems = {name: stem_dir / f"{name}.wav" for name in STEM_NAMES}
    missing = [n for n, p in stems.items() if not p.exists()]
    if missing:
        raise RuntimeError(f"demucs finished but stems are missing: {missing} in {stem_dir}")
    return stems


def crowd_pass(audio: Path, work_dir: Path, separator_bin: str = "audio-separator") -> tuple[Path, Path]:
    """Split audio into (clean performance, crowd) with the crowd model.

    Run this on the FULL mix before Demucs (crowd-first): the model was
    trained on full live recordings, and separating first means applause
    never reaches the stems."""
    tmp = Path(tempfile.mkdtemp(dir=work_dir))
    proc = subprocess.run(
        [separator_bin, str(audio),
         "--model_filename", CROWD_MODEL,
         "--output_format", "flac",
         "--output_dir", str(tmp)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"audio-separator failed:\n{proc.stderr.strip()[-2000:]}")

    # Output names embed the stem marker as "(crowd)" / "(other)"; the model
    # name itself contains "crowd", so match only the parenthesized marker.
    crowd_file = clean_file = None
    for f in tmp.glob("*.flac"):
        if "(crowd)" in f.name.lower():
            crowd_file = f
        else:
            clean_file = f
    if not crowd_file or not clean_file:
        raise RuntimeError(f"unexpected crowd-model output in {tmp}: {[f.name for f in tmp.iterdir()]}")
    return clean_file, crowd_file

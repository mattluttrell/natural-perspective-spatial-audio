"""The spatial mixes: ffmpeg filtergraphs placing each stem at its stage
position in an 8-channel (7.1) 24-bit FLAC.

Ported from the proven reference scripts (make_on_stage.sh /
make_front_row.sh). Every output bus carries a peak limiter. amerge order places back-pair content at positions 5-6 and side-pair at 7-8 to match the FLAC 7.1 channel mask (FL FR FC LFE BL BR SL SR) and
aformat=channel_layouts=mono — the latter is mandatory or amerge fails
format negotiation.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

LPF = 120          # LFE low-pass (Hz)
DRUM_GAIN = 2.0    # drums boosted + limited before placement
DRUM_FRONT = 0.7   # Front Row: drum weight in front L/R (strong)
DRUM_SIDE = 1.0    # Front Row: drum weight in surround L/R (maximum)
OTHER_REAR = 1.0   # Front Row: "other" weight in both rear backs
CROWD_SUR = 0.7    # Front Row: crowd weight in surround L/R
CROWD_REAR = 1.0   # Front Row: crowd weight in rear back L/R

_BUS = ",alimiter=limit=0.95,aformat=channel_layouts=mono"


def _run_ffmpeg(inputs: list[Path], graph: str, out_file: Path, ffmpeg_bin: str) -> None:
    cmd = [ffmpeg_bin, "-nostdin", "-y", "-loglevel", "error"]
    for i in inputs:
        cmd += ["-i", str(i)]
    cmd += ["-filter_complex", graph, "-c:a", "flac", "-sample_fmt", "s32", str(out_file)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mix failed:\n{proc.stderr.strip()[-2000:]}")


def mix_on_stage(stems: dict[str, Path], out_file: Path, ffmpeg_bin: str = "ffmpeg") -> Path:
    """On Stage Spatial Standard: every instrument at one primary direction;
    bass deliberately spread; drums focused behind the listener.

      0 FL  Guitar L (100%) + Bass (80%)
      1 FR  Guitar R (100%) + Piano (80%)
      2 C   Vocals (100%)
      3 LFE Bass low-pass (100%) + Drums kick low-pass (60%)
      4 SL  Bass (100%) + Other (100%)
      5 SR  Piano (100%) + Bass (60%)
      6 RBL Drums L (100%) + Other (50%)
      7 RBR Drums R (100%) + Bass (40%)
    """
    inputs = [stems[n] for n in ("guitar", "vocals", "bass", "drums", "piano", "other")]
    graph = f"""
      [0]channelsplit=channel_layout=stereo[gL][gR];
      [1]pan=mono|c0=0.5*c0+0.5*c1[vox];
      [2]pan=mono|c0=0.5*c0+0.5*c1[bas_mono];
      [bas_mono]asplit=5[bas_fl][bas_sl][bas_sr][bas_rbr][bas_lfe_in];
      [bas_lfe_in]lowpass=f={LPF}[bas_lfe];
      [3]volume={DRUM_GAIN},alimiter=limit=0.95:level=disabled,asplit=2[drm_a][drm_b];
      [drm_a]channelsplit=channel_layout=stereo[dL][dR];
      [drm_b]pan=mono|c0=0.5*c0+0.5*c1,lowpass=f={LPF}[drm_lfe];
      [4]pan=mono|c0=0.5*c0+0.5*c1[pno_mono];
      [pno_mono]asplit=2[pno_fr][pno_sr];
      [5]pan=mono|c0=0.5*c0+0.5*c1[oth_mono];
      [oth_mono]asplit=2[oth_sl][oth_rbl];
      [gL][bas_fl]amix=inputs=2:weights=1.0 0.8:normalize=0{_BUS}[ch0];
      [gR][pno_fr]amix=inputs=2:weights=1.0 0.8:normalize=0{_BUS}[ch1];
      [vox]aformat=channel_layouts=mono[ch2];
      [bas_lfe][drm_lfe]amix=inputs=2:weights=1.0 0.6:normalize=0{_BUS}[ch3];
      [bas_sl][oth_sl]amix=inputs=2:weights=1.0 1.0:normalize=0{_BUS}[ch4];
      [pno_sr][bas_sr]amix=inputs=2:weights=1.0 0.6:normalize=0{_BUS}[ch5];
      [dL][oth_rbl]amix=inputs=2:weights=1.0 0.5:normalize=0{_BUS}[ch6];
      [dR][bas_rbr]amix=inputs=2:weights=1.0 0.4:normalize=0{_BUS}[ch7];
      [ch0][ch1][ch2][ch3][ch6][ch7][ch4][ch5]amerge=inputs=8
    """
    _run_ffmpeg(inputs, graph, out_file, ffmpeg_bin)
    return out_file


def mix_front_row(stems: dict[str, Path], crowds: list[Path],
                  out_file: Path, ffmpeg_bin: str = "ffmpeg") -> Path:
    """Front Row Spatial Standard: wide-drums stage mix with the
    front-feeding stems crowd-cleaned (pass the cleaned paths in `stems`);
    the summed crowd residues go ONLY to the rear four (SL/SR/RBL/RBR) —
    band ahead, crowd around, nothing up front.

    Unlike the wide-drums base, bass is NOT in the front: the crowd model
    cannot clean low-frequency applause rumble out of the bass stem, and
    bass is non-directional by design — it is felt via SL/SR/RBR/LFE only.
    Drums live in the front four only: maximum on the surrounds, strong on
    the fronts, none in the rear backs.

      0 FL  Guitar L (100%) + Drums L (70%)
      1 FR  Guitar R (100%) + Piano (80%) + Drums R (70%)
      2 C   Vocals (100%)
      3 LFE Bass low-pass (100%) + Drums kick low-pass (60%)
      4 SL  Bass (100%) + Other (100%) + Drums L (100%) + Crowd L (70%)
      5 SR  Piano (100%) + Bass (60%) + Drums R (100%) + Crowd R (70%)
      6 RBL Other (100%) + Crowd L (100%)
      7 RBR Bass (40%) + Other (100%) + Crowd R (100%)
    """
    inputs = [stems["guitar"], stems["vocals"], stems["bass"], stems["drums"],
              stems["piano"], stems["other"], *crowds]
    crowd_idx = "".join(f"[{6 + i}]" for i in range(len(crowds)))
    crowd_bus = (
        f"{crowd_idx}amix=inputs={len(crowds)}:normalize=0,alimiter=limit=0.95,"
        if len(crowds) > 1 else "[6]"
    )
    graph = f"""
      [0]channelsplit=channel_layout=stereo[gL][gR];
      [1]pan=mono|c0=0.5*c0+0.5*c1[vox];
      [2]pan=mono|c0=0.5*c0+0.5*c1[bas_mono];
      [bas_mono]asplit=4[bas_sl][bas_sr][bas_rbr][bas_lfe_in];
      [bas_lfe_in]lowpass=f={LPF}[bas_lfe];
      [3]volume={DRUM_GAIN},alimiter=limit=0.95:level=disabled,asplit=2[drm_a][drm_b];
      [drm_a]channelsplit=channel_layout=stereo[dL][dR];
      [dL]asplit=2[dL_fl][dL_sl];
      [dR]asplit=2[dR_fr][dR_sr];
      [drm_b]pan=mono|c0=0.5*c0+0.5*c1,lowpass=f={LPF}[drm_lfe];
      [4]pan=mono|c0=0.5*c0+0.5*c1[pno_mono];
      [pno_mono]asplit=2[pno_fr][pno_sr];
      [5]pan=mono|c0=0.5*c0+0.5*c1[oth_mono];
      [oth_mono]asplit=3[oth_sl][oth_rbl][oth_rbr];
      {crowd_bus}aformat=channel_layouts=stereo,channelsplit=channel_layout=stereo[cL][cR];
      [cL]asplit=2[cL_sl][cL_rbl];
      [cR]asplit=2[cR_sr][cR_rbr];
      [gL][dL_fl]amix=inputs=2:weights=1.0 {DRUM_FRONT}:normalize=0{_BUS}[ch0];
      [gR][pno_fr][dR_fr]amix=inputs=3:weights=1.0 0.8 {DRUM_FRONT}:normalize=0{_BUS}[ch1];
      [vox]aformat=channel_layouts=mono[ch2];
      [bas_lfe][drm_lfe]amix=inputs=2:weights=1.0 0.6:normalize=0{_BUS}[ch3];
      [bas_sl][oth_sl][dL_sl][cL_sl]amix=inputs=4:weights=1.0 1.0 {DRUM_SIDE} {CROWD_SUR}:normalize=0{_BUS}[ch4];
      [pno_sr][bas_sr][dR_sr][cR_sr]amix=inputs=4:weights=1.0 0.6 {DRUM_SIDE} {CROWD_SUR}:normalize=0{_BUS}[ch5];
      [oth_rbl][cL_rbl]amix=inputs=2:weights={OTHER_REAR} {CROWD_REAR}:normalize=0{_BUS}[ch6];
      [bas_rbr][oth_rbr][cR_rbr]amix=inputs=3:weights=0.4 {OTHER_REAR} {CROWD_REAR}:normalize=0{_BUS}[ch7];
      [ch0][ch1][ch2][ch3][ch6][ch7][ch4][ch5]amerge=inputs=8
    """
    _run_ffmpeg(inputs, graph, out_file, ffmpeg_bin)
    return out_file

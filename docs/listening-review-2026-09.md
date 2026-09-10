# Listening-experience review — September 2026

A technical pass over separation and mixing, looking for what would make the
7.1 output *sound* better. Measurements were taken on this repo's pipeline
with ffmpeg 4.4, Demucs 4.0.1 and audio-separator 0.44.2, on a 60 s clip.
Items marked **done** shipped with this review; the rest are recommendations
with the trade-off spelled out, in rough order of payoff.

## Fixed now

### The channel limiters had no headroom — **done**

Every channel bus and the Optimized pass end in `alimiter=limit=0.95`. That
filter's `level` option (auto level) defaults to *on*, and it multiplies the
output by `1/limit`. Measured on a tone: the "0.95 ceiling" came out
**+0.45 dB hotter than the input**, i.e. the real ceiling was 0 dBFS. Every
limiter now carries `level=disabled`, so the ceiling is genuinely 0.95
(−0.45 dBFS) and inter-sample peaks have somewhere to go. (Demucs' drum prep
already had it; the buses did not.)

### Demucs wrote 16-bit stems into a 24-bit chain — **done**

`demucs` defaults to 16-bit WAV. The stems are cached as FLAC and mixed into a
24-bit file, so the extra quantisation was pointless. Demucs now runs with
`--int24`. Existing cache entries stay valid (they are just 16-bit).

## Recommended — mixing

### 1. LFE is fed ~10 dB too hot

Receivers apply **+10 dB in-band gain to the LFE channel** on playback (the
Dolby/DTS convention; the film mixes this tool's output sits next to in Plex
were made against it). The default config sends bass at weight 1.0 and drums
at 0.6 into LFE *and* sends the same bass to SL/SR/BR at up to 1.0. On a
bass-managed system the sub therefore gets the bass twice — once redirected
from the mains, once from LFE at +10 dB. That is the classic "boomy, one-note
low end" of home-made surround mixes.

Suggested: LFE sources at about **0.3 (−10 dB)** in `DEFAULT_CONFIG` and in
the system prompt's guidance to the model, and say so in `CONFIG_SCHEMA.md`.
The Optimized pass already excludes LFE from smoothing, so nothing else
moves. This is the single change most likely to be *heard*, and it is a taste
call, so it is not applied here — try it on a couple of tracks first.

### 2. Loudness is measured the wrong way, and the result is loud

`optimize.compute_gains` targets −20 dB, but its "overall" figure is the
*mean per-channel power* (`sum(powers)/8`). The sum of eight channels is
9 dB higher, and BS.1770 weights the surround channels +1.5 dB. Measured on
the test render: **−12.0 LUFS integrated, true peak −2 dBFS**. Streaming
music sits at −14 to −16 LUFS stereo; surround music and Plex's film content
are quieter still. Users will reach for the volume knob between this and
everything else, and the hot level leaves the limiters working on drum hits.

Suggested: replace the RMS pass with `ebur128` (ffmpeg's BS.1770-4 meter,
already available: it reported the numbers above) targeting a configurable
integrated loudness, default around **−18 LUFS**, with a **−1 dBTP** true-peak
ceiling. Keep the per-channel *smoothing* step as is — it is doing a
different job. Cost: one extra analysis pass (seconds). `--target-lufs` on
the CLI and the `target:` line in comments.md would carry the number.

### 3. Mono-summing stereo stems loses width and can cancel

Most default routes use `pan=mono|c0=0.5*c0+0.5*c1` — piano, bass, other,
vocals, crowd are summed to mono before placement. Stems that were wide in the
source (chorused guitars, stereo piano, ambience in "other") partially cancel
when summed, which reads as thin or hollow. The schema already supports
`side: L/R`; the fix is policy: route wide stems as an **L/R pair across a
speaker pair** (piano L→FR, piano R→SR, other L→SL, other R→BL …) rather than
mono to one speaker, and tell the model the same. A cheap detector — the
stem's L/R correlation from `astats` — could flag which stems are wide.

### 4. LFE low-pass is 12 dB/oct

`lowpass=f=120` is a 2-pole (12 dB/oct) filter; bass management crossovers
are 24 dB/oct, typically 80–120 Hz. A shallow slope lets 200–300 Hz into the
sub, which localises. Two cascaded `lowpass` (ffmpeg caps `poles` at 2) give
24 dB/oct; 100 Hz is a reasonable default.

## Recommended — separation

### 5. A better vocal stem, two-pass

`htdemucs_6s` is the only model that yields guitar and piano, but it is the
*weakest* Demucs at everything else (vocals 9.6 / drums 8.5 / bass 10.1 dB
SDR, versus 10.8 / 10.0 / 12.0 for `htdemucs_ft`), and Demucs' own README
warns the piano stem "is not working great". The audio-separator registry
this project already uses lists Mel-Band RoFormer vocal models at
**12.4–12.6 dB SDR** (`vocals_mel_band_roformer.ckpt`,
`mel_band_roformer_kim_ft_unwa.ckpt`).

Suggested pipeline: crowd pass → **RoFormer vocals pass** (vocals + instrumental)
→ `htdemucs_6s` on the *instrumental* for guitar/piano/bass/drums/other, with
the Demucs vocal stem discarded. Vocals gain ~3 dB SDR (the centre channel is
the most exposed stem in this format), and the other five stems improve
because there is no vocal bleed left to mis-assign. Cost: one more RoFormer
pass — about the same as the crowd pass (12 s per minute of audio on a 3090;
several minutes per song on CPU). The cache key must include the model set
(`cdir = … / f"{standard}-{model_id}"`) so old stems are not reused.

### 6. Demucs quality dials are at their cheapest settings

`--shifts 2` (random-shift averaging) and `--overlap 0.5` each buy a small,
well-documented SDR gain for roughly 2× the Demucs time. Worth a `--quality
best` switch for GPU users; not a default for CPU users.

### 7. Stems are rescaled when they clip

Demucs' default `--clip-mode rescale` scales an *individual* stem down when it
would clip on export, which quietly changes that stem's level relative to the
others — and the stem levels are what the model designs the mix from.
`--float32` avoids it but FLAC cannot store float; `--int24` (now on) makes it
rarer. Low priority; noted so nobody is surprised by a soft drum stem.

## Not worth changing

- Sample rate: everything runs at 44.1 kHz end to end; Demucs resamples
  internally. Fine.
- The crowd model (`mel_band_roformer_crowd_aufr33_viperx`, 8.7 dB) is still
  the best crowd separator in the registry.
- `amix normalize=0` and the explicit 7.1 channel order are correct; ffprobe
  reports `channel_layout=7.1`, 24-bit, on the output.

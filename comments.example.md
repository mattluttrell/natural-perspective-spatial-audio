# comments.md — notes about the audio + your playback rig
#
# Free text guides the Natural Perspective model; per-channel dB lines tune the
# Optimized leveling for your speakers. Pass it with `--comments comments.md`
# (CLI) or the Comments picker (GUI). Any line that isn't a recognized trim
# label is ignored, so write freely.

Live club energy — keep the crowd lively but the vocals clear and centered.

# Per-channel labels: FL FR FC LFE BL BR SL SR
# Group shortcuts:    fronts (FL+FR), surrounds (SL+SR), backs (BL+BR)

surrounds: -3     # side speakers run a little hot in this room
backs: -3         # rear speakers too
FC: +1            # nudge the center up for vocals
LFE: -2           # single modest sub, keep it polite

target: -20       # overall loudness in dB (optional)

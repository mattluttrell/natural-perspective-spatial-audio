# Natural Perspective — mix configuration schema (draft)

Natural Perspective replaces the two hand-written mixes (On Stage, Front Row)
with a single **parameterized mixer** driven by a declarative configuration.
A model invents a scene and emits this config; deterministic code turns it
into the ffmpeg filtergraph and **guarantees the result is safe** no matter
what the model asks for.

This document is the contract between the model's output and the mixer. It is
the new "standard": an open format for describing a perspective-driven 7.1
spatial mix from stems.

## Pipeline

```
ingest → separate (incl. crowd) → measure each stem's level
       → model emits config (metadata + comments.md + filename + stem levels)
       → build mix from config → optional Optimized leveling → tag
       → write the per-track config + the album index.html
```

The model call happens **after** separation so it can see real stem levels
(the "pseudo pass"). With no API key, the **default config** below is used
verbatim; with a key, the model returns a customized config.

## Channels and stems

- Output is 8-channel 7.1, in file order: `FL FR FC LFE BL BR SL SR`.
- Stems (from Demucs `htdemucs_6s`): `vocals guitar piano bass drums other`,
  plus `crowd` when `separate_crowd` is true (MelBand-RoFormer, run first).

## Schema

```jsonc
{
  "scene":        "string",   // model-invented label, e.g. "Stadium Anthem"
  "perspective":  "string",   // one line: where the listener stands
  "separate_crowd": true,     // run the crowd model before Demucs
  "optimized":    true,       // run the Optimized leveling pass
  "stem_prep": {              // optional per-stem pre-processing (before routing)
    "drums": { "gain": 2.0, "limit": 0.95 }
  },
  "routing": {                // one entry per channel; omit a channel for silence
    "FC":  [ { "stem": "vocals", "weight": 1.0 } ],
    "FL":  [ { "stem": "guitar", "side": "L", "weight": 1.0 },
             { "stem": "bass", "weight": 0.8 } ],
    "LFE": { "lowpass_hz": 120,
             "sources": [ { "stem": "bass", "weight": 1.0 },
                          { "stem": "drums", "weight": 0.6 } ] }
    // ... FR, BL, BR, SL, SR
  }
}
```

Per-source fields:
- `stem` — one of the stem names above.
- `side` — `"L"` or `"R"` to take one side of a stereo stem; omit to sum mono.
- `weight` — linear mix weight (matches ffmpeg `amix` weights, `normalize=0`).
- a channel may instead be an object with `lowpass_hz` + `sources` (used for LFE).

### Builder guarantees (enforced regardless of model output)

- Every channel bus gets a peak limiter (`alimiter=limit=0.95`).
- `amix` runs with `normalize=0`, so weights are literal; the limiter — not
  auto-normalization — protects against clipping.
- Weights are clamped to a safe range; unknown stems are dropped; a channel
  with no usable sources is silence.
- LFE is always low-passed; non-LFE channels never receive the LFE band twice.

### Rig accommodation (comments.md)

`comments.md` carries both the free-text scene/perspective notes (read by the
model) and the playback-rig description. The rig influences routing two ways:
1. The model sees the rig prose and shapes `routing` accordingly — e.g. *"tiny
   rear speakers"* → it omits `bass` from `BL`/`BR`.
2. The per-channel dB trims (`SL: -3`, `target: -20`, …) still feed the
   **Optimized** leveling pass, exactly as today.

## Example 1 — default config (Front Row + Optimized)

Used verbatim when no API key is present. Re-expresses today's
`mix_front_row` + Optimized.

```json
{
  "scene": "Front Stage",
  "perspective": "Front row — band ahead, crowd around you.",
  "separate_crowd": true,
  "optimized": true,
  "stem_prep": { "drums": { "gain": 2.0, "limit": 0.95 } },
  "routing": {
    "FL":  [ { "stem": "guitar", "side": "L", "weight": 1.0 }, { "stem": "drums", "side": "L", "weight": 0.7 }, { "stem": "vocals", "weight": 0.3 } ],
    "FR":  [ { "stem": "guitar", "side": "R", "weight": 1.0 }, { "stem": "piano", "weight": 0.8 }, { "stem": "drums", "side": "R", "weight": 0.7 }, { "stem": "vocals", "weight": 0.3 } ],
    "FC":  [ { "stem": "vocals", "weight": 1.0 } ],
    "LFE": { "lowpass_hz": 120, "sources": [ { "stem": "bass", "weight": 1.0 }, { "stem": "drums", "weight": 0.6 } ] },
    "SL":  [ { "stem": "bass", "weight": 1.0 }, { "stem": "other", "weight": 1.0 }, { "stem": "drums", "side": "L", "weight": 1.0 }, { "stem": "crowd", "side": "L", "weight": 0.7 } ],
    "SR":  [ { "stem": "piano", "weight": 1.0 }, { "stem": "bass", "weight": 0.6 }, { "stem": "drums", "side": "R", "weight": 1.0 }, { "stem": "crowd", "side": "R", "weight": 0.7 } ],
    "BL":  [ { "stem": "other", "weight": 1.0 }, { "stem": "crowd", "side": "L", "weight": 1.0 } ],
    "BR":  [ { "stem": "bass", "weight": 0.4 }, { "stem": "other", "weight": 1.0 }, { "stem": "crowd", "side": "R", "weight": 1.0 } ]
  }
}
```

## Example 2 — On Stage (proves the schema covers the other mix)

Re-expresses today's `mix_on_stage` (no crowd stem; bass spread; drums behind).

```json
{
  "scene": "On Stage",
  "perspective": "On stage among the players; each instrument at one direction.",
  "separate_crowd": false,
  "optimized": false,
  "stem_prep": { "drums": { "gain": 2.0, "limit": 0.95 } },
  "routing": {
    "FL":  [ { "stem": "guitar", "side": "L", "weight": 1.0 }, { "stem": "bass", "weight": 0.8 } ],
    "FR":  [ { "stem": "guitar", "side": "R", "weight": 1.0 }, { "stem": "piano", "weight": 0.8 } ],
    "FC":  [ { "stem": "vocals", "weight": 1.0 } ],
    "LFE": { "lowpass_hz": 120, "sources": [ { "stem": "bass", "weight": 1.0 }, { "stem": "drums", "weight": 0.6 } ] },
    "SL":  [ { "stem": "bass", "weight": 1.0 }, { "stem": "other", "weight": 1.0 } ],
    "SR":  [ { "stem": "piano", "weight": 1.0 }, { "stem": "bass", "weight": 0.6 } ],
    "BL":  [ { "stem": "drums", "side": "L", "weight": 1.0 }, { "stem": "other", "weight": 0.5 } ],
    "BR":  [ { "stem": "drums", "side": "R", "weight": 1.0 }, { "stem": "bass", "weight": 0.4 } ]
  }
}
```

## Output layout and documentation

```
<Artist>/Natural Perspective Spatial Audio/<Title> [Natural Perspective Spatial Audio].flac
<Artist>/Natural Perspective Spatial Audio/<Title> [Natural Perspective Spatial Audio].config.json
<Artist>/Natural Perspective Spatial Audio/index.html
```

Each track keeps its emitted config alongside the FLAC, so a result can be
re-rendered without another model call (`--config file.json`). `index.html`
is built deterministically from those configs (scene, perspective, routing
table, stem levels, rig summary, decisions), with an optional Sonnet pass to
write the narrative prose.

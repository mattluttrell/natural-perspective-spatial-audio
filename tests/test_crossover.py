"""The LFE / mains split is a matched 4th-order Linkwitz-Riley crossover."""
from spatial_standards import mixconfig, natural


def _config():
    cfg = mixconfig.default_config()
    cfg["routing"]["LFE"] = {"lowpass_hz": 100, "sources": [{"stem": "bass", "weight": 1.0}]}
    cfg["routing"]["FL"] = [{"stem": "guitar", "side": "L", "weight": 1.0},
                            {"stem": "bass", "weight": 0.5, "highpass_hz": 100}]
    return cfg


def test_lowpass_and_highpass_are_lr4_pairs():
    cfg = _config()
    index = {n: i for i, n in enumerate(mixconfig.STEMS)}
    graph = mixconfig.build_filtergraph(cfg, index)
    assert graph.count("lowpass=f=100:poles=2,lowpass=f=100:poles=2") == 1
    assert graph.count("highpass=f=100:poles=2,highpass=f=100:poles=2") == 1


def test_highpassed_and_full_range_taps_of_one_stem_stay_separate():
    cfg = _config()
    cfg["routing"]["FR"].append({"stem": "bass", "weight": 0.5})  # full-range tap, no highpass
    index = {n: i for i, n in enumerate(mixconfig.STEMS)}
    graph = mixconfig.build_filtergraph(cfg, index)
    assert graph.count("highpass=f=100:poles=2,highpass=f=100:poles=2") == 1
    mixconfig.validate_config(cfg)


def test_crossover_frequency_is_clamped():
    assert "f=400:" in mixconfig._lr4("highpass", 5000)
    assert "f=30:" in mixconfig._lr4("lowpass", 1)


def test_model_output_carries_highpass_through():
    out = {"scene": "s", "perspective": "p", "separate_crowd": False, "optimized": True, "routing": [
        {"channel": ch, "lowpass_hz": 100 if ch == "LFE" else 0,
         "sources": [{"stem": "bass", "side": "mono", "weight": 1.0, "highpass_hz": 0 if ch == "LFE" else 100}]}
        for ch in mixconfig.CHANNELS]}
    cfg = natural.to_config(out)
    assert cfg["routing"]["FL"][0]["highpass_hz"] == 100
    assert "highpass_hz" not in cfg["routing"]["LFE"]["sources"][0]

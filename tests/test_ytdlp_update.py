"""ytdlp_update: version arithmetic and the decide-to-update logic (no network)."""
from datetime import date, timedelta

from spatial_standards import ytdlp_update as u


def test_version_key_matches_pypi_normalisation():
    assert u.version_key("2026.08.19") == u.version_key("2026.8.19")
    assert u.version_key("2026.09.05") > u.version_key("2026.08.19")


def test_version_date_and_staleness():
    assert u.version_date("2026.08.19") == date(2026, 8, 19)
    assert u.version_date(None) is None
    assert u.version_date("garbage") is None
    fresh = (date.today() - timedelta(days=3)).strftime("%Y.%m.%d")
    old = (date.today() - timedelta(days=60)).strftime("%Y.%m.%d")
    assert not u.is_stale(fresh)
    assert u.is_stale(old)
    assert not u.is_stale(None)


def test_looks_outdated_on_site_change_signatures():
    assert u.looks_outdated("ERROR: [youtube] x: Sign in to confirm you're not a bot", "2026.09.01")
    assert u.looks_outdated("Confirm you are on the latest version using yt-dlp -U", "2026.09.01")
    assert not u.looks_outdated("ERROR: [youtube] x: Video unavailable",
                                date.today().strftime("%Y.%m.%d"))


def test_unmanaged_binaries_are_left_alone(tmp_path):
    assert not u.managed("yt-dlp")                       # bare name, not found
    assert not u.managed(str(tmp_path / "yt-dlp"))       # not in our environment


def test_retry_after_update_retries_once_when_upgraded(monkeypatch):
    calls = []
    monkeypatch.setattr(u, "ensure_current", lambda b, s, force=False: True)

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("yt-dlp failed: nsig extraction failed")
        return "ok"
    assert u.retry_after_update(fn, "yt-dlp", lambda m: None) == "ok"
    assert len(calls) == 2


def test_retry_after_update_adds_hint_when_no_upgrade(monkeypatch):
    monkeypatch.setattr(u, "ensure_current", lambda b, s, force=False: False)
    monkeypatch.setattr(u, "installed_version", lambda b: "2025.01.01")

    def fn():
        raise RuntimeError("yt-dlp failed: HTTP Error 403")
    try:
        u.retry_after_update(fn, "yt-dlp", lambda m: None)
    except RuntimeError as e:
        assert "HTTP Error 403" in str(e) and "update it" in str(e)
    else:
        raise AssertionError("expected RuntimeError")

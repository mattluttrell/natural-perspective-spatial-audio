"""Artist/title resolution: flags > embedded tags > filename pattern."""
from spatial_standards.pipeline import TrackMeta, _derive_meta, meta_from_tags


def test_tags_fill_gaps_and_flags_win():
    tags = {"artist": "The GOODING BAND", "title": "Elysium", "track": "1/11", "date": "2002-05-01",
            "album": "Live at Loft 150"}
    m = meta_from_tags(TrackMeta(), tags)
    assert (m.artist, m.title, m.track_number, m.date) == ("The GOODING BAND", "Elysium", 1, "2002")
    m = meta_from_tags(TrackMeta(artist="Gooding", track_number=7), tags)
    assert (m.artist, m.title, m.track_number) == ("Gooding", "Elysium", 7)


def test_album_artist_preferred_over_track_artist():
    m = meta_from_tags(TrackMeta(), {"artist": "Guest feat. Someone", "album_artist": "The Band", "title": "X"})
    assert m.artist == "The Band"


def test_filename_fallback_strips_track_number():
    src = "/music/Gooding/01 - The GOODING BAND - Elysium.flac"
    assert _derive_meta(src, None, TrackMeta()) == ("The GOODING BAND", "Elysium")
    assert _derive_meta("/m/07. Artist - Song.mp3", None, TrackMeta()) == ("Artist", "Song")
    assert _derive_meta("/m/1-03 - Artist - Song.flac", None, TrackMeta()) == ("Artist", "Song")


def test_numeric_band_names_survive():
    # "311 - Down" is a band called 311, not track 311 — three digits then " - "
    # is ambiguous, so only strip when what remains still splits into two parts.
    assert _derive_meta("/m/10,000 Maniacs - These Are Days.flac", None, TrackMeta()) == ("10,000 Maniacs", "These Are Days")
    assert _derive_meta("/m/311 - Down.flac", None, TrackMeta()) == ("311", "Down")


def test_no_pattern_falls_back_to_stem():
    assert _derive_meta("/m/mystery.flac", None, TrackMeta()) == ("Unknown Artist", "mystery")

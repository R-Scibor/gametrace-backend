"""
tests/unit/test_confidence.py

Phase 3 — unit tests for the _confidence() scoring function used by the
enrichment worker to decide IGDB match quality.
"""
from app.tasks.enrichment import _confidence


def test_exact_match():
    assert _confidence("Cyberpunk 2077", "Cyberpunk 2077") == 1.0


def test_case_insensitive():
    assert _confidence("cyberpunk 2077", "Cyberpunk 2077") == 1.0


def test_high_similarity():
    # Different numeral style — same game, should meet the 0.85 enrichment threshold
    # FAILS with difflib (0.82); passes after rapidfuzz + roman numeral normalization
    assert _confidence("Diablo IV", "Diablo 4") >= 0.85


def test_low_similarity():
    assert _confidence("Minecraft", "Cyberpunk 2077") < 0.3


def test_just_below_threshold():
    # Sequel with different numeral — close but not close enough for 0.85 threshold
    assert _confidence("Hades", "Hades II") < 0.85


def test_empty_string():
    assert _confidence("", "Minecraft") == 0.0

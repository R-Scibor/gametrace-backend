"""
tests/unit/test_confidence.py

Phase 3 — unit tests for the _confidence() scoring function used by the
enrichment worker to decide IGDB match quality.

After the rapidfuzz refactor: uses WRatio on sanitized names, so partial
title matches, roman numeral normalization, and .exe stripping all work.
"""
from app.tasks.enrichment import _confidence


def test_exact_match():
    assert _confidence("Cyberpunk 2077", "Cyberpunk 2077") == 1.0


def test_case_insensitive():
    assert _confidence("cyberpunk 2077", "Cyberpunk 2077") == 1.0


def test_high_similarity():
    # Partial title match — same game, should clear the 0.85 threshold
    # FAILS with difflib SequenceMatcher (scores ~0.70); passes with WRatio
    assert _confidence("The Witcher 3", "The Witcher 3: Wild Hunt") >= 0.85


def test_roman_numeral_normalization():
    # Both sanitize to "diablo 4" → exact match
    assert _confidence("Diablo IV", "Diablo 4") == 1.0


def test_exe_extension_stripped():
    # .exe suffix stripped before comparison → exact match
    assert _confidence("The Witcher 3.exe", "The Witcher 3") == 1.0


def test_low_similarity():
    assert _confidence("Minecraft", "Cyberpunk 2077") < 0.3


def test_just_below_threshold():
    # Share "Dragon Age" prefix but are clearly different titles
    # WRatio should stay below 0.85 (verified empirically post-rebuild)
    assert _confidence("Dragon Age Origins", "Dragon Age Inquisition") < 0.85


def test_empty_string():
    assert _confidence("", "Minecraft") == 0.0

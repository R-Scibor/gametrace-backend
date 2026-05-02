"""
tests/unit/test_confidence.py

Unit tests for _confidence() — the scoring function used by the enrichment
worker to decide IGDB match quality.

Scoring pipeline (see enrichment.py module docstring for full spec):
  1. Both strings are _sanitize()-d (lowercase, strip extensions/brackets/separators,
     roman numerals → arabic digits).
  2. fuzz.WRatio on sanitized forms.
  3. Number guard: if digit sets differ, score is capped at _NUMBER_MISMATCH_CAP (0.75),
     keeping same-franchise-different-entry pairs below the 0.85 CONFIDENCE_THRESHOLD.
"""
from app.tasks.enrichment import _confidence, _sanitize

THRESHOLD = 0.85


# ── _sanitize: word boundaries preserved (regression guard) ──────────────────

def test_sanitize_preserves_word_boundaries():
    # _sanitize output is also fed verbatim to IGDB/Steam as the search term.
    # Gluing tokens kills full-text recall — see docs/game-matching.md gotcha.
    assert _sanitize("The Farmer Was Replaced") == "the farmer was replaced"
    assert _sanitize("Europa Universalis V") == "europa universalis 5"


# ── Exact / case ────────────────────────────────────────────────────────────

def test_exact_match():
    assert _confidence("Cyberpunk 2077", "Cyberpunk 2077") == 1.0


def test_case_insensitive():
    assert _confidence("cyberpunk 2077", "Cyberpunk 2077") == 1.0


# ── Partial title / subtitle ─────────────────────────────────────────────────

def test_high_similarity():
    # Same number on both sides → no number guard → WRatio handles subtitle
    # FAILS with difflib SequenceMatcher (~0.70); passes with WRatio
    assert _confidence("The Witcher 3", "The Witcher 3: Wild Hunt") >= THRESHOLD


# ── Roman numeral normalisation ──────────────────────────────────────────────

def test_roman_numeral_normalization():
    # Both sanitize to "diablo 4" → identical strings → 1.0
    assert _confidence("Diablo IV", "Diablo 4") == 1.0


# ── Extension stripping ──────────────────────────────────────────────────────

def test_exe_extension_stripped():
    # .exe stripped before comparison → exact match
    assert _confidence("The Witcher 3.exe", "The Witcher 3") == 1.0


# ── Number guard — different sequel entries ───────────────────────────────────

def test_number_guard_missing_vs_sequel():
    # One string has no number, the other has "2" (after roman numeral map)
    # WRatio alone would return ~0.95; guard caps at 0.75
    assert _confidence("Hades", "Hades II") < THRESHOLD


def test_number_guard_different_entries():
    # Both strings have numbers but they differ → guard applies
    assert _confidence("Diablo 3", "Diablo 4") < THRESHOLD


def test_number_guard_same_number_no_penalty():
    # Same number on both sides → no cap, WRatio result stands
    assert _confidence("Cyberpunk 2077", "Cyberpunk 2077: Phantom Liberty") >= THRESHOLD


# ── Dissimilar games ─────────────────────────────────────────────────────────

def test_low_similarity():
    assert _confidence("Minecraft", "Cyberpunk 2077") < 0.3


def test_just_below_threshold():
    # Share "Dragon Age" prefix but clearly different titles; no numbers → WRatio only
    assert _confidence("Dragon Age Origins", "Dragon Age Inquisition") < THRESHOLD


# ── Edge ─────────────────────────────────────────────────────────────────────

def test_empty_string():
    assert _confidence("", "Minecraft") == 0.0

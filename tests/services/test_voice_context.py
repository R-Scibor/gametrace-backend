"""
Unit tests for app.services.voice_context.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.voice_context import (
    build_candidate_block,
    build_datetime_block,
    match_candidates,
    resolve_timezone,
)


# ── resolve_timezone ──────────────────────────────────────────────────────────

def test_resolve_timezone_valid():
    assert resolve_timezone("Europe/Warsaw").key == "Europe/Warsaw"


def test_resolve_timezone_invalid_falls_back_to_default():
    assert resolve_timezone("Not/A/Zone").key == "Europe/Warsaw"


def test_resolve_timezone_empty_falls_back_to_default():
    assert resolve_timezone(None).key == "Europe/Warsaw"
    assert resolve_timezone("").key == "Europe/Warsaw"


def test_resolve_timezone_bare_utc_uses_default():
    """A user with the column-default 'UTC' gets the configured default instead."""
    assert resolve_timezone("UTC").key == "Europe/Warsaw"


# ── build_datetime_block ──────────────────────────────────────────────────────

def test_datetime_block_uses_user_timezone():
    fixed = datetime(2026, 5, 15, 18, 30, tzinfo=ZoneInfo("UTC"))
    block = build_datetime_block("Europe/Warsaw", now=fixed)
    assert "Europe/Warsaw" in block
    # 18:30 UTC = 20:30 in Warsaw (CEST in May)
    assert "20:30" in block
    assert "Friday" in block
    assert "2026-05-15" in block


def test_datetime_block_invalid_tz_falls_back_to_default():
    fixed = datetime(2026, 5, 15, 18, 30, tzinfo=ZoneInfo("UTC"))
    block = build_datetime_block("Garbage", now=fixed)
    assert "Europe/Warsaw" in block
    # 18:30 UTC = 20:30 in Warsaw (CEST in May)
    assert "20:30" in block


# ── match_candidates ──────────────────────────────────────────────────────────

def test_match_candidates_finds_phonetic_match():
    library = ["Zenless Zone Zero", "Xenoblade Chronicles", "Persona 5", "Elden Ring"]
    transcript = "I was playing Xenazon Zero for half an hour"
    matches = match_candidates(transcript, library)
    assert matches
    assert matches[0][0] == "Zenless Zone Zero"
    assert matches[0][1] >= 50


def test_match_candidates_respects_floor():
    library = ["Doom Eternal", "Civilization VI"]
    matches = match_candidates("yyy zzz qqq", library)
    assert matches == []


def test_match_candidates_empty_library():
    assert match_candidates("any transcript", []) == []


def test_match_candidates_returns_at_most_three():
    library = [f"Game {i}" for i in range(10)] + ["Hades", "Hollow Knight"]
    transcript = "I played Hades and Hollow Knight"
    matches = match_candidates(transcript, library)
    assert len(matches) <= 3


# ── build_candidate_block ─────────────────────────────────────────────────────

def test_candidate_block_empty_when_no_matches():
    assert build_candidate_block([]) == ""


def test_candidate_block_lists_names():
    block = build_candidate_block([("Hades", 80.0), ("Hollow Knight", 70.0)])
    assert "Hades" in block
    assert "Hollow Knight" in block

"""
tests/unit/test_company_resolution.py

Unit tests for the pure company_resolution helper.
No I/O, no DB, no IGDB calls — all inputs are hand-crafted fixtures.

Shapes accepted by extract_companies:
  {"company": {"name": "X", "parent": {"name": "Y"}}, "developer": True, "publisher": False}
  All keys are optional; read defensively with .get().
"""
from app.services.company_resolution import (
    PUBLISHER_ALIASES,
    canonicalize_publishers,
    dedupe_developers,
    extract_companies,
    resolve_companies,
)


# ── PUBLISHER_ALIASES sanity ─────────────────────────────────────────────────

def test_publisher_aliases_align_to_igdb_root():
    # Both alias to miHoYo — the IGDB parent-chain root for this family — so the
    # fallback matches what parent rollup produces when IGDB has the link.
    assert PUBLISHER_ALIASES.get("cognosphere") == "miHoYo"
    assert PUBLISHER_ALIASES.get("hoyoverse") == "miHoYo"


# ── extract_companies ────────────────────────────────────────────────────────

def test_extract_companies_splits_devs_pubs_and_both():
    """Developer-only, publisher-only, and both-at-once entries split correctly."""
    involved = [
        {"company": {"name": "Dev Studio"}, "developer": True, "publisher": False},
        {"company": {"name": "Pub Corp"}, "developer": False, "publisher": True},
        {"company": {"name": "Both Inc"}, "developer": True, "publisher": True},
    ]
    devs, pubs = extract_companies(involved)
    assert devs == ["Dev Studio", "Both Inc"]
    assert [p[0] for p in pubs] == ["Pub Corp", "Both Inc"]


def test_extract_companies_captures_parent_name():
    """Parent name is captured as the second element of each publisher pair."""
    involved = [
        {
            "company": {"name": "Cognosphere", "parent": {"name": "HoYoverse Ltd"}},
            "developer": False,
            "publisher": True,
        }
    ]
    _, pubs = extract_companies(involved)
    assert pubs == [("Cognosphere", "HoYoverse Ltd")]


def test_extract_companies_tolerates_missing_fields():
    """No exception raised when company, name, or role booleans are absent."""
    involved = [
        {},                                        # entirely empty entry
        {"company": None},                         # company present but None
        {"company": {"name": "No Role"}},          # booleans absent — defaults to False
        {"company": {}, "developer": True},        # name absent inside company
    ]
    devs, pubs = extract_companies(involved)
    # Only the entry with developer=True survives — but it has no name, so nothing
    assert devs == []
    assert pubs == []


# ── canonicalize_publishers ──────────────────────────────────────────────────

def test_canonicalize_parent_used_when_present():
    """When a parent name exists, it replaces the subsidiary name."""
    pairs = [("Subsidiary Games", "Big Parent Corp")]
    result = canonicalize_publishers(pairs)
    assert result == ["Big Parent Corp"]


def test_canonicalize_alias_no_parent_cognosphere():
    """Cognosphere has no parent → alias map resolves it to the IGDB root miHoYo."""
    pairs = [("Cognosphere", None)]
    result = canonicalize_publishers(pairs)
    assert result == ["miHoYo"]


def test_canonicalize_no_alias_no_parent_keeps_name():
    """Company with no parent and no alias keeps its own name."""
    pairs = [("Valve", None)]
    result = canonicalize_publishers(pairs)
    assert result == ["Valve"]


def test_canonicalize_dedupes_cognosphere_and_hoyoverse():
    """Cognosphere and HoYoverse (no parent links) both alias to miHoYo → one entry."""
    pairs = [("Cognosphere", None), ("HoYoverse", None)]
    result = canonicalize_publishers(pairs)
    assert result == ["miHoYo"]


def test_canonicalize_dedupe_is_order_preserving_and_case_insensitive():
    """First occurrence is kept; later duplicates (any case) are dropped."""
    pairs = [("Valve", None), ("valve", None)]
    result = canonicalize_publishers(pairs)
    assert result == ["Valve"]


# ── dedupe_developers ────────────────────────────────────────────────────────

def test_dedupe_developers_preserves_distinct_studios():
    result = dedupe_developers(["Studio A", "Studio B"])
    assert result == ["Studio A", "Studio B"]


def test_dedupe_developers_collapses_exact_repeats():
    result = dedupe_developers(["Studio A", "Studio A"])
    assert result == ["Studio A"]


def test_dedupe_developers_collapses_case_variants():
    result = dedupe_developers(["Studio A", "studio a"])
    assert result == ["Studio A"]


def test_dedupe_developers_no_rollup():
    """A studio that shares a name with a publisher parent is kept as-is."""
    result = dedupe_developers(["HoYoverse", "miHoYo"])
    assert result == ["HoYoverse", "miHoYo"]


# ── resolve_companies (end-to-end) ───────────────────────────────────────────

def test_resolve_companies_end_to_end():
    """
    Full pipeline fixture:
    - miHoYo: developer only, no parent, no alias
    - Cognosphere: publisher only, no parent, alias → miHoYo
    - Both Inc: both dev and publisher, has parent → parent used for publisher side
    - Dup Dev: developer repeated twice → deduped
    """
    involved = [
        {"company": {"name": "miHoYo"}, "developer": True, "publisher": False},
        {"company": {"name": "Cognosphere"}, "developer": False, "publisher": True},
        {
            "company": {"name": "Both Inc", "parent": {"name": "Parent Corp"}},
            "developer": True,
            "publisher": True,
        },
        {"company": {"name": "Dup Dev"}, "developer": True, "publisher": False},
        {"company": {"name": "dup dev"}, "developer": True, "publisher": False},
    ]
    devs, pubs = resolve_companies(involved)
    assert devs == ["miHoYo", "Both Inc", "Dup Dev"]
    assert pubs == ["miHoYo", "Parent Corp"]

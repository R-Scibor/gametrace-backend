"""
app/services/company_resolution.py

Pure helper for IGDB company canonicalization.
No I/O, no DB, no IGDB calls.

Roles:
- extract_companies: split raw involved_companies into (dev_names, publisher_pairs)
- canonicalize_publishers: parent-or-self → alias map → order-preserving dedupe
- dedupe_developers: order-preserving case-insensitive dedupe, no rollup
- resolve_companies: composes all three; the public call site
"""
from __future__ import annotations

# Alias map: keys are lowercased; values are the canonical display name.
# Applied after the parent-or-self choice so a subsidiary alias still fires
# when IGDB has no parent link. Values must match the IGDB parent-chain root
# for the same entity, otherwise a game whose parent link IGDB happens to have
# and one where it's missing would land in two different buckets.
#
# Examples:
# - Cognosphere / HoYoverse (no parent) → miHoYo (IGDB root)
# - Iwplay variants (regional operator, no parent link) → Perfect World Games
PUBLISHER_ALIASES: dict[str, str] = {
    "cognosphere": "miHoYo",
    "hoyoverse": "miHoYo",
    "iwplay": "Perfect World Games",
    "iwplay world": "Perfect World Games",
    "iwplay world interactive entertainment": "Perfect World Games",
}


def extract_companies(
    involved_companies: list[dict],
) -> tuple[list[str], list[tuple[str, str | None]]]:
    """
    Parse a raw ``involved_companies`` list from an IGDB response.

    Returns:
        developer_names  – flat list of company names where developer=True
        publisher_pairs  – list of (company_name, parent_name_or_None)
                           for entries where publisher=True
    """
    developer_names: list[str] = []
    publisher_pairs: list[tuple[str, str | None]] = []

    for entry in involved_companies:
        company = entry.get("company") or {}
        name: str | None = company.get("name") if isinstance(company, dict) else None
        if not name:
            continue

        parent_dict = company.get("parent") if isinstance(company, dict) else None
        parent_name: str | None = (
            parent_dict.get("name") if isinstance(parent_dict, dict) else None
        )

        is_dev: bool = bool(entry.get("developer", False))
        is_pub: bool = bool(entry.get("publisher", False))

        if is_dev:
            developer_names.append(name)
        if is_pub:
            publisher_pairs.append((name, parent_name))

    return developer_names, publisher_pairs


def canonicalize_publishers(pairs: list[tuple[str, str | None]]) -> list[str]:
    """
    Resolve each publisher pair to a single canonical name, then dedupe.

    Resolution order per entry:
      1. Use ``parent_name`` if truthy.
      2. Otherwise use ``company_name``.
      3. Map through ``PUBLISHER_ALIASES`` (lowercase key lookup).

    Dedupe is order-preserving and case-insensitive (casefold); first
    occurrence wins.
    """
    resolved: list[str] = []
    for company_name, parent_name in pairs:
        chosen = parent_name if parent_name else company_name
        canonical = PUBLISHER_ALIASES.get(chosen.casefold(), chosen)
        resolved.append(canonical)

    return _casefold_dedupe(resolved)


def dedupe_developers(names: list[str]) -> list[str]:
    """Order-preserving case-insensitive dedupe. No parent rollup applied."""
    return _casefold_dedupe(names)


def resolve_companies(
    involved_companies: list[dict],
) -> tuple[list[str], list[str]]:
    """
    Full pipeline: extract → canonicalize/dedupe publishers, dedupe developers.

    Returns:
        (developers, publishers) — both are flat deduplicated name lists.
    """
    dev_names, pub_pairs = extract_companies(involved_companies)
    publishers = canonicalize_publishers(pub_pairs)
    developers = dedupe_developers(dev_names)
    return developers, publishers


# ── internal helpers ──────────────────────────────────────────────────────────

def _casefold_dedupe(names: list[str]) -> list[str]:
    """Return names with duplicates removed, preserving first-occurrence order."""
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            result.append(name)
    return result

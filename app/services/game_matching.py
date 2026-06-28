"""
Shared game-matching helpers: sanitize, confidence scoring, IGDB search.

Extracted from app.tasks.enrichment so API endpoints can call these
without taking a Celery dependency.

See app/tasks/enrichment.py module docstring for the full pipeline spec
(sanitize → confidence → igdb_search → steam_search → decision).
"""
import logging
import re
from datetime import date
from typing import NamedTuple

import httpx
from rapidfuzz import fuzz

from app.core.config import settings
from app.services.company_resolution import resolve_companies
from app.tasks.igdb_auth import get_igdb_token, invalidate_igdb_token

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.85
# Score ceiling applied when sanitized digit sets differ (sequel guard).
# Must stay below CONFIDENCE_THRESHOLD so mismatched-number pairs never enrich.
_NUMBER_MISMATCH_CAP = 0.75

_ROMAN_MAP = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
    "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15",
}


# ---------------------------------------------------------------------------
# Custom exception to signal 429 back to the sync Celery task for retry
# ---------------------------------------------------------------------------

class _RateLimited(Exception):
    pass


# ---------------------------------------------------------------------------
# Sync HTTP helpers — called via asyncio.to_thread() from enrichment tasks
# ---------------------------------------------------------------------------

def _sanitize(s: str) -> str:
    s = s.lower()
    s = re.sub(r'\.\w{2,5}$', '', s)              # strip file extension (.exe, .app)
    s = re.sub(r'[\[\(][^\]\)]*[\]\)]', '', s)    # remove [tags] and (tags)
    s = s.replace('&', 'and')                      # & → and
    s = re.sub(r'[:\-_]', ' ', s)                 # structural separators → space
    s = re.sub(r'[^a-z0-9\s]', '', s)             # strip remaining non-alphanumeric
    tokens = [_ROMAN_MAP.get(t, t) for t in s.split()]
    # Words stay space-separated. The space-collapse trick (for substring
    # alignment of exe-style names) lives inside _confidence — gluing here
    # would break IGDB / Steam search recall on multi-word titles.
    return ' '.join(tokens)


def _confidence(a: str, b: str) -> float:
    # Strip whitespace from sanitized forms so partial_ratio finds exe-style
    # names (e.g. "witcher3") as substrings of canonical titles
    # ("thewitcher3wildhunt"). Applied symmetrically; scoring-only.
    sa = _sanitize(a).replace(' ', '')
    sb = _sanitize(b).replace(' ', '')
    score = fuzz.WRatio(sa, sb) / 100.0

    nums_a = set(re.findall(r'\d+', sa))
    nums_b = set(re.findall(r'\d+', sb))
    if (nums_a or nums_b) and nums_a != nums_b:
        score = min(score, _NUMBER_MISMATCH_CAP)

    return score


class IGDBResult(NamedTuple):
    cover_url: str | None
    confidence: float
    genres: list[str]
    themes: list[str]
    developers: list[str]
    publishers: list[str]
    first_release_date: date | None


def _empty_igdb_result() -> IGDBResult:
    return IGDBResult(
        cover_url=None,
        confidence=0.0,
        genres=[],
        themes=[],
        developers=[],
        publishers=[],
        first_release_date=None,
    )


def _normalize_cover_url(url: str | None) -> str | None:
    """Normalize IGDB cover URL: protocol-relative → https, t_thumb → t_cover_big."""
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    return url.replace("/t_thumb/", "/t_cover_big/")


class IGDBCandidate(NamedTuple):
    igdb_id: int
    name: str
    year: int | None
    cover_url: str | None
    score: float


def _igdb_search_candidates(name: str) -> list[IGDBCandidate]:
    """Return all IGDB candidates for *name* ranked by confidence score descending.

    Runs the same ``search "...";`` query as ``_igdb_search`` but exposes every
    row as an :class:`IGDBCandidate` so API endpoints can present a pick-list.

    Raises :class:`_RateLimited` on HTTP 401 or 429.
    """
    if not settings.igdb_client_id or not settings.igdb_client_secret:
        logger.warning("IGDB credentials not set — skipping candidate search")
        return []

    token = get_igdb_token()
    clean_name = _sanitize(name)
    safe_name = clean_name.replace('"', '\\"')

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            "https://api.igdb.com/v4/games",
            headers={
                "Client-ID": settings.igdb_client_id,
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain",
            },
            content=(
                f'search "{safe_name}"; '
                'fields name,cover.url,cover.image_id,alternative_names.name,'
                'genres.name,themes.name,'
                'involved_companies.company.name,involved_companies.developer,'
                'involved_companies.publisher,first_release_date; '
                'limit 5;'
            ),
        )

    if resp.status_code == 401:
        invalidate_igdb_token()
        raise _RateLimited("IGDB-auth")

    if resp.status_code == 429:
        raise _RateLimited("IGDB")

    resp.raise_for_status()

    candidates: list[IGDBCandidate] = []
    for game in resp.json():
        candidate_names = [game.get("name", "")]
        for alt in game.get("alternative_names", []):
            if alt.get("name"):
                candidate_names.append(alt["name"])
        score = max((_confidence(name, n) for n in candidate_names if n), default=0.0)

        cover = game.get("cover")
        cover_url = _normalize_cover_url(cover.get("url") if cover else None)

        ts = game.get("first_release_date")
        year = date.fromtimestamp(ts).year if ts else None

        candidates.append(IGDBCandidate(
            igdb_id=game["id"],
            name=game.get("name", ""),
            year=year,
            cover_url=cover_url,
            score=score,
        ))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def _igdb_fetch_by_id(igdb_id: int) -> tuple[str, IGDBResult] | None:
    """Fetch a single IGDB game row by its numeric id.

    Returns ``(canonical_name, IGDBResult)`` with ``confidence=1.0`` (exact
    lookup — no fuzzy scoring needed), or ``None`` when the id yields no row.

    Raises :class:`_RateLimited` on HTTP 401 or 429.
    """
    if not settings.igdb_client_id or not settings.igdb_client_secret:
        logger.warning("IGDB credentials not set — skipping fetch-by-id")
        return None

    token = get_igdb_token()

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            "https://api.igdb.com/v4/games",
            headers={
                "Client-ID": settings.igdb_client_id,
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain",
            },
            content=(
                f'where id = {igdb_id}; '
                'fields name,cover.url,'
                'genres.name,themes.name,'
                'involved_companies.company.name,involved_companies.company.parent.name,'
                'involved_companies.developer,'
                'involved_companies.publisher,first_release_date; '
                'limit 1;'
            ),
        )

    if resp.status_code == 401:
        invalidate_igdb_token()
        raise _RateLimited("IGDB-auth")

    if resp.status_code == 429:
        raise _RateLimited("IGDB")

    resp.raise_for_status()

    rows = resp.json()
    if not rows:
        return None

    game = rows[0]
    canonical_name = game.get("name", "")

    cover = game.get("cover")
    cover_url = _normalize_cover_url(cover.get("url") if cover else None)

    genres = [g["name"] for g in game.get("genres", []) if g.get("name")]
    themes = [t["name"] for t in game.get("themes", []) if t.get("name")]
    developers, publishers = resolve_companies(game.get("involved_companies", []))
    ts = game.get("first_release_date")
    release_date = date.fromtimestamp(ts) if ts else None

    return canonical_name, IGDBResult(
        cover_url=cover_url,
        confidence=1.0,
        genres=genres,
        themes=themes,
        developers=developers,
        publishers=publishers,
        first_release_date=release_date,
    )


def _igdb_search(name: str) -> IGDBResult:
    """Returns IGDBResult with cover, confidence, and metadata for the best candidate.

    Raises _RateLimited on HTTP 429 or 401.
    """
    if not settings.igdb_client_id or not settings.igdb_client_secret:
        logger.warning("IGDB credentials not set — skipping IGDB search")
        return _empty_igdb_result()

    token = get_igdb_token()
    clean_name = _sanitize(name)
    safe_name = clean_name.replace('"', '\\"')

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            "https://api.igdb.com/v4/games",
            headers={
                "Client-ID": settings.igdb_client_id,
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain",
            },
            content=(
                f'search "{safe_name}"; '
                'fields name,cover.url,cover.image_id,alternative_names.name,'
                'genres.name,themes.name,'
                'involved_companies.company.name,involved_companies.company.parent.name,'
                'involved_companies.developer,'
                'involved_companies.publisher,first_release_date; '
                'limit 5;'
            ),
        )

    if resp.status_code == 401:
        invalidate_igdb_token()
        raise _RateLimited("IGDB-auth")  # triggers Celery backoff retry

    if resp.status_code == 429:
        raise _RateLimited("IGDB")

    resp.raise_for_status()

    best_score = 0.0
    best_cover: str | None = None
    best_genres: list[str] = []
    best_themes: list[str] = []
    best_developers: list[str] = []
    best_publishers: list[str] = []
    best_release: date | None = None

    for game in resp.json():
        candidate_names = [game.get("name", "")]
        for alt in game.get("alternative_names", []):
            if alt.get("name"):
                candidate_names.append(alt["name"])
        score = max(_confidence(name, n) for n in candidate_names if n)

        if score > best_score:
            best_score = score

            cover = game.get("cover")
            if cover and cover.get("url"):
                url = cover["url"]
                if url.startswith("//"):
                    url = "https:" + url
                url = url.replace("/t_thumb/", "/t_cover_big/")
                best_cover = url
            else:
                best_cover = None

            best_genres = [g["name"] for g in game.get("genres", []) if g.get("name")]
            best_themes = [t["name"] for t in game.get("themes", []) if t.get("name")]
            best_developers, best_publishers = resolve_companies(game.get("involved_companies", []))
            ts = game.get("first_release_date")
            best_release = date.fromtimestamp(ts) if ts else None

    return IGDBResult(
        cover_url=best_cover,
        confidence=best_score,
        genres=best_genres,
        themes=best_themes,
        developers=best_developers,
        publishers=best_publishers,
        first_release_date=best_release,
    )

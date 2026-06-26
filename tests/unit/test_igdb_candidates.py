"""
tests/unit/test_igdb_candidates.py

TDD for Task 2: ranked IGDB candidate search + fetch-by-id.

_igdb_search_candidates(name) → list[IGDBCandidate] sorted by score desc
_igdb_fetch_by_id(igdb_id)   → tuple[str, IGDBResult] | None
"""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from app.services.game_matching import (
    IGDBCandidate,
    _igdb_fetch_by_id,
    _igdb_search_candidates,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_client(payload: list, status: int = 200) -> MagicMock:
    """Return a mock httpx.Client context manager with a canned IGDB response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = mock_resp
    return mock_client


@contextmanager
def _patch_igdb(mock_client: MagicMock):
    """Patch every IGDB dependency in game_matching for the duration of the block.

    Mocks settings, the token fetch, the token invalidation (so the 401 path
    never touches real Redis), and httpx.Client. Yields the mock settings.
    """
    with patch("app.services.game_matching.settings") as s, \
         patch("app.services.game_matching.get_igdb_token", return_value="tok"), \
         patch("app.services.game_matching.invalidate_igdb_token"), \
         patch("app.services.game_matching.httpx.Client", return_value=mock_client):
        s.igdb_client_id = "test-id"
        s.igdb_client_secret = "test-secret"
        yield s


# Three-game payload for "witcher 3" search
_THREE_GAMES = [
    {
        "id": 1942,
        "name": "The Witcher 3: Wild Hunt",
        "cover": {"url": "//images.igdb.com/igdb/image/upload/t_thumb/co1wyy.jpg"},
        "alternative_names": [{"name": "Witcher 3"}],
        "genres": [{"name": "Role-playing (RPG)"}],
        "themes": [{"name": "Fantasy"}],
        "involved_companies": [
            {"developer": True, "publisher": False, "company": {"name": "CD Projekt Red"}},
            {"developer": False, "publisher": True, "company": {"name": "CD Projekt"}},
        ],
        "first_release_date": 1431993600,  # 2015-05-19
    },
    {
        "id": 472,
        "name": "The Witcher 2: Assassins of Kings",
        "cover": {"url": "https://images.igdb.com/igdb/image/upload/t_thumb/co2abc.jpg"},
        "alternative_names": [],
        "genres": [{"name": "Role-playing (RPG)"}],
        "themes": [],
        "involved_companies": [],
        "first_release_date": 1305158400,
    },
    {
        "id": 9999,
        "name": "Minecraft",
        "cover": None,
        "alternative_names": [],
        "genres": [],
        "themes": [],
        "involved_companies": [],
        "first_release_date": None,
    },
]


# ---------------------------------------------------------------------------
# _igdb_search_candidates
# ---------------------------------------------------------------------------

class TestIgdbSearchCandidates:
    def _call(self, name: str, payload: list) -> list:
        mock_client = _make_mock_client(payload)
        with _patch_igdb(mock_client):
            return _igdb_search_candidates(name)

    def test_returns_list_of_igdb_candidates(self):
        results = self._call("witcher 3", _THREE_GAMES)
        assert isinstance(results, list)
        assert all(isinstance(r, IGDBCandidate) for r in results)

    def test_returns_all_three_rows(self):
        results = self._call("witcher 3", _THREE_GAMES)
        assert len(results) == 3

    def test_sorted_by_score_descending(self):
        results = self._call("witcher 3", _THREE_GAMES)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_top_candidate_is_witcher_3(self):
        results = self._call("witcher 3", _THREE_GAMES)
        assert results[0].igdb_id == 1942
        assert results[0].name == "The Witcher 3: Wild Hunt"

    def test_cover_url_protocol_relative_becomes_https(self):
        results = self._call("witcher 3", _THREE_GAMES)
        assert results[0].cover_url is not None
        assert results[0].cover_url.startswith("https://")

    def test_cover_url_thumb_replaced_with_cover_big(self):
        results = self._call("witcher 3", _THREE_GAMES)
        assert "/t_cover_big/" in results[0].cover_url
        assert "/t_thumb/" not in results[0].cover_url

    def test_missing_cover_is_none(self):
        # Minecraft entry has cover=None
        results = self._call("witcher 3", _THREE_GAMES)
        minecraft = next(r for r in results if r.igdb_id == 9999)
        assert minecraft.cover_url is None

    def test_year_extracted_from_timestamp(self):
        results = self._call("witcher 3", _THREE_GAMES)
        # 1431993600 → 2015-05-19 → year 2015
        witcher = next(r for r in results if r.igdb_id == 1942)
        assert witcher.year == 2015

    def test_year_none_when_no_release_date(self):
        results = self._call("witcher 3", _THREE_GAMES)
        minecraft = next(r for r in results if r.igdb_id == 9999)
        assert minecraft.year is None

    def test_igdb_id_is_int(self):
        results = self._call("witcher 3", _THREE_GAMES)
        assert all(isinstance(r.igdb_id, int) for r in results)

    def test_empty_payload_returns_empty_list(self):
        results = self._call("obscure game", [])
        assert results == []

    def test_rate_limited_401_raises(self):
        from app.services.game_matching import _RateLimited
        mock_client = _make_mock_client([], status=401)
        with _patch_igdb(mock_client):
            with pytest.raises(_RateLimited):
                _igdb_search_candidates("anything")

    def test_rate_limited_429_raises(self):
        from app.services.game_matching import _RateLimited
        mock_client = _make_mock_client([], status=429)
        with _patch_igdb(mock_client):
            with pytest.raises(_RateLimited):
                _igdb_search_candidates("anything")


# ---------------------------------------------------------------------------
# _igdb_fetch_by_id
# ---------------------------------------------------------------------------

_FETCH_PAYLOAD = [
    {
        "id": 1942,
        "name": "The Witcher 3: Wild Hunt",
        "cover": {"url": "//images.igdb.com/igdb/image/upload/t_thumb/co1wyy.jpg"},
        "genres": [{"name": "Role-playing (RPG)"}, {"name": "Adventure"}],
        "themes": [{"name": "Fantasy"}, {"name": "Open world"}],
        "involved_companies": [
            {"developer": True, "publisher": False, "company": {"name": "CD Projekt Red"}},
            {"developer": False, "publisher": True, "company": {"name": "CD Projekt"}},
        ],
        "first_release_date": 1431993600,
    }
]


class TestIgdbFetchById:
    def _call(self, igdb_id: int, payload: list):
        mock_client = _make_mock_client(payload)
        with _patch_igdb(mock_client):
            return _igdb_fetch_by_id(igdb_id)

    def test_returns_tuple_on_match(self):
        result = self._call(1942, _FETCH_PAYLOAD)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_first_element_is_canonical_name(self):
        result = self._call(1942, _FETCH_PAYLOAD)
        canonical_name, _ = result
        assert canonical_name == "The Witcher 3: Wild Hunt"

    def test_second_element_is_igdb_result(self):
        from app.services.game_matching import IGDBResult
        result = self._call(1942, _FETCH_PAYLOAD)
        _, igdb_result = result
        assert isinstance(igdb_result, IGDBResult)

    def test_confidence_is_1_0(self):
        _, igdb_result = self._call(1942, _FETCH_PAYLOAD)
        assert igdb_result.confidence == 1.0

    def test_genres_populated(self):
        _, igdb_result = self._call(1942, _FETCH_PAYLOAD)
        assert "Role-playing (RPG)" in igdb_result.genres
        assert "Adventure" in igdb_result.genres

    def test_themes_populated(self):
        _, igdb_result = self._call(1942, _FETCH_PAYLOAD)
        assert "Fantasy" in igdb_result.themes
        assert "Open world" in igdb_result.themes

    def test_developers_populated(self):
        _, igdb_result = self._call(1942, _FETCH_PAYLOAD)
        assert "CD Projekt Red" in igdb_result.developers

    def test_publishers_populated(self):
        _, igdb_result = self._call(1942, _FETCH_PAYLOAD)
        assert "CD Projekt" in igdb_result.publishers

    def test_cover_url_normalized(self):
        _, igdb_result = self._call(1942, _FETCH_PAYLOAD)
        assert igdb_result.cover_url is not None
        assert igdb_result.cover_url.startswith("https://")
        assert "/t_cover_big/" in igdb_result.cover_url

    def test_first_release_date_populated(self):
        _, igdb_result = self._call(1942, _FETCH_PAYLOAD)
        from datetime import date
        assert igdb_result.first_release_date == date(2015, 5, 19)

    def test_returns_none_on_empty_payload(self):
        result = self._call(9999, [])
        assert result is None

    def test_rate_limited_401_raises(self):
        from app.services.game_matching import _RateLimited
        mock_client = _make_mock_client([], status=401)
        with _patch_igdb(mock_client):
            with pytest.raises(_RateLimited):
                _igdb_fetch_by_id(1942)

    def test_rate_limited_429_raises(self):
        from app.services.game_matching import _RateLimited
        mock_client = _make_mock_client([], status=429)
        with _patch_igdb(mock_client):
            with pytest.raises(_RateLimited):
                _igdb_fetch_by_id(1942)

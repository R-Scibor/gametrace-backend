"""Worker outcomes for an ambiguous IGDB search. Search scoring lives in test_igdb_search_ties."""
from unittest.mock import AsyncMock, patch

import pytest

from app.models.game import EnrichmentStatus
from app.tasks.enrichment import IGDBResult, _run_enrichment
from tests.tasks.test_enrichment import (
    _assert_unchanged,
    _db_patches,
    _enriched_game,
    _game_mock,
    _igdb_result,
)


@pytest.fixture(autouse=True)
def _mock_sync_review_preferences():
    with patch("app.tasks.enrichment.sync_review_preferences", new_callable=AsyncMock), \
         patch("app.services.game_matching.sync_review_preferences", new_callable=AsyncMock):
        yield


def _ambiguous_igdb_result() -> IGDBResult:
    return _igdb_result(None, 1.0, name=None, igdb_id=None)._replace(ambiguous=True)


async def test_ambiguous_igdb_tie_needs_review_and_skips_steam():
    game = _game_mock("Lords of the Fallen")
    p_engine, p_sm, _ = _db_patches(game)
    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search",
               return_value=_ambiguous_igdb_result()), \
         patch("app.tasks.enrichment._steam_search") as steam:
        status, cover, ext_id = await _run_enrichment(1)
    assert status == EnrichmentStatus.NEEDS_REVIEW
    assert cover is None
    assert ext_id is None
    assert game.enrichment_status == EnrichmentStatus.NEEDS_REVIEW
    assert game.external_api_id is None
    assert game.primary_name == "Lords of the Fallen"
    steam.assert_not_called()


async def test_ambiguous_igdb_tie_keeps_an_enriched_row():
    game = _enriched_game()
    p_engine, p_sm, _ = _db_patches(game)
    with p_engine, p_sm, \
         patch("app.tasks.enrichment.sync_review_preferences", new_callable=AsyncMock) as sync, \
         patch("app.tasks.enrichment._igdb_search",
               return_value=_ambiguous_igdb_result()), \
         patch("app.tasks.enrichment._steam_search") as steam:
        status, _, ext_id = await _run_enrichment(1)
    assert status == EnrichmentStatus.ENRICHED
    assert ext_id == "igdb:42"
    _assert_unchanged(game)
    steam.assert_not_called()
    sync.assert_not_awaited()

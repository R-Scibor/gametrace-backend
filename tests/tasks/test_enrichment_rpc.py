"""RPC enrichment. The name pipeline is the branch with no application id."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.game import CoverSource, EnrichmentStatus, Game
from app.services.game_matching import IGDBResult
from app.tasks.discord_rpc import RpcCall, RpcPayload
from app.tasks.enrichment import LookupRetryable, _retry_source, _run_enrichment


@pytest.fixture(autouse=True)
def _mock_sync_review_preferences():
    with patch("app.tasks.enrichment.sync_review_preferences", new_callable=AsyncMock), \
         patch("app.services.game_matching.sync_review_preferences", new_callable=AsyncMock):
        yield


def _game(name: str = "Lords of the Fallen") -> MagicMock:
    game = MagicMock(spec=Game)
    game.id = 77
    game.primary_name = name
    game.cover_source = CoverSource.EXTERNAL
    game.cover_image_url = "http://old-cover"
    game.enrichment_status = EnrichmentStatus.PENDING
    game.external_api_id = None
    game.genres = ["RPG"]
    game.themes = []
    game.developers = []
    game.publishers = []
    game.first_release_date = None
    return game


def _session(game: MagicMock) -> MagicMock:
    session = AsyncMock()
    session.get.return_value = game
    result = MagicMock()
    result.scalar_one_or_none.return_value = game
    result.all.return_value = []
    result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    nested = AsyncMock()
    nested.__aenter__ = AsyncMock(return_value=nested)
    nested.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=nested)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


def _db(game: MagicMock):
    engine = MagicMock()
    engine.dispose = AsyncMock()
    factory = MagicMock(return_value=_session(game))
    return (
        patch("app.tasks.enrichment.create_async_engine", return_value=engine),
        patch("app.tasks.enrichment.async_sessionmaker", return_value=factory),
    )


def _igdb(name: str, igdb_id: int) -> tuple[str, IGDBResult]:
    result = IGDBResult(
        cover_url="http://igdb-cover",
        confidence=1.0,
        genres=["Adventure"],
        themes=[],
        developers=[],
        publishers=[],
        first_release_date=None,
        name=name,
        igdb_id=igdb_id,
    )
    return name, result


def _ok(*igdb: int, steam: tuple[int, ...] = ()) -> RpcPayload:
    return RpcPayload(RpcCall.OK, igdb, steam)


async def _run(game, payload, *, chosen="463425384893906944", fetched=None,
               steam_hit=None, igdb_error=None, steam_error=None,
               retries=0, max_retries=5, credentials=("id", "secret")):
    p_engine, p_sm = _db(game)
    with p_engine, p_sm, \
         patch("app.tasks.enrichment.settings.igdb_client_id", credentials[0]), \
         patch("app.tasks.enrichment.settings.igdb_client_secret", credentials[1]), \
         patch("app.tasks.enrichment._chosen_application_id", new_callable=AsyncMock, return_value=chosen), \
         patch("app.tasks.enrichment.fetch_discord_rpc", return_value=payload) as fetch, \
         patch("app.tasks.enrichment._igdb_search") as igdb_search, \
         patch("app.tasks.enrichment._steam_search") as steam_search, \
         patch("app.tasks.enrichment._igdb_fetch_by_id", side_effect=igdb_error, return_value=fetched) as igdb_fetch, \
         patch(
             "app.tasks.enrichment._steam_appdetails", side_effect=steam_error, return_value=steam_hit
         ) as steam_details:
        outcome = await _run_enrichment(game.id, retries=retries, max_retries=max_retries)
    return outcome, fetch, igdb_search, steam_search, igdb_fetch, steam_details


async def test_no_application_id_uses_the_name_pipeline():
    game = _game()
    p_engine, p_sm = _db(game)
    with p_engine, p_sm, \
         patch("app.tasks.enrichment._chosen_application_id", new_callable=AsyncMock, return_value=None), \
         patch("app.tasks.enrichment.fetch_discord_rpc") as fetch, \
         patch("app.tasks.enrichment._igdb_search", return_value=IGDBResult(
             cover_url="http://cover", confidence=0.95, genres=[], themes=[],
             developers=[], publishers=[], first_release_date=None,
             name="Canonical", igdb_id=42,
         )), \
         patch("app.tasks.enrichment._steam_search") as steam:
        status, _cover, ext_id = await _run_enrichment(game.id)
    fetch.assert_not_called()
    steam.assert_not_called()
    assert status == EnrichmentStatus.ENRICHED
    assert ext_id == "igdb:42"


async def test_identified_row_skips_discord_and_leaves_metadata():
    """Admin requeue, backfill, and reset_enrichment all enter here without clearing the id."""
    game = _game()
    game.external_api_id = "igdb:21593"
    game.enrichment_status = EnrichmentStatus.ENRICHED
    p_engine, p_sm = _db(game)
    with p_engine, p_sm, \
         patch(
             "app.tasks.enrichment._chosen_application_id",
             new_callable=AsyncMock,
             return_value="463425384893906944",
         ), \
         patch("app.tasks.enrichment.fetch_discord_rpc") as fetch, \
         patch("app.tasks.enrichment._igdb_search") as igdb_search, \
         patch("app.tasks.enrichment._steam_search") as steam_search:
        status, cover, ext_id = await _run_enrichment(game.id)
    fetch.assert_not_called()
    igdb_search.assert_not_called()
    steam_search.assert_not_called()
    assert status == EnrichmentStatus.ENRICHED
    assert cover == "http://old-cover"
    assert ext_id == "igdb:21593"
    assert game.genres == ["RPG"]
    assert game.primary_name == "Lords of the Fallen"


async def test_pending_stub_does_not_name_search():
    game = _game()
    outcome, fetch, igdb_search, steam_search, igdb_fetch, steam_details = await _run(
        game, _ok(4847), fetched=_igdb("Lords of the Fallen (2014)", 4847),
    )
    assert outcome[0] == EnrichmentStatus.ENRICHED
    igdb_search.assert_not_called()
    steam_search.assert_not_called()
    steam_details.assert_not_called()
    fetch.assert_called_once_with("463425384893906944")
    igdb_fetch.assert_called_once_with(4847)


async def test_one_igdb_applies_when_the_name_differs_and_steam_ids_exist():
    game = _game()
    outcome, _fetch, igdb_search, steam_search, _igdb_fetch, steam_details = await _run(
        game,
        _ok(4847, steam=(265300, 1501750)),
        fetched=_igdb("Lords of the Fallen (2014)", 4847),
    )
    assert outcome[2] == "igdb:4847"
    assert game.primary_name == "Lords of the Fallen (2014)"
    assert game.external_api_id == "igdb:4847"
    igdb_search.assert_not_called()
    steam_search.assert_not_called()
    steam_details.assert_not_called()


async def test_unset_igdb_credentials_leave_the_row():
    game = _game()
    outcome, _fetch, igdb_search, steam_search, igdb_fetch, steam_details = await _run(
        game, _ok(4847), credentials=("", ""),
    )
    assert outcome[0] == EnrichmentStatus.PENDING
    assert game.external_api_id is None
    assert game.enrichment_status == EnrichmentStatus.PENDING
    igdb_fetch.assert_not_called()
    igdb_search.assert_not_called()
    steam_search.assert_not_called()
    steam_details.assert_not_called()


async def test_igdb_miss_needs_review_and_does_not_try_steam():
    game = _game()
    outcome, _fetch, igdb_search, steam_search, _igdb_fetch, steam_details = await _run(
        game, _ok(4847, steam=(265300,)), fetched=None,
    )
    assert outcome[0] == EnrichmentStatus.NEEDS_REVIEW
    assert game.enrichment_status == EnrichmentStatus.NEEDS_REVIEW
    steam_details.assert_not_called()
    igdb_search.assert_not_called()
    steam_search.assert_not_called()


async def test_enriched_null_external_id_stays_enriched():
    game = _game()
    game.enrichment_status = EnrichmentStatus.ENRICHED
    outcome, *_rest = await _run(game, RpcPayload(RpcCall.TERMINAL))
    assert outcome[0] == EnrichmentStatus.ENRICHED
    assert game.enrichment_status == EnrichmentStatus.ENRICHED
    assert game.external_api_id is None


async def test_one_steam_id_uses_appdetails():
    game = _game()
    cover = "https://cdn.akamai.steamstatic.com/steam/apps/265300/library_600x900.jpg"
    outcome, _fetch, igdb_search, steam_search, igdb_fetch, steam_details = await _run(
        game, _ok(steam=(265300,)), steam_hit=(265300, cover, "Lords of the Fallen"),
    )
    assert outcome[2] == "steam:265300"
    assert game.primary_name == "Lords of the Fallen"
    assert game.cover_image_url == cover
    igdb_fetch.assert_not_called()
    igdb_search.assert_not_called()
    steam_search.assert_not_called()
    steam_details.assert_called_once_with(265300)


async def test_steam_success_false_needs_review():
    game = _game()
    outcome, *_rest = await _run(game, _ok(steam=(265300,)), steam_hit=None)
    assert outcome[0] == EnrichmentStatus.NEEDS_REVIEW
    assert game.external_api_id is None


@pytest.mark.parametrize(
    "payload",
    [
        _ok(4847, 21593),
        _ok(steam=(265300, 1501750)),
        _ok(),
        RpcPayload(RpcCall.TERMINAL),
    ],
)
async def test_ambiguous_payloads_need_review_without_name_search(payload):
    game = _game()
    outcome, _fetch, igdb_search, steam_search, igdb_fetch, steam_details = await _run(game, payload)
    assert outcome[0] == EnrichmentStatus.NEEDS_REVIEW
    igdb_search.assert_not_called()
    steam_search.assert_not_called()
    igdb_fetch.assert_not_called()
    steam_details.assert_not_called()


async def test_discord_429_retries_with_source_discord_rpc():
    game = _game()
    with pytest.raises(LookupRetryable) as raised:
        await _run(game, RpcPayload(RpcCall.RETRY), retries=0, max_retries=5)
    assert raised.value.source == "discord_rpc"
    assert game.enrichment_status == EnrichmentStatus.PENDING


async def test_last_discord_failure_sets_needs_review_without_raising():
    game = _game()
    outcome, *_rest = await _run(game, RpcPayload(RpcCall.RETRY), retries=5, max_retries=5)
    assert outcome[0] == EnrichmentStatus.NEEDS_REVIEW
    assert game.enrichment_status == EnrichmentStatus.NEEDS_REVIEW


async def test_discord_403_sets_needs_review_without_raising():
    game = _game()
    outcome, _fetch, igdb_search, *_rest = await _run(
        game, RpcPayload(RpcCall.TERMINAL), retries=0, max_retries=5,
    )
    assert outcome[0] == EnrichmentStatus.NEEDS_REVIEW
    igdb_search.assert_not_called()


def test_retry_source_passes_discord_rpc():
    assert _retry_source(LookupRetryable("discord_rpc")) == "discord_rpc"
    assert _retry_source(LookupRetryable("igdb")) == "igdb"


async def test_igdb_429_retries_with_source_igdb():
    from app.tasks.enrichment import _RateLimited

    game = _game()
    with pytest.raises(LookupRetryable) as raised:
        await _run(game, _ok(4847), igdb_error=_RateLimited("IGDB"))
    assert raised.value.source == "igdb"
    assert game.enrichment_status == EnrichmentStatus.PENDING


async def test_lowest_alias_application_id_is_fetched():
    game = _game()
    _outcome, fetch, *_rest = await _run(
        game, _ok(4847), chosen="111", fetched=_igdb("Other Name", 4847),
    )
    fetch.assert_called_once_with("111")

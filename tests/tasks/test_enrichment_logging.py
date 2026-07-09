import logging

from tests.unit.test_logging import _install


def test_game_not_found_logs_structured_game_id(caplog):
    from app.tasks import enrichment

    _install(caplog, "worker")
    with caplog.at_level(logging.WARNING, logger="app.tasks.enrichment"):
        enrichment.logger.warning(
            "enrich_game.not_found", extra={"game_id": 99}
        )

    record = caplog.records[-1]
    assert record.getMessage() == "enrich_game.not_found"
    assert record.game_id == 99
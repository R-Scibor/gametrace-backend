"""Unit tests for app.services.enrichment_dispatch.queue_enrichment."""
from unittest.mock import MagicMock, patch

from app.services.enrichment_dispatch import queue_enrichment


def test_queue_enrichment_calls_apply_async_with_fixed_task_id():
    mock_apply_async = MagicMock()
    with patch(
        "app.tasks.enrichment.enrich_game.apply_async",
        mock_apply_async,
    ):
        queue_enrichment(42)

    mock_apply_async.assert_called_once_with(args=[42], task_id="enrich_game_42")
"""Enqueue Celery enrichment tasks for games."""


def queue_enrichment(game_id: int) -> None:
    """Fire-and-forget enrichment task.

    The fixed task id is the Celery result key. It does not collapse duplicate messages.
    """
    from app.tasks.enrichment import enrich_game

    task_id = f"enrich_game_{game_id}"
    enrich_game.apply_async(args=[game_id], task_id=task_id)
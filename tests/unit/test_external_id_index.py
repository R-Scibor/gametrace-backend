"""Partial unique index on games.external_api_id.

create_all in tests/conftest.py builds this from the model. The migration
file is the same index for production and does not rewrite rows.
"""

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.game import Game
from app.services.external_ids import EXTERNAL_API_ID_UNIQUE, igdb_external_id
from tests.factories import make_game

_MIGRATION = Path("alembic/versions/0023_games_external_api_id_unique.py")


def test_model_declares_the_partial_unique_index():
    indexes = {index.name: index for index in Game.__table__.indexes}
    index = indexes[EXTERNAL_API_ID_UNIQUE]
    assert index.unique is True
    where = str(index.dialect_options["postgresql"]["where"])
    assert "external_api_id IS NOT NULL" in where


def test_migration_0023_creates_that_index_without_rewriting_rows():
    text = _MIGRATION.read_text()
    assert 'revision: str = "0023"' in text
    assert 'down_revision: str | None = "0022"' in text
    assert EXTERNAL_API_ID_UNIQUE in text
    assert "external_api_id IS NOT NULL" in text
    assert "UPDATE" not in text
    assert "CHECK" not in text


async def test_bare_and_prefixed_ids_both_insert_and_a_repeat_does_not(db):
    bare = await make_game(db, "Lords of the Fallen")
    prefixed = await make_game(db, "Lords of the Fallen (IGDB)")
    bare.external_api_id = "21593"
    prefixed.external_api_id = igdb_external_id(21593)
    await db.flush()

    duplicate = await make_game(db, "Second IGDB row")
    duplicate.external_api_id = igdb_external_id(21593)
    with pytest.raises(IntegrityError):
        await db.flush()

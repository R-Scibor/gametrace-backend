"""game_aliases.application_id is a nullable note. Not unique. No backfill."""

from pathlib import Path

from app.models.game import GameAlias

_MIGRATION = Path("alembic/versions/0026_game_alias_application_id.py")


def test_model_column_is_nullable_varchar_32_and_not_unique():
    column = GameAlias.__table__.c.application_id
    assert column.nullable is True
    assert column.type.length == 32
    assert column.unique is not True
    assert GameAlias.discord_process_name.unique is True


def test_migration_0026_adds_the_column_without_rewriting_rows():
    text = _MIGRATION.read_text()
    assert 'revision: str = "0026"' in text
    assert 'down_revision: str | None = "0025"' in text
    assert "application_id" in text
    assert "String(length=32)" in text
    assert "nullable=True" in text
    assert "unique=True" not in text
    assert "UPDATE" not in text
    assert "op.drop_column" in text

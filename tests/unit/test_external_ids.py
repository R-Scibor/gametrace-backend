"""Formatters for games.external_api_id and the unique-index predicate."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.external_ids import (
    EXTERNAL_API_ID_UNIQUE,
    igdb_external_id,
    is_external_id_conflict,
    steam_external_id,
)


def test_igdb_external_id_prefixes_the_digits():
    assert igdb_external_id(21593) == "igdb:21593"
    assert igdb_external_id(21593) != "21593"


def test_steam_external_id_prefixes_the_digits():
    assert steam_external_id(730) == "steam:730"
    assert steam_external_id(730) != "730"


@pytest.mark.parametrize("factory", [igdb_external_id, steam_external_id])
@pytest.mark.parametrize("bad", [0, -1, True, False])
def test_helpers_reject_non_positive_and_bool(factory, bad):
    with pytest.raises(ValueError):
        factory(bad)


class _Orig(Exception):
    def __init__(self, sqlstate: str, constraint_name: str | None, text: str):
        super().__init__(text)
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


def _integrity(sqlstate: str, constraint_name: str | None, text: str) -> IntegrityError:
    return IntegrityError("INSERT", {}, _Orig(sqlstate, constraint_name, text))


def test_conflict_predicate_matches_this_constraint_only():
    taken = _integrity("23505", EXTERNAL_API_ID_UNIQUE, "duplicate")
    other_unique = _integrity("23505", "game_aliases_discord_process_name_key", "duplicate")
    overlap = _integrity("23P01", "excl_game_sessions_no_overlap", "overlap")
    named_only_in_text = _integrity("23505", None, f'duplicate key value violates "{EXTERNAL_API_ID_UNIQUE}"')

    assert is_external_id_conflict(taken) is True
    assert is_external_id_conflict(named_only_in_text) is True
    assert is_external_id_conflict(other_unique) is False
    assert is_external_id_conflict(overlap) is False

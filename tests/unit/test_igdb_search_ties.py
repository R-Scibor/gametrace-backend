"""Tied IGDB titles must not collapse to whichever row came back first."""
from unittest.mock import MagicMock, patch

from app.services.game_matching import _empty_igdb_result, _igdb_search


def _search(name: str, rows: list):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = rows
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response
    with patch("app.services.game_matching.settings") as settings, \
         patch("app.services.game_matching.get_igdb_token", return_value="tok"), \
         patch("app.services.game_matching.httpx.Client", return_value=client):
        settings.igdb_client_id = "cid"
        settings.igdb_client_secret = "secret"
        return _igdb_search(name)


def test_empty_result_is_not_ambiguous():
    assert _empty_igdb_result().ambiguous is False


def test_two_lords_of_the_fallen_ids_are_ambiguous():
    result = _search("Lords of the Fallen", [
        {
            "id": 194987,
            "name": "Lords of the Fallen",
            "cover": {"url": "//images.igdb.com/t_thumb/old.jpg"},
            "genres": [{"name": "RPG"}],
        },
        {
            "id": 21593,
            "name": "Lords of the Fallen",
            "cover": {"url": "//images.igdb.com/t_thumb/new.jpg"},
            "genres": [{"name": "Adventure"}],
        },
    ])
    assert result.ambiguous is True
    assert result.confidence == 1.0
    assert result.igdb_id is None
    assert result.name is None
    assert result.cover_url is None
    assert result.genres == []
    assert result.themes == []
    assert result.developers == []
    assert result.publishers == []
    assert result.first_release_date is None


def test_one_higher_id_stays_the_winner():
    result = _search("Lords of the Fallen", [
        {"id": 194987, "name": "zzzz unrelated"},
        {
            "id": 21593,
            "name": "Lords of the Fallen",
            "cover": {"url": "https://images.igdb.com/t_thumb/new.jpg"},
        },
    ])
    assert result.ambiguous is False
    assert result.igdb_id == 21593
    assert result.name == "Lords of the Fallen"
    assert result.cover_url == "https://images.igdb.com/t_cover_big/new.jpg"


def test_a_lower_second_id_does_not_tie():
    result = _search("Lords of the Fallen", [
        {"id": 194987, "name": "Lords of the Fallen"},
        {"id": 999, "name": "Lords of the Fallen 2"},
    ])
    assert result.ambiguous is False
    assert result.igdb_id == 194987
    assert result.name == "Lords of the Fallen"


def test_tie_below_the_threshold_keeps_the_first_row():
    """Both rows are the sequel. The number guard caps them under 0.85, so they tie as a miss."""
    result = _search("Hades", [
        {"id": 194987, "name": "Hades II"},
        {"id": 21593, "name": "Hades II"},
    ])
    assert result.ambiguous is False
    assert result.igdb_id == 194987
    assert result.name == "Hades II"
    assert 0 < result.confidence < 0.85


def test_a_later_higher_score_replaces_an_earlier_pair():
    result = _search("Lords of the Fallen", [
        {"id": 1, "name": "Hades II"},
        {"id": 2, "name": "Hades II"},
        {"id": 21593, "name": "Lords of the Fallen"},
    ])
    assert result.ambiguous is False
    assert result.igdb_id == 21593
    assert result.name == "Lords of the Fallen"


def test_a_row_without_an_id_does_not_tie():
    result = _search("Lords of the Fallen", [
        {"name": "Lords of the Fallen", "cover": {"url": "//images.igdb.com/t_thumb/old.jpg"}},
        {
            "id": 21593,
            "name": "Lords of the Fallen",
            "cover": {"url": "https://images.igdb.com/t_thumb/new.jpg"},
        },
    ])
    assert result.ambiguous is False
    assert result.igdb_id == 21593
    assert result.cover_url == "https://images.igdb.com/t_cover_big/new.jpg"


def test_alternative_name_on_one_id_is_not_a_tie():
    result = _search("Lords of the Fallen", [
        {
            "id": 21593,
            "name": "zzzz unrelated",
            "alternative_names": [{"name": "Lords of the Fallen"}],
        },
    ])
    assert result.ambiguous is False
    assert result.igdb_id == 21593
    assert result.name == "zzzz unrelated"
    assert result.confidence >= 0.85

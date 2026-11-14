import json
from unittest.mock import MagicMock, patch

import httpx

from app.services.game_aliases import choose_application_id
from app.tasks.discord_rpc import RpcCall, fetch_discord_rpc, parse_third_party_skus


def test_string_and_int_sku_ids_dedupe_and_other_distributors_drop():
    body = {
        "third_party_skus": [
            {"distributor": "igdb", "id": 21593},
            {"distributor": "igdb", "id": "21593"},
            {"distributor": "igdb", "id": True},
            {"distributor": "igdb", "id": 0},
            {"distributor": "igdb", "id": "nope"},
            {"distributor": "steam", "id": "265300"},
            {"distributor": "steam", "id": 265300},
            {"distributor": "xbox", "id": 99},
            {"distributor": "IGDB", "id": 4847},
            "not-a-dict",
        ]
    }
    assert parse_third_party_skus(body) == ((21593,), (265300,))


def test_missing_or_non_list_skus_are_unusable():
    assert parse_third_party_skus({}) is None
    assert parse_third_party_skus({"third_party_skus": None}) is None
    assert parse_third_party_skus({"third_party_skus": {}}) is None
    assert parse_third_party_skus([]) is None
    assert parse_third_party_skus({"third_party_skus": []}) == ((), ())


def test_lowest_alias_id_wins():
    rows = [(20, "20000000000000000"), (3, "30000000000000000"), (9, None), (1, "")]
    assert choose_application_id(rows) == "30000000000000000"
    assert choose_application_id([(4, None)]) is None
    assert choose_application_id([]) is None


def _client(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = response
    return client


def _response(status: int, payload=None, *, bad_json: bool = False) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    if bad_json:
        response.json.side_effect = json.JSONDecodeError("bad", "", 0)
    else:
        response.json.return_value = payload
    return response


def test_fetch_classifies_http_results():
    ok = {"third_party_skus": [{"distributor": "igdb", "id": 4847}]}
    cases = [
        (_response(200, ok), RpcCall.OK),
        (_response(200, {}), RpcCall.TERMINAL),
        (_response(200, ok, bad_json=True), RpcCall.TERMINAL),
        (_response(401), RpcCall.TERMINAL),
        (_response(403), RpcCall.TERMINAL),
        (_response(404), RpcCall.TERMINAL),
        (_response(400), RpcCall.TERMINAL),
        (_response(429), RpcCall.RETRY),
        (_response(500), RpcCall.RETRY),
        (_response(503), RpcCall.RETRY),
    ]
    for response, expected in cases:
        with patch("app.tasks.discord_rpc.httpx.Client", return_value=_client(response)):
            payload = fetch_discord_rpc("463425384893906944")
        assert payload.call is expected
        if expected is RpcCall.OK:
            assert payload.igdb_ids == (4847,)
            assert payload.steam_ids == ()


def test_fetch_transport_error_retries_and_sends_no_token():
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.side_effect = httpx.ConnectError("down")
    with patch("app.tasks.discord_rpc.httpx.Client", return_value=client):
        payload = fetch_discord_rpc("463425384893906944")
    assert payload.call is RpcCall.RETRY
    url = client.get.call_args.args[0]
    assert url == "https://discord.com/api/v9/applications/463425384893906944/rpc"
    assert client.get.call_args.kwargs == {}

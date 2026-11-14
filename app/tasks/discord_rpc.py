"""Unauthenticated Discord RPC lookup for store SKUs."""

import json
import re
from dataclasses import dataclass
from enum import Enum

import httpx

_SKU_ID = re.compile(r"^[0-9]+$")
_RPC_URL = "https://discord.com/api/v9/applications/{application_id}/rpc"


class RpcCall(Enum):
    OK = "ok"
    RETRY = "retry"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class RpcPayload:
    call: RpcCall
    igdb_ids: tuple[int, ...] = ()
    steam_ids: tuple[int, ...] = ()


def _sku_int(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 1 else None
    if isinstance(raw, str) and _SKU_ID.fullmatch(raw):
        value = int(raw)
        return value if value >= 1 else None
    return None


def _dedupe(values: list[int]) -> tuple[int, ...]:
    seen: set[int] = set()
    ordered: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def parse_third_party_skus(body: object) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    """``(igdb_ids, steam_ids)`` or None when the body is not a usable payload.

    An empty list is usable and means no store ids. A missing key, a non-list,
    or a non-object body is not usable.
    """
    if not isinstance(body, dict) or "third_party_skus" not in body:
        return None
    skus = body["third_party_skus"]
    if not isinstance(skus, list):
        return None
    igdb: list[int] = []
    steam: list[int] = []
    for item in skus:
        if not isinstance(item, dict):
            continue
        parsed = _sku_int(item.get("id"))
        if parsed is None:
            continue
        distributor = item.get("distributor")
        if distributor == "igdb":
            igdb.append(parsed)
        elif distributor == "steam":
            steam.append(parsed)
    return _dedupe(igdb), _dedupe(steam)


def fetch_discord_rpc(application_id: str) -> RpcPayload:
    """GET the RPC document. No token. Timeout is 10 seconds.

    429, 5xx, and transport errors are retries. Every other non-200, a
    non-JSON body, and an unusable payload are terminal. There is no cache.
    """
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(_RPC_URL.format(application_id=application_id))
    except httpx.TransportError:
        return RpcPayload(RpcCall.RETRY)
    code = response.status_code
    if code == 429 or code >= 500:
        return RpcPayload(RpcCall.RETRY)
    if code != 200:
        return RpcPayload(RpcCall.TERMINAL)
    try:
        body = response.json()
    except json.JSONDecodeError:
        return RpcPayload(RpcCall.TERMINAL)
    parsed = parse_third_party_skus(body)
    if parsed is None:
        return RpcPayload(RpcCall.TERMINAL)
    return RpcPayload(RpcCall.OK, parsed[0], parsed[1])

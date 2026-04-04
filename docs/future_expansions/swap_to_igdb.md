# Plan: Swap RAWG → IGDB jako primary enrichment

**Status:** Zaimplementowane (2026-04-04)
**Powód:** RAWG `background_image` to landscape screenshot (16:9), nie nadaje się jako cover w gridzie. IGDB ma dedykowane pole `cover` z box artem w pionie (264×374px).

---

## Kontekst problemu

RAWG zwraca `background_image` — to screenshot/artwork poziomy, bez tytułu gry. IGDB ma `cover.url` wskazujący na oficjalny box art pionowy (`t_cover_big`). Steam fallback też wymaga poprawki: obecne `header.jpg` (460×215, poziomy) → zmienić na `library_600x900.jpg` (pionowy).

---

## Pliki do zmiany

| Plik | Co się zmienia |
|------|----------------|
| `app/tasks/igdb_auth.py` | **NOWY** — pobieranie i cachowanie tokenu Twitch w Redis |
| `app/tasks/enrichment.py` | zamiana `_rawg_search` → `_igdb_search`; przy Steam: `header.jpg` → `library_600x900.jpg` |
| `app/core/config.py` | dodać `igdb_client_id`, `igdb_client_secret`; usunąć `rawg_api_key` |
| `docker-compose.yml` | dodać nowe env vars do `worker` i `api` |
| `.env` | ręcznie: `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET` |

Brak zmian schematu DB, brak zmian API, brak zmian frontendu.

---

## Kroki implementacji

### 1. Rejestracja aplikacji Twitch
Wejdź na dev.twitch.tv → utwórz aplikację → skopiuj `Client ID` i `Client Secret`.

### 2. Nowy moduł `app/tasks/igdb_auth.py`

Sync helper (pasuje do obecnego wzorca `asyncio.to_thread()`). Używa sync `redis-py` (już jest jako zależność Celery) i sync `httpx`.

```python
import redis as redis_sync
import httpx
from app.core.config import settings

IGDB_TOKEN_KEY = "igdb:access_token"

def get_igdb_token() -> str:
    r = redis_sync.from_url(settings.redis_url, decode_responses=True)
    token = r.get(IGDB_TOKEN_KEY)
    if token:
        return token
    return _refresh(r)

def invalidate_igdb_token() -> None:
    """Wywołać przy 401 — force refresh przy następnym get_igdb_token()."""
    r = redis_sync.from_url(settings.redis_url, decode_responses=True)
    r.delete(IGDB_TOKEN_KEY)

def _refresh(r) -> str:
    resp = httpx.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": settings.igdb_client_id,
            "client_secret": settings.igdb_client_secret,
            "grant_type": "client_credentials",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    r.setex(IGDB_TOKEN_KEY, data["expires_in"] - 300, token)  # bufor 5 min
    return token
```

### 3. Zamiana `_rawg_search` → `_igdb_search` w `enrichment.py`

```python
def _igdb_search(name: str) -> tuple[str | None, float]:
    if not settings.igdb_client_id or not settings.igdb_client_secret:
        logger.warning("IGDB credentials not set — skipping")
        return None, 0.0

    token = get_igdb_token()
    safe_name = name.replace('"', '\\"')

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            "https://api.igdb.com/v4/games",
            headers={
                "Client-ID": settings.igdb_client_id,
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain",
            },
            content=f'search "{safe_name}"; fields name,cover.url,cover.image_id; limit 5;',
        )

    if resp.status_code == 401:
        invalidate_igdb_token()
        raise _RateLimited("IGDB-auth")  # wpadnie w retry z backoff

    if resp.status_code == 429:
        raise _RateLimited("IGDB")

    resp.raise_for_status()

    best_score, best_cover = 0.0, None
    for game in resp.json():
        score = _confidence(name, game.get("name", ""))
        if score > best_score:
            best_score = score
            cover = game.get("cover")
            if cover and cover.get("url"):
                url = cover["url"]
                if url.startswith("//"):
                    url = "https:" + url
                url = url.replace("/t_thumb/", "/t_cover_big/")
                best_cover = url

    return best_cover, best_score
```

W `_run_enrichment` zamienić wywołanie:
```python
# było:
rawg_cover, rawg_confidence = await asyncio.to_thread(_rawg_search, name)
# będzie:
rawg_cover, rawg_confidence = await asyncio.to_thread(_igdb_search, name)
```

### 4. Poprawka Steam cover w `_steam_search`

```python
# było:
cover = f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg"
# będzie:
cover = f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/library_600x900.jpg"
```

Uwaga: `library_600x900.jpg` nie istnieje dla starszych gier na Steam. Opcja: HEAD request + fallback do `header.jpg` — dodatkowy request, decyzja do podjęcia.

### 5. `app/core/config.py`

```python
igdb_client_id: str = ""
igdb_client_secret: str = ""
# rawg_api_key: str = ""  ← usunąć
```

### 6. Re-enrichment istniejących rekordów

Gry z `enrichment_status=ENRICHED` i `cover_source=EXTERNAL` mają stare landscape URL z RAWG — worker ich nie tknie. Jednorazowy skrypt `scripts/reset_enrichment.py`:
- Reset do `PENDING`, wyczyść `cover_image_url` dla wszystkich `cover_source=EXTERNAL`
- Enqueue `enrich_game.delay(id)` dla każdego

### 7. Deploy i weryfikacja

`docker compose up --build worker` → ręcznie przetestować na kilku grach przed re-enrichmentem bazy.

---

## Pułapki i zagrożenia

### Token management
- **Race condition:** wiele workerów startuje równocześnie, wszystkie trafiają na pusty Redis i wszystkie fetchują token. Niegroźne funkcjonalnie, ale można zabezpieczyć przez `SET NX` zamiast `SETEX` — tylko pierwszy worker zapisuje.
- **401 mid-flight:** token wygasa między odczytem z Redis a wywołaniem API (rzadkie przy 5-min buforze). Obsługa: `invalidate_igdb_token()` + retry — działa przez istniejący Celery backoff.

### IGDB API
- **`cover` jest polem zagnieżdżonym:** w Apicalypse trzeba użyć `cover.url`, nie samego `cover` (który zwróci samo ID jako integer).
- **Gry bez coveru:** wiele indie/niszowych gier na IGDB nie ma coveru — `cover` pole nieobecne. Gra może dostać `ENRICHED` bez cover URL — lepsze niż `NEEDS_REVIEW`.
- **Apicalypse injection:** jeśli `primary_name` zawiera cudzysłów, query się posypie — escapować przed wstawieniem.
- **`Content-Type`:** IGDB wymaga `text/plain` w nagłówku przy POST z Apicalypse body.
- **Rate limit:** 4 req/sek na token. Przy burst enrich tasków mogą się sypać 429 — backoff obsługuje, ale baza może się wzbogacać wolno.

### `external_api_id` niejednoznaczność
Pole teraz przechowuje albo Steam app_id albo IGDB game_id bez rozróżnienia. Pole nie jest eksponowane w API. Opcja: prefixować `"igdb:1942"` vs `"steam:12345"` — bez migracji schematu.

### Stare rekordy
Bez skryptu re-enrichmentu (krok 6) połowa bazy ma stare landscape covery z RAWG. Krok 6 jest obowiązkowy.

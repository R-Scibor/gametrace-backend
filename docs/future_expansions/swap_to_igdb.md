# Future Expansion: Swap RAWG to IGDB as Primary Enrichment API

**Status:** Not planned — document only
**Priority:** Low (RAWG + Steam covers current needs)
**Trigger:** When RAWG base proves insufficient (missing titles, bad metadata quality, rate limits at scale)

---

## Why IGDB

IGDB (Internet Game Database), owned by Twitch/Amazon, is the most complete publicly available game database. It covers PC, console, mobile, and indie titles with high-quality metadata: canonical names, cover art, genres, release dates, and developer info. It is the industry standard used by most game tracking applications.

RAWG is sufficient for the homelab use case, but IGDB would provide:
- Better coverage of niche and older titles
- Higher quality cover images (official art)
- More reliable canonical names for Confidence Scoring

---

## What Needs to Change

### 1. Authentication — Twitch OAuth

IGDB requires a Twitch `client_credentials` OAuth token. This is the main complexity delta vs RAWG.

**Required env vars (new):**
```
IGDB_CLIENT_ID=<twitch app client id>
IGDB_CLIENT_SECRET=<twitch app client secret>
```

The Worker must obtain and cache a bearer token via:
```
POST https://id.twitch.tv/oauth2/token
  ?client_id=...
  &client_secret=...
  &grant_type=client_credentials
```

Token expires after ~60 days. The Worker needs a **token refresh mechanism**: store the token + expiry in Redis, refresh proactively before expiry (e.g., if `expires_in < 7 days`).

### 2. API Query Change

RAWG endpoint:
```
GET https://api.rawg.io/api/games?search=<process_name>&key=<api_key>
```

IGDB endpoint (POST with body):
```
POST https://api.igdb.com/v4/games
Headers:
  Client-ID: <client_id>
  Authorization: Bearer <token>
Body:
  fields name, slug, cover.url, genres.name, first_release_date;
  search "<process_name>";
  limit 5;
```

IGDB uses a custom query language (Apicalypse). The Worker needs a small query builder or hardcoded query strings.

### 3. Confidence Scoring — No Change Required

The fuzzy matching logic (process name vs `name`/`slug`) stays identical. IGDB returns `name` and `slug` fields the same way RAWG does. The scoring threshold (>85%) does not change.

### 4. Steam Fallback — Stays As-Is

The Steam API fallback layer is independent and does not change. IGDB as primary, Steam as fallback — same two-layer strategy, just primary swapped.

### 5. Cover Images

RAWG returns `background_image` (direct URL).
IGDB returns `cover.url` in the format `//images.igdb.com/igdb/image/upload/t_thumb/...` — needs `https:` prefix and ideally swap `t_thumb` for `t_cover_big` for better resolution.

Worker cover URL normalization:
```python
cover_url = f"https:{igdb_cover_url.replace('t_thumb', 't_cover_big')}"
```

---

## Migration Path (Zero Downtime)

1. Add `IGDB_CLIENT_ID` and `IGDB_CLIENT_SECRET` to docker-compose env
2. Implement token refresh logic in a new `igdb_client.py` module
3. Replace RAWG query in Worker with IGDB query — same interface, different HTTP call
4. Deploy — existing `ENRICHED` records are unaffected (no re-enrichment needed)
5. Games with `NEEDS_REVIEW` or `PENDING` will be picked up by future enrichment tasks naturally

No database schema changes required. No API contract changes. No frontend changes.

---

## Effort Estimate

| Task | Effort |
|---|---|
| Twitch OAuth + token refresh in Redis | Medium |
| IGDB query builder / Apicalypse strings | Small |
| Cover URL normalization | Trivial |
| Update Worker to use new client | Small |
| Tests (unit + integration) | Medium |

Total: a focused afternoon of work once the decision is made.

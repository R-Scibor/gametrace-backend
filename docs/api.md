# API Reference

All endpoints are prefixed `/api/v1/`. Auth uses `Authorization: Bearer <token>` issued by `POST /auth/login`. Pagination uses `?skip=0&limit=20` (default 20, max 100).

For full request/response schemas, hit the FastAPI interactive docs at `http://localhost:8010/docs` once the stack is running.

## HTTP status reference

Grouped by code — see endpoint sections below for path-specific detail. Authed routes return `401` when the bearer token is missing, unknown, or past `expires_at` (expired tokens are deleted on first use).

| Code | When |
|---|---|
| `200` | Successful read or update (`GET`, `PATCH`, `PUT`, `POST /auth/login`, `POST /sessions/{id}/restore`). `GET /games/resolve` also returns `200` with body `null` on miss. `POST /games` returns `200` when the game already exists (deduplication by `igdb_id`). |
| `201` | `POST /sessions` — manual session created. `POST /games` — new game row created (either mode). |
| `204` | Successful delete with no body (`POST /auth/logout`, `DELETE /sessions/{id}`, `DELETE /user/preferences/{game_id}`, `DELETE /notifications/register-token`, `POST /games/{id}/merge/{target_id}`). |
| `400` | Client input rejected — e.g. self-merge (`POST /games/{id}/merge/{target_id}`), empty audio upload (`POST /voice/transcribe`), `redirect_uri` not allowlisted (`POST /auth/discord`). |
| `401` | Invalid or expired bearer token (`get_current_user`), or unknown token on `POST /auth/logout`, or bad/expired Discord code (`POST /auth/discord`). |
| `403` | Bot-managed row — `PATCH` or soft `DELETE` on an `ONGOING` session. Also custom cover upload (`PUT /games/{id}/cover`), which is disabled pending admin controls. |
| `404` | Resource not found or not owned by the caller — user not registered (`POST /auth/login`), session/game missing, game missing on preference upsert. Soft-deleting an already-trashed session also returns `404` (same as not found). |
| `409` | Session time overlap — `POST /sessions`, `PATCH /sessions/{id}`, `POST /sessions/{id}/restore` (body: `{detail: {detail, conflicting_session}}`). |
| `422` | Semantic validation — `end_time` not after `start_time` (`PATCH /sessions/{id}`), `DELETE /sessions/{id}?hard=true` on a non-trashed row, invalid IANA timezone on `PUT /profile/settings` (Pydantic). |
| `500` | Unhandled server error (global handler in `app/main.py`). |
| `502` | Upstream voice failure — OpenAI Whisper or Vertex Gemini error (`POST /voice/transcribe`). Discord OAuth upstream failure (`POST /auth/discord`). IGDB upstream error — non-rate-limit failure (`POST /games/match`). |
| `503` | Voice pipeline not configured — `OPENAI_API_KEY` or `GCP_PROJECT` unset (`POST /voice/transcribe`). IGDB rate-limited or auth expired (`POST /games/match`, `POST /games` with `igdb_id`). |

`GET /health` and `GET /api/v1/health` always return `200`; bot offline or Redis loss is reflected in the JSON payload (`bot.status`: `offline` / `unknown`), not the HTTP status.

## Auth

Two login paths exist: username (`/auth/login`) and Discord OAuth2 (`/auth/discord`). OAuth requires the user to be a member of a configured bot server for presence tracking to produce data; non-members can still log in but receive `needs_server_join: true`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Login by Discord username (user must be pre-registered via `/login` slash command). Issues a session token. Returns `404` with "User not found. Run /login on Discord first." if the user isn't registered. Accepts optional `timezone` (IANA); non-`UTC` values are persisted on the user row. |
| `POST` | `/api/v1/auth/logout` | Invalidate the current bearer token server-side. |
| `POST` | `/api/v1/auth/discord` | Discord OAuth2 login (code + PKCE). Body `{code, code_verifier, redirect_uri}`. Backend exchanges the code server-side, reads `/users/@me`, and issues a session token. Auto-creates the user on first login (verified `discord_id` + `username`). Response includes `needs_server_join: true` when the user is in none of the configured bot servers — the app should prompt them to join so presence tracking works. `400` if `redirect_uri` is not allowlisted; `401` on bad/expired code; `502` if Discord is unreachable. |

Tokens expire after `SESSION_TOKEN_EXPIRE_DAYS` of inactivity (sliding window — every authenticated request bumps `expires_at`). On expiry the token row is deleted and subsequent calls return `401`.

## Profile

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/profile/me` | Current user's profile (`discord_id`, `username`, `timezone`, notification toggles). |
| `PUT` | `/api/v1/profile/settings` | Update timezone and/or notification toggles (`weekly_report_enabled`, `push_enabled`). Partial update — unset fields are left alone. |

## Sessions

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/sessions` | List the caller's sessions across all games (paginated, `?skip=`/`?limit=`). Optional repeated `?status=` filter — e.g. `?status=COMPLETED&status=ERROR` for the Dashboard "Recents" tile. Soft-deleted rows always excluded. |
| `GET` | `/api/v1/sessions/{id}` | Fetch a single session (must belong to the caller). |
| `GET` | `/api/v1/sessions/trash` | List the caller's trashed sessions. Paginated (`?skip=`/`?limit=`; max 100). Ordered by `deleted_at DESC`. Each row includes `purges_at = deleted_at + 7 days` — when the Hard Delete Sweeper will permanently remove it. |
| `POST` | `/api/v1/sessions` | Create a manual session. Saved directly as `COMPLETED`. Server-side overlap check → `409 Conflict` with the conflicting session in the body. |
| `POST` | `/api/v1/sessions/{id}/restore` | Clear `deleted_at` on a trashed session. Status is preserved — restoring an `ERROR` session brings it back as `ERROR` (still needs to be fixed via `PATCH`). For `COMPLETED`, overlap is re-validated; returns `409 Conflict` (`ConflictResponse`: `{detail: {detail, conflicting_session}}`) on conflict. Returns `200 SessionResponse`. |
| `PATCH` | `/api/v1/sessions/{id}` | Edit `end_time` on a `COMPLETED` or `ERROR` session. Sets `source=MANUAL` — times are user-attested after edit. To delete a session (`ERROR` or `COMPLETED`), use `DELETE /api/v1/sessions/{id}`. Editing `ONGOING` is forbidden — those are bot-managed. |
| `DELETE` | `/api/v1/sessions/{id}` | Soft-delete a session. Sets `deleted_at = NOW()`. Allowed on `COMPLETED` and `ERROR`. `403` on `ONGOING` (bot-managed). `404` if already trashed or not found. Returns `204 No Content`. |
| `DELETE` | `/api/v1/sessions/{id}?hard=true` | Permanently remove a trashed session, bypassing the 7-day sweeper. The session must already be soft-deleted — `422` otherwise. Returns `204 No Content`. |

Flicker sessions (`is_flicker=true`) are excluded from `GET /sessions` (list and detail), all stats aggregates, `GET /games`, `GET /games/resolve`, the voice-context library candidates, and overlap validation — exactly like `ERROR` and soft-deleted rows. `GET /sessions/{id}`, `PATCH /sessions/{id}`, and `DELETE /sessions/{id}` return `404` for a flicker row. `is_flicker` is not exposed in `SessionResponse`.

Session state machine — see the [README session state machine](../README.md#session-state-machine).

## Games

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/games` | List games the user has at least one session for. Main library excludes `is_ignored` games and unaccepted `NEEDS_REVIEW` stubs. `?in_library=false` returns the out-of-library tab (ignored ∪ unaccepted `NEEDS_REVIEW`). `?status=NEEDS_REVIEW` returns the Unrecognized inbox (`is_accepted` not true). `?is_ignored=true` returns hidden games only. Optional `?q=<string>` for server-side case-insensitive substring search on `primary_name`. Paginated (`?skip=`/`?limit=`, max 100). Response: `{"total": <int>, "items": [...]}` — each item includes `is_ignored` and `is_accepted`. |
| `POST` | `/api/v1/games` | Create a new global `Game` row or link to an existing one. Two modes (exactly one): **igdb_id mode** — dedupes by `external_api_id` (returns `200` if already known with no IGDB call, else fetches IGDB metadata and creates an `ENRICHED` row → `201`; IGDB miss → `404`; rate-limited → `503`). **Unrecognized mode** (`unrecognized: true` + non-blank `name`) — inserts a `NEEDS_REVIEW` stub with `name` itself stored as a `GameAlias` → `201`. In igdb_id mode, optional `query` is stored as a `GameAlias` for future `/resolve` lookups (ignored in unrecognized mode). Both/neither mode active → `422`. |
| `GET` | `/api/v1/games/resolve?name=<string>` | Map a free-text name to `{game_id, name}` from the user's library (games with at least one non-soft-deleted session — `ERROR` counts, ignored games still resolve). Exact case-insensitive match on `primary_name`, then on `game_aliases.discord_process_name`. Returns `200` with body `null` on miss. Voice-flow prefill. |
| `GET` | `/api/v1/games/suggest?q=<string>` | Fuzzy-search the **global** games catalog (all users' games, not restricted to the caller's library). Pre-filters with ILIKE-any-token on `primary_name` and aliases, scores each candidate with `_confidence()` (max across name + aliases), drops score < 0.3, sorts descending, paginates. Returns `{"total": <int>, "items": [<GameSuggestItem>]}` — each item includes `game_id`, `primary_name`, `cover_image_url`, `enrichment_status`, `score`. `422` if `q` is blank or whitespace. |
| `POST` | `/api/v1/games/match` | Synchronous IGDB candidate search — no DB write. Body: `{"query": "<string>"}`. Returns `list[IGDBCandidateOut]` (`igdb_id`, `name`, `year\|null`, `cover_url\|null`, `score`). Use when suggest has no usable match; pass the chosen `igdb_id` to `POST /games`. `503` rate-limited; `502` other IGDB error. |
| `GET` | `/api/v1/games/{id}/sessions` | Paginated session list for a game. `is_ignored` does not apply — same visibility rules as other session reads (soft-deleted and flicker rows excluded). |
| `GET` | `/api/v1/games/{id}/stats` | Lifetime playtime stats for a single game — `total_seconds` (ONGOING counted live via `now() - start_time`), `session_count`, `first_played`, `last_played`. `404` when the caller has no visible sessions for the game (also covers a non-existent `game_id`). |
| `POST` | `/api/v1/games/{id}/merge/{target_id}` | Transactional merge — reassigns aliases + sessions + preferences from `id` to `target_id`, deletes the source row. `400` on self-merge, `404` if either game is missing. Returns `204`. |
| `PUT` | `/api/v1/games/{id}/cover` | **Disabled** — returns `403`, no upload performed. Custom covers mutated the global `Game` row with no per-user scoping or RBAC, so one user could overwrite shared cover art for everyone. Closed pending admin-only controls; see `docs/roadmap.md` → "Game covers". |

### `GET /games` — library list

Paginated library for the current user. Only games with at least one visible session (`ERROR` counts; flicker and soft-deleted sessions do not). Main list excludes `is_ignored` games and `NEEDS_REVIEW` stubs the user has not accepted (`is_accepted` not `true`). `?in_library=false` returns the out-of-library union (ignored games + unaccepted `NEEDS_REVIEW` stubs, deduped). `?status=NEEDS_REVIEW` returns the Unrecognized inbox only. `?is_ignored=true` returns hidden games only. Ordered by `primary_name` ascending.

**Query parameters**

| Param | Default | Description |
|---|---|---|
| `skip` | `0` | Pagination offset (≥ 0) |
| `limit` | `20` | Page size (1–100) |
| `status` | *(none)* | Filter by `enrichment_status` — e.g. `NEEDS_REVIEW` for the Unrecognized tab, `ENRICHED` for enriched-only |
| `in_library` | *(none)* | `false` — out-of-library tab (ignored ∪ unaccepted `NEEDS_REVIEW`). Omit or `true` — main library behaviour. |
| `is_ignored` | *(none)* | `true` — hidden games only. Takes precedence over `in_library`. Omit or `false` — no extra ignore filter. |
| `q` | *(none)* | Case-insensitive substring search on `primary_name`. Applied server-side before pagination. |

`status`, `in_library`, `is_ignored`, and `q` combine (AND). Reset `skip` to `0` when any filter changes.

**Response — `GameListResponse`**

```json
{ "total": <int>, "items": [<GameResponse>, …] }
```

- `total` — count of games matching the current filters across all pages (use for the Library header, not `items.length`).
- `items` — current page; each row is `GameResponse` (`id`, `primary_name`, `cover_image_url`, `cover_source`, `enrichment_status`, `is_ignored`, `is_accepted`).

**Breaking change:** Previously returned a bare `GameResponse[]`. Mobile clients must read `items` and `total`.

Returns `401` without a valid bearer token.

### `POST /games` — create or link game

Create a new global `Game` row or link to an existing one. Exactly one mode must be active.

**Mode 1 — igdb_id mode** (`igdb_id` set):
1. Deduplication check by `external_api_id` — if a `Game` row with this IGDB id already exists, returns it immediately with `200` (no IGDB call made).
2. Fetches game metadata from IGDB by id.
3. Inserts an `ENRICHED` row with `cover_source=EXTERNAL`, genres, themes, developers, publishers, and `first_release_date` → `201`.

**Mode 2 — unrecognized mode** (`unrecognized: true` + non-blank `name`):
Inserts a `NEEDS_REVIEW` stub using the provided name and creates an alias from `name`. No IGDB call → `201`. Use when the user confirms no IGDB match is correct (obscure indie, non-game activity, etc.).

In igdb_id mode, optional `query` is stored as a `GameAlias` for future `/resolve` lookups. In unrecognized mode `query` is ignored — the `name` itself is stored as the alias.

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `igdb_id` | `int\|null` | Mode 1 | IGDB game identifier |
| `name` | `string\|null` | Mode 2 | Game name for the `NEEDS_REVIEW` stub |
| `unrecognized` | `bool` | Mode 2 | Must be `true` when supplying `name` |
| `query` | `string\|null` | Optional | Alias stored for future `/resolve` lookups (igdb_id mode only; ignored in unrecognized mode) |

**Response — `GameResponse`**

Fields: `id`, `primary_name`, `cover_image_url`, `cover_source`, `enrichment_status`, `is_ignored`, `is_accepted`.

`is_ignored` and `is_accepted` reflect the caller's preference for this game, but `POST /games` always returns them as `false` / `null` — use `GET /games` to read the real preference state.

**Status codes**

| Code | Condition |
|---|---|
| `200` | igdb_id mode — game already exists (deduplication) |
| `201` | New game row created (either mode) |
| `404` | IGDB has no record for the provided `igdb_id` |
| `422` | Both modes active, neither mode active, or `name` is blank with `unrecognized: true` |
| `503` | IGDB rate-limited or auth expired (igdb_id mode only) |

### `GET /games/suggest` — global catalog fuzzy search

Fuzzy-search the global games catalog by name. Scope is **all games** in the DB, not restricted to the caller's library. Intended as the first step in the manual game discovery wizard — before escalating to a live IGDB query.

Pre-filters candidates with ILIKE-any-token (a game is included if any whitespace-split token matches `primary_name` or at least one alias). Scores each candidate with `_confidence()` (max over `primary_name` and all `game_aliases`). Drops score < 0.3. Sorts descending by score. Paginates.

**Query parameters**

| Param | Default | Description |
|---|---|---|
| `q` | *(required)* | Search string — blank or whitespace-only → `422` |
| `skip` | `0` | Pagination offset (≥ 0) |
| `limit` | `20` | Page size (1–100) |

**Response — `GameSuggestResponse`**

```json
{ "total": <int>, "items": [<GameSuggestItem>, …] }
```

- `total` — number of candidates surviving the 0.3 score floor across all pages.
- `items` — current page; each row is `GameSuggestItem`:

| Field | Type | Description |
|---|---|---|
| `game_id` | `int` | Internal game identifier |
| `primary_name` | `string` | Canonical game title |
| `cover_image_url` | `string\|null` | Cover art URL (IGDB-sourced or null) |
| `enrichment_status` | `string` | `ENRICHED`, `NEEDS_REVIEW`, `PENDING`, or `ERROR` |
| `score` | `float` | Relevance score (0.3–1.0) |

Returns `401` without a valid bearer token. Returns `422` if `q` is blank or whitespace.

### `POST /games/match` — IGDB candidate search

Search IGDB synchronously and return ranked candidates for `query`. No DB write — callers display the pick-list and submit the chosen `igdb_id` to `POST /games`. Intended for the manual discovery wizard's "search online" step when the local catalog suggest has no usable match.

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `query` | `string` | Yes (min length 1) | Free-text game name to search IGDB |

**Response — `list[IGDBCandidateOut]`**

| Field | Type | Description |
|---|---|---|
| `igdb_id` | `int` | IGDB game identifier |
| `name` | `string` | Canonical IGDB game title |
| `year` | `int\|null` | First release year (null if unknown) |
| `cover_url` | `string\|null` | IGDB cover art URL |
| `score` | `float` | Ranking confidence score |

Returns `401` without a valid bearer token. Returns `503` when IGDB is rate-limited or the Twitch auth token has expired. Returns `502` on any other IGDB failure (logged server-side).

## Stats

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/stats/summary` | User-selectable window analytical view — `?days=N` (0–365, default 7; **`0` = all-time**). Returns `total_seconds`, full `per_game` breakdown, and `pending_errors`, plus insight fields: `avg_session_seconds` and `longest_session_seconds`/`longest_session_game_id`/`longest_session_game_name` (mean/max over COMPLETED sessions in the window; `0`/`null` when none), `previous_total_seconds` (COMPLETED total over the immediately preceding window of equal length — client computes the % delta), and `new_games_count` (games whose first-ever visible session falls inside the window). In all-time mode (`days=0`) `window_start` is `null` and `previous_total_seconds` is `0` (no preceding period). Reused by the weekly Celery report so push content matches the screen. |
| `GET` | `/api/v1/stats/dashboard` | Polling tile endpoint — `total_seconds_today` (wall-clock midnight in `users.timezone`) + `total_seconds_7d` + `total_seconds_30d`, the active `ONGOING` session brief (with `game_id` + `cover_image_url` for direct render), and `pending_errors`. No per-game breakdown. Designed for 30s polling on the Dashboard tab. The "Recents" list is fetched separately via `GET /api/v1/sessions?status=COMPLETED&status=ERROR`. |
| `GET` | `/api/v1/stats/heatmap` | 7×24 grid of seconds played, bucketed by day-of-week × hour in `users.timezone`. `?days=N` (0–365, default 90; **`0` = all-time**). Always returns 168 cells (zero-filled). Each session's seconds are split across every cell its local interval spans — a 23:00→02:00 session contributes 1h each to the 23:00, 00:00 and 01:00 cells (the latter two rolling onto the next day-of-week). Includes `ONGOING` sessions. |
| `GET` | `/api/v1/stats/streak` | `current_streak` and `longest_streak` (days). A play day is any session whose `start_time` falls on that local calendar date. `current_streak` survives a one-day grace if today is empty but yesterday played. |
| `GET` | `/api/v1/stats/weekly-trend` | Total seconds per week, oldest first. `?weeks=N` (1–52, default 12). Weeks start Monday in user TZ. Always returns N entries (zero-filled). Includes `ONGOING`. |
| `GET` | `/api/v1/stats/genres` | Total seconds aggregated by genre tag (from `games.genres`). Sorted desc. **Sums can exceed total playtime** — multi-genre games count toward each tag. This is "tag exposure," not a partition. `?days=N` (0–365) restricts to a rolling window of the last N days; **`0` or omit = all-time**. |
| `GET` | `/api/v1/stats/themes` | Same shape as `/genres`, over `games.themes`. Same caveat. `?days=N` (0–365; `0` or omit = all-time). |
| `GET` | `/api/v1/stats/companies` | Top developers or publishers by total seconds played. `?role=developer\|publisher` (required), `?limit=N` (1–50, default 10). Returns `name`, `total_seconds`, `game_count`. Tie-break: name asc. `?days=N` (0–365; `0` or omit = all-time). |
| `GET` | `/api/v1/stats/release-years` | Total seconds bucketed by decade of `games.first_release_date` (e.g. `"2010s"`). Games with NULL release date are excluded. Sorted asc. `?days=N` (0–365; `0` or omit = all-time). |

All stats endpoints exclude soft-deleted sessions, `ERROR` sessions, `is_flicker=true` sessions, and `is_ignored` games. `/stats/summary` and `/stats/dashboard` totals count only `COMPLETED` sessions (`duration_seconds`); dashboard also returns the active `ONGOING` session separately. Time-based and tag endpoints (`/heatmap`, `/streak`, `/weekly-trend`, `/genres`, `/themes`, `/companies`, `/release-years`) include `ONGOING` sessions using `now() - start_time` for duration. `GET /games/{id}/stats` follows the same exclusions and ONGOING-live convention, except it does not apply `is_ignored` — the caller navigated to the game by id rather than through a list view, so the real numbers are always returned.

## Voice

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/voice/transcribe` | Multipart audio upload (m4a/wav/mp3/ogg). See pipeline below. Unknown fields come back as `null`. The user always confirms before saving — this endpoint only suggests values. After transcription, the frontend typically calls `GET /games/resolve?name=` to map the spoken game name to a library entry. `503` if `OPENAI_API_KEY` or `GCP_PROJECT` is unset. |

### Transcribe pipeline

```
audio upload
  → OpenAI Whisper (verbose_json — transcript + detected language)
  → build context blocks (app/services/voice_context.py):
      • datetime anchor in users.timezone ("wczoraj", "an hour ago", "just finished" → concrete times)
      • top library candidates via rapidfuzz.partial_ratio over the user's game history
      • Whisper language hint
  → Gemini Flash via Vertex AI (structured output, response_schema)
  → {game, date, start_time, end_time, duration_minutes, raw_transcript}
```

Gemini uses `response_mime_type="application/json"` + `response_schema` — no markdown-fence stripping. Invalid `users.timezone` values fall back to `DEFAULT_TIMEZONE` (env, default `Europe/Warsaw`) with a warning log; users still at the DB default `UTC` also use `DEFAULT_TIMEZONE` for the voice datetime anchor.

## Preferences

| Method | Path | Description |
|---|---|---|
| `PUT` | `/api/v1/user/preferences/{game_id}` | Upsert a per-user preference (`is_ignored`, optional `is_accepted`, `custom_tag`). `is_accepted` only valid on `NEEDS_REVIEW` games — set `true` to accept an unrecognized stub into the main library. Ignored games disappear from `/stats/*` and the main `/games` list; sessions are preserved. |
| `DELETE` | `/api/v1/user/preferences/{game_id}` | Remove the preference row entirely (game returns to default visibility). |

## Notifications

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/notifications/register-token` | Upsert an FCM device token for the current user. `ON CONFLICT (fcm_token)` reassigns the token if the same device logs in as a different user. |
| `DELETE` | `/api/v1/notifications/register-token` | Unregister an FCM token. Idempotent — silent OK if the token isn't on file. |

## Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Plain liveness probe (no auth, no Redis hit). Returns `{"status": "ok"}`. Use this for container/orchestrator health checks. |
| `GET` | `/api/v1/health` | Rich status payload — version metadata + bot liveness. No auth. Safe to poll. Fails-soft on Redis loss (returns `bot.status: "unknown"` instead of erroring). |

`GET /api/v1/health` response shape:

```json
{
  "status": "ok",
  "version": "v1.4.2",
  "commit_sha": "a3f9c1",
  "build_time": "2026-05-01T12:34:56Z",
  "api": { "uptime_seconds": 4821 },
  "bot": {
    "status": "online",
    "uptime_seconds": 84213,
    "last_heartbeat_seconds_ago": 12
  }
}
```

`bot.status` is `"online"` when Redis has a heartbeat key written within the last 90s, `"offline"` if the key is absent or stale, `"unknown"` if Redis is unreachable. The bot writes `bot:started_at` on `on_ready` and refreshes `bot:heartbeat` every 30s with a 90s TTL. Version fields come from Docker build args (`GIT_SHA`, `BUILD_TIME`, `APP_VERSION`) — `"dev"` / `"unknown"` for local builds without those set.

## Static

`/covers/*` is a static-file mount (not an API endpoint) backed by the `covers` Docker volume. It served files written by `PUT /games/{id}/cover`; that write path is now disabled (see Games), so no new files are produced. The mount is retained for the future admin-curated cover feature.

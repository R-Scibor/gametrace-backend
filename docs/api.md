# API Reference

All endpoints are prefixed `/api/v1/`. Auth uses `Authorization: Bearer <token>` issued by `POST /auth/login`. Pagination uses `?skip=0&limit=20` (default 20, max 100).

For full request/response schemas, hit the FastAPI interactive docs at `http://localhost:8010/docs` once the stack is running.

## HTTP status reference

Grouped by code — see endpoint sections below for path-specific detail. Authed routes return `401` when the bearer token is missing, unknown, or past `expires_at` (expired tokens are deleted on first use).

| Code | When |
|---|---|
| `200` | Successful read or update (`GET`, `PATCH`, `PUT`, `POST /auth/login`, `POST /sessions/{id}/restore`). `GET /games/resolve` also returns `200` with body `null` on miss. |
| `201` | `POST /sessions` — manual session created. |
| `204` | Successful delete with no body (`POST /auth/logout`, `DELETE /sessions/{id}`, `DELETE /user/preferences/{game_id}`, `DELETE /notifications/register-token`, `POST /games/{id}/merge/{target_id}`). |
| `400` | Client input rejected — e.g. self-merge (`POST /games/{id}/merge/{target_id}`), empty audio upload (`POST /voice/transcribe`), `redirect_uri` not allowlisted (`POST /auth/discord`). |
| `401` | Invalid or expired bearer token (`get_current_user`), or unknown token on `POST /auth/logout`, or bad/expired Discord code (`POST /auth/discord`). |
| `403` | Bot-managed row — `PATCH` or soft `DELETE` on an `ONGOING` session. Also custom cover upload (`PUT /games/{id}/cover`), which is disabled pending admin controls. |
| `404` | Resource not found or not owned by the caller — user not registered (`POST /auth/login`), session/game missing, game missing on preference upsert. Soft-deleting an already-trashed session also returns `404` (same as not found). |
| `409` | Session time overlap — `POST /sessions`, `PATCH /sessions/{id}`, `POST /sessions/{id}/restore` (body: `{detail: {detail, conflicting_session}}`). |
| `422` | Semantic validation — `end_time` not after `start_time` (`PATCH /sessions/{id}`), `DELETE /sessions/{id}?hard=true` on a non-trashed row, invalid IANA timezone on `PUT /profile/settings` (Pydantic). |
| `500` | Unhandled server error (global handler in `app/main.py`). |
| `502` | Upstream voice failure — OpenAI Whisper or Vertex Gemini error (`POST /voice/transcribe`). Discord OAuth upstream failure (`POST /auth/discord`). |
| `503` | Voice pipeline not configured — `OPENAI_API_KEY` or `GCP_PROJECT` unset (`POST /voice/transcribe`). |

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
| `GET` | `/api/v1/games` | List games the user has at least one session for. Excludes ignored games. Optional `?status=NEEDS_REVIEW` filter for the Unrecognized tab. Paginated. |
| `GET` | `/api/v1/games/resolve?name=<string>` | Map a free-text name to `{game_id, name}` from the user's library (games with at least one non-soft-deleted session — `ERROR` counts, ignored games still resolve). Exact case-insensitive match on `primary_name`, then on `game_aliases.discord_process_name`. Returns `200` with body `null` on miss. Voice-flow prefill. |
| `GET` | `/api/v1/games/{id}/sessions` | Paginated session list for a game. Returns `[]` if the user has marked the game as ignored. |
| `GET` | `/api/v1/games/{id}/stats` | Lifetime playtime stats for a single game — `total_seconds` (ONGOING counted live via `now() - start_time`), `session_count`, `first_played`, `last_played`. `404` when the caller has no visible sessions for the game (also covers a non-existent `game_id`). |
| `POST` | `/api/v1/games/{id}/merge/{target_id}` | Transactional merge — reassigns aliases + sessions + preferences from `id` to `target_id`, deletes the source row. `400` on self-merge, `404` if either game is missing. Returns `204`. |
| `PUT` | `/api/v1/games/{id}/cover` | **Disabled** — returns `403`, no upload performed. Custom covers mutated the global `Game` row with no per-user scoping or RBAC, so one user could overwrite shared cover art for everyone. Closed pending admin-only controls; see `docs/roadmap.md` → "Game covers". |

## Stats

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/stats/summary` | User-selectable window analytical view — `?days=N` (1–365, default 7). Returns `total_seconds`, full `per_game` breakdown, and `pending_errors`. Reused by the weekly Celery report so push content matches the screen. |
| `GET` | `/api/v1/stats/dashboard` | Polling tile endpoint — `total_seconds_today` (wall-clock midnight in `users.timezone`) + `total_seconds_7d` + `total_seconds_30d`, the active `ONGOING` session brief (with `game_id` + `cover_image_url` for direct render), and `pending_errors`. No per-game breakdown. Designed for 30s polling on the Dashboard tab. The "Recents" list is fetched separately via `GET /api/v1/sessions?status=COMPLETED&status=ERROR`. |
| `GET` | `/api/v1/stats/heatmap` | 7×24 grid of seconds played, bucketed by day-of-week × hour in `users.timezone`. `?days=N` (1–365, default 90). Always returns 168 cells (zero-filled). Sessions bucketed by `start_time` only — not split across hour boundaries. Includes `ONGOING` sessions. |
| `GET` | `/api/v1/stats/streak` | `current_streak` and `longest_streak` (days). A play day is any session whose `start_time` falls on that local calendar date. `current_streak` survives a one-day grace if today is empty but yesterday played. |
| `GET` | `/api/v1/stats/weekly-trend` | Total seconds per week, oldest first. `?weeks=N` (1–52, default 12). Weeks start Monday in user TZ. Always returns N entries (zero-filled). Includes `ONGOING`. |
| `GET` | `/api/v1/stats/genres` | Total seconds aggregated by genre tag (from `games.genres`). Sorted desc. **Sums can exceed total playtime** — multi-genre games count toward each tag. This is "tag exposure," not a partition. |
| `GET` | `/api/v1/stats/themes` | Same shape as `/genres`, over `games.themes`. Same caveat. |
| `GET` | `/api/v1/stats/companies` | Top developers or publishers by total seconds played. `?role=developer\|publisher` (required), `?limit=N` (1–50, default 10). Returns `name`, `total_seconds`, `game_count`. Tie-break: name asc. |
| `GET` | `/api/v1/stats/release-years` | Total seconds bucketed by decade of `games.first_release_date` (e.g. `"2010s"`). Games with NULL release date are excluded. Sorted asc. |

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
| `PUT` | `/api/v1/user/preferences/{game_id}` | Upsert a per-user preference for a game (`is_ignored`, `custom_tag`). Ignored games disappear from `/stats/*` and `/games`, but the underlying sessions are preserved. |
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

# API Reference

All endpoints are prefixed `/api/v1/`. Auth uses `Authorization: Bearer <token>` issued by `POST /auth/login`. Pagination uses `?skip=0&limit=20` (default 20, max 100).

For full request/response schemas, hit the FastAPI interactive docs at `http://localhost:8010/docs` once the stack is running.

## Auth

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Login by Discord username (user must be pre-registered via `/login` slash command). Issues a session token. Returns `404` with "Run /login on Discord first." if the user isn't registered. |
| `POST` | `/api/v1/auth/logout` | Invalidate the current bearer token server-side. |

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
| `POST` | `/api/v1/sessions` | Create a manual session. Saved directly as `COMPLETED`. Server-side overlap check → `409 Conflict` with the conflicting session in the body. |
| `PATCH` | `/api/v1/sessions/{id}` | Edit `end_time` (any `COMPLETED` or `ERROR` session) or `discard: true` (only `ERROR`, soft-deletes the row). Editing `ONGOING` is forbidden — those are bot-managed. |

Session state machine — see the README's "State machine" section.

## Games

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/games` | List games the user has at least one session for. Excludes ignored games. Optional `?status=NEEDS_REVIEW` filter for the Unrecognized tab. Paginated. |
| `GET` | `/api/v1/games/{id}/sessions` | Paginated session list for a game. Returns `[]` if the user has marked the game as ignored. |
| `POST` | `/api/v1/games/{id}/merge/{target_id}` | Transactional merge — reassigns aliases + sessions + preferences from `id` to `target_id`, deletes the source row. `400` on self-merge, `404` if either game is missing. Returns `204`. |
| `PUT` | `/api/v1/games/{id}/cover` | Upload a custom cover (Base64 + extension). Saves to the `covers` Docker volume, sets `cover_source=CUSTOM`. The Celery enrichment worker will not overwrite a CUSTOM cover. Allowed extensions: `jpg`, `jpeg`, `png`, `webp`. |

## Stats

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/stats/summary` | User-selectable window analytical view — `?days=N` (1–365, default 7). Returns `total_seconds`, full `per_game` breakdown, and `pending_errors`. Reused by the weekly Celery report so push content matches the screen. |
| `GET` | `/api/v1/stats/dashboard` | Polling tile endpoint — `total_seconds_today` (wall-clock midnight in `users.timezone`) + `total_seconds_7d` + `total_seconds_30d`, the active `ONGOING` session brief (with `game_id` + `cover_image_url` for direct render), and `pending_errors`. No per-game breakdown. Designed for 30s polling on the Dashboard tab. The "Recents" list is fetched separately via `GET /api/v1/sessions?status=COMPLETED&status=ERROR`. |

Both endpoints exclude soft-deleted sessions, `ERROR` sessions, and `is_ignored` games from the totals.

## Voice

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/voice/transcribe` | Multipart audio upload (m4a/wav/mp3/ogg). Pipeline: OpenAI Whisper (STT) → Gemini Flash via Vertex AI (transcript → `{game, date, start_time, end_time, duration_minutes}`). Unknown fields come back as `null`. The user always confirms before saving — this endpoint only suggests values. `503` if `OPENAI_API_KEY` or `GCP_PROJECT` is unset. |

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

`/covers/*` is a static-file mount (not an API endpoint) backed by the `covers` Docker volume. URLs returned by `PUT /games/{id}/cover` resolve to files served from this mount.

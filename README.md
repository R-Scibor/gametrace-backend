# GameTrace Backend

Distributed game-time tracking system. A Discord bot detects game activity via `on_presence_update` and logs sessions to PostgreSQL. A FastAPI backend exposes a REST API consumed by a React Native mobile app. Celery workers handle async data enrichment.

## Stack

| Component | Technology |
|-----------|-----------|
| API | Python 3.11 + FastAPI |
| Database | PostgreSQL 15 + Alembic |
| Task queue | Celery + Redis |
| Bot | discord.py |
| Push notifications | Firebase Cloud Messaging |
| Voice pipeline | OpenAI Whisper + Gemini Flash via Vertex AI |
| Game enrichment | IGDB (primary, via Twitch OAuth) + Steam Store (fallback) |

## Quick start

```bash
cp example.env .env
# fill in .env values — see example.env for the full list
mkdir -p credentials
# place gcp-sa.json (Vertex ADC) and firebase-cred.json (FCM) — see example.env
docker compose up
```

API at `http://localhost:8010`. Interactive Swagger docs at `http://localhost:8010/docs`.

**Required secrets:** `DISCORD_BOT_TOKEN`, `IGDB_CLIENT_ID` / `IGDB_CLIENT_SECRET` (Twitch dev console). **Discord OAuth2 login** additionally needs `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_OAUTH_REDIRECT_URIS`, and `DISCORD_GUILD_IDS` — register the same redirect URIs in the Discord Developer Portal → OAuth2 → Redirects. **Voice pipeline** additionally needs `OPENAI_API_KEY`, `GCP_PROJECT`, and a mounted GCP service-account JSON. **Push notifications** need `credentials/firebase-cred.json`. Features with missing config return `503` (voice) or silently skip (FCM, Sentry).

## Services

```
db            PostgreSQL 15
redis         Message broker for Celery
alembic_init  Runs migrations before API starts (Init Container pattern)
api           FastAPI — REST API for the mobile app
bot           Discord bot — presence tracking
worker        Celery worker — async game metadata enrichment
beat          Celery beat — scheduled tasks (weekly report, hard-delete sweeper)
flower        Celery monitor (port 5555, internal)
```

## User onboarding

Two login paths are available:

- **Username login (`POST /auth/login`)** — requires pre-registration via the `/login` slash command on Discord. The bot registers the user in the database; the mobile app then logs in by Discord username.
- **Discord OAuth2 (`POST /auth/discord`)** — self-provisioning. The mobile app completes the OAuth2 flow (code + PKCE) and the backend creates the user on first login. Requires `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_OAUTH_REDIRECT_URIS`, and `DISCORD_GUILD_IDS` in `.env`. Non-members of the configured bot servers can still log in but receive `needs_server_join: true` — presence tracking will not produce data until they join.

## API

All endpoints are prefixed `/api/v1/`. Auth is `Authorization: Bearer <token>`. Token expires after 30 days of inactivity (sliding window — every authed call bumps the expiry).

Full endpoint reference: **[docs/api.md](docs/api.md)**. Live schemas: `http://localhost:8010/docs`.

Dashboard polling uses `GET /api/v1/stats/dashboard` (tiles + `pending_errors`); the analytical breakdown is `GET /api/v1/stats/summary?days=N`. Additional analytics endpoints (`heatmap`, `streak`, `weekly-trend`, `genres`, `themes`, `companies`, `release-years`) are documented in `docs/api.md`. Bot liveness: `GET /api/v1/health`.

## Session state machine

Bot-sourced sessions (`source=BOT`):

```
ONGOING   ──► COMPLETED         (bot detects game closed)
ONGOING   ──► ERROR             (Self-Healing on bot restart: different game, or >12h elapsed)
ERROR     ──► COMPLETED         (user supplies end_time via PATCH /sessions/{id}; source → MANUAL)
ERROR     ──► soft-deleted      (user discards via DELETE /sessions/{id})
COMPLETED ──► soft-deleted      (user deletes via DELETE /sessions/{id})
COMPLETED ──► COMPLETED         (user edits end_time; must remain > start_time; source → MANUAL)
soft-deleted ──► COMPLETED/ERROR (user restores via POST /sessions/{id}/restore; status preserved)
```

Manual sessions (`source=MANUAL`) skip the cycle and are saved directly as `COMPLETED`. `ERROR` sessions are excluded from all aggregates until resolved. `ONGOING` sessions cannot be soft-deleted directly — only the bot owns those rows.

Discord presence flicker (brief dropouts mid-session) is handled by the bot: short BOT sessions are suppressed at close and stitched on same-game resume within a configurable window. See [docs/bot.md](docs/bot.md#flicker-suppression-and-stitch-resume).

Trashed sessions appear in `GET /api/v1/sessions/trash` and are permanently purged by the Hard Delete Sweeper after 7 days. Use `DELETE /sessions/{id}?hard=true` to remove a trashed session immediately.

## Database migrations

```bash
# Apply all pending migrations
docker compose run --rm api alembic upgrade head

# Create a new migration after changing a model
docker compose run --rm api alembic revision --autogenerate -m "description"

# Rollback one migration
docker compose run --rm api alembic downgrade -1
```

Migrations also run automatically via the `alembic_init` init container before the API starts.

## Backups

Local backups run daily at 03:00 via host cron. Dumps land on a host path outside Docker named volumes — default `/data/gametrace-backups` on the homelab `/data` disk (`sda1`), separate from the NVMe where `/var/lib/docker` lives. Offsite cloud copy is deferred to [pre-release hardening](docs/roadmap.md#offsite-database-backups).

```bash
# One-shot backup (PostgreSQL custom dump + optional covers tar)
docker compose --profile backup run --rm backup

# Restore from latest symlinks (destructive — overwrites current DB + covers)
docker compose --profile restore run --rm restore

# Restore a specific dump
docker compose --profile restore run --rm restore \
  /backups/gametrace-2026-06-28T120000Z.dump \
  /backups/covers-2026-06-28T120000Z.tar.gz
```

Cron is installed from `scripts/backup.cron` (daily 03:00, logs to `/data/gametrace-backups/cron.log`). Re-install after cloning:

```bash
crontab scripts/backup.cron
```

Retention defaults to 7 days (`BACKUP_RETENTION_DAYS`). Optional mirror: set `BACKUP_MIRROR_DIR` and add a second bind mount for `/mirror` in `docker-compose.yml` (e.g. `/data/bulk/gametrace-backups` on `sdb1`).

## Observability

Two optional integrations, both off by default:

- **Sentry** — set `SENTRY_DSN` in `.env` and api / bot / worker / beat will start reporting unhandled exceptions, tagged with `component={api,bot,celery}`. Bearer tokens in `Authorization` headers and `?token=` query strings are scrubbed before send. Empty DSN keeps the SDK uninitialised — zero overhead.
- **Flower** — Celery queue monitor on port 5555 inside the docker network. Set `FLOWER_BASIC_AUTH=user:pass` in `.env` to require auth. Flower has no read-only mode, so do not expose it publicly without auth — route through Nginx Proxy Manager and gate on the LAN if you want a browser view.
- **Health** — `GET /health` for container liveness; `GET /api/v1/health` for version metadata and bot online/offline status (Redis heartbeat). Optional build args `GIT_SHA`, `BUILD_TIME`, `APP_VERSION` in `docker-compose.yml` populate version fields.

## Discord Developer Portal prerequisites

Before first run, in the [Discord Developer Portal](https://discord.com/developers/applications):

1. **Bot → Privileged Gateway Intents:** enable `PRESENCE INTENT` and `SERVER MEMBERS INTENT`.
2. **OAuth2 → URL Generator:** select scopes `bot` **and** `applications.commands` — both are required. Regenerate the invite URL and re-invite the bot if it was previously added without `applications.commands`.

## Development commands

```bash
# Start all services with hot-reload
docker compose up

# Run tests
docker compose run --rm api pytest

# Add a user directly (dev shortcut, bypasses /login)
docker exec -it gametrace_db psql -U gametrace_user -d gametrace_db \
  -c "INSERT INTO users (discord_id, username) VALUES ('<id>', '<username>');"
```

## Docs

| Document | Description |
|----------|-------------|
| [docs/api.md](docs/api.md) | Full endpoint reference |
| [docs/bot.md](docs/bot.md) | Bot architecture — presence tracking, `/login` flow, Self-Healing |
| [docs/schema.md](docs/schema.md) | Database schema — tables, relationships, indexes, invariants |
| [docs/game-matching.md](docs/game-matching.md) | Game-name matching pipeline — sanitization, WRatio, number guard, IGDB alternative names |
| [docs/roadmap.md](docs/roadmap.md) | Future plans — auth, voice pipeline, hardening, scale |

## Future plans

High-level — see [docs/roadmap.md](docs/roadmap.md) for full context.

- **RBAC on destructive ops** — merge and cover endpoints need admin/owner controls so one user cannot affect another's data.
- **Pre-release hardening** — request body size cap (nginx), rate-limit on `/voice/transcribe` (per-user, Redis-backed), MIME sniffing on cover + audio uploads.
- **Voice pipeline robustness** — regex fallback when Vertex AI is unavailable, bring-your-own-key (user-supplied GCP / OpenAI), self-hosted Whisper option.
- **Timezone-aware weekly reports** — hourly fan-out so each user gets the digest at their local Monday 09:00, not UTC's.
- **Scale: range-partition `game_sessions`** by month when the table crosses ~10M rows or `/stats/summary` slows down.

## License

[MIT](LICENSE) — © 2026 R-Scibor

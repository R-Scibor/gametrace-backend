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
docker compose up
```

API at `http://localhost:8010`. Interactive Swagger docs at `http://localhost:8010/docs`.

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

1. User runs `/login` on any Discord server where the bot is present.
2. Bot registers them in the database (captures Discord ID and username automatically).
3. User opens the mobile app and logs in with their Discord username.

## API

All endpoints are prefixed `/api/v1/`. Auth is `Authorization: Bearer <token>`. Token expires after 30 days of inactivity (sliding window — every authed call bumps the expiry).

Full endpoint reference: **[docs/api.md](docs/api.md)**. Live schemas: `http://localhost:8010/docs`.

## Session state machine

Bot-sourced sessions (`source=BOT`):

```
ONGOING ──► COMPLETED         (bot detects game closed)
ONGOING ──► ERROR             (Self-Healing on bot restart: different game, or >12h elapsed)
ERROR   ──► COMPLETED         (user supplies end_time via PATCH /sessions/{id})
ERROR   ──► soft-deleted      (user discards via PATCH /sessions/{id} with discard=true)
COMPLETED ──► COMPLETED       (user edits end_time; must remain > start_time)
```

Manual sessions (`source=MANUAL`) skip the cycle and are saved directly as `COMPLETED`. `ERROR` sessions are excluded from all aggregates until resolved. `ONGOING` sessions cannot be soft-deleted directly — only the bot owns those rows.

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

## Observability

Two optional integrations, both off by default:

- **Sentry** — set `SENTRY_DSN` in `.env` and api / bot / worker / beat will start reporting unhandled exceptions, tagged with `component={api,bot,celery}`. Bearer tokens in `Authorization` headers and `?token=` query strings are scrubbed before send. Empty DSN keeps the SDK uninitialised — zero overhead.
- **Flower** — Celery queue monitor on port 5555 inside the docker network. Set `FLOWER_BASIC_AUTH=user:pass` in `.env` to require auth. Flower has no read-only mode, so do not expose it publicly without auth — route through Nginx Proxy Manager and gate on the LAN if you want a browser view.

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

- **Discord OAuth2 login** — replace username-based auth; Discord owns the auth surface, closes the rate-limit / enumeration gap as a side effect.
- **Pre-release hardening** — request body size cap (nginx), rate-limit on `/voice/transcribe` (per-user, Redis-backed), MIME sniffing on cover + audio uploads.
- **Voice pipeline robustness** — regex fallback when Vertex AI is unavailable, bring-your-own-key (user-supplied GCP / OpenAI), self-hosted Whisper option.
- **Timezone-aware weekly reports** — hourly fan-out so each user gets the digest at their local Monday 09:00, not UTC's.
- **Scale: range-partition `game_sessions`** by month when the table crosses ~10M rows or `/stats/summary` slows down.
- **Bot flicker debounce** — coalesce `ONGOING → COMPLETED → ONGOING` transitions shorter than ~2 minutes into a single continuous session.

## License

MIT — © 2026 R-Scibor

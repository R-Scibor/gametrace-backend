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
| Voice pipeline | OpenAI Whisper + Gemini Flash |

## Quick start

```bash
cp example.env .env
# fill in .env values
docker-compose up
```

API available at `http://localhost:8010`. Interactive docs at `http://localhost:8010/docs`.

## Services

```
db            PostgreSQL 15
redis         Message broker for Celery
alembic_init  Runs migrations before API starts (Init Container pattern)
api           FastAPI — REST API for the mobile app
bot           Discord bot — presence tracking
worker        Celery worker — async game metadata enrichment
beat          Celery beat — scheduled tasks (cleanup, reports)
```

## User onboarding

1. User runs `/login` on any Discord server where the bot is present
2. Bot registers them in the database (captures Discord ID and username automatically)
3. User opens the mobile app and logs in with their Discord username

## API

All endpoints are prefixed `/api/v1/`. Auth uses `Authorization: Bearer <token>`.

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/auth/login` | Login by Discord username, returns session token |
| `POST` | `/api/v1/auth/logout` | Invalidate token |

Token expires after 30 days of inactivity (sliding window).

### Sessions
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/sessions` | Add a manual session (COMPLETED, overlap check → 409) |
| `PATCH` | `/api/v1/sessions/{id}` | Edit end_time/notes or discard an ERROR session |
| `GET` | `/api/v1/sessions/game/{game_id}` | Paginated session list for a game (`?skip=0&limit=20`) |

Session state machine (bot-sourced): `ONGOING → COMPLETED`, `ONGOING → ERROR`, `ERROR → COMPLETED`, `ERROR → soft-delete`. Manual sessions are saved directly as `COMPLETED`.

### Stats
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/stats/summary` | Aggregated stats + pending errors (`?days=7`, max 365) |

`/stats/summary` response includes `total_seconds`, per-game breakdown, and `pending_errors` — a list of all unresolved ERROR sessions.

### Other
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |

## Database migrations

```bash
# Apply all pending migrations
docker-compose run --rm api alembic upgrade head

# Create a new migration after changing a model
docker-compose run --rm api alembic revision --autogenerate -m "description"

# Rollback one migration
docker-compose run --rm api alembic downgrade -1
```

## Discord Developer Portal prerequisites

Before first run, in the [Discord Developer Portal](https://discord.com/developers/applications):

1. **Bot → Privileged Gateway Intents:** enable `PRESENCE INTENT` and `SERVER MEMBERS INTENT`
2. **OAuth2 → URL Generator:** select scopes `bot` **and** `applications.commands` — both are required. Regenerate the invite URL and re-invite the bot if it was previously added without `applications.commands`.

## Development commands

```bash
# Start all services with hot-reload
docker-compose up

# Run tests
docker-compose run --rm api pytest

# Add a user directly (dev shortcut, bypasses /login)
docker exec -it gametrace_db psql -U gametrace_user -d gametrace_db \
  -c "INSERT INTO users (discord_id, username) VALUES ('<id>', '<username>');"
```

## Project structure

```
app/
├── main.py                  # FastAPI entry point
├── core/
│   ├── config.py            # Settings from .env (pydantic-settings)
│   ├── database.py          # SQLAlchemy async engine + get_db dependency
│   └── celery_app.py        # Celery instance
├── models/                  # SQLAlchemy ORM models
│   ├── user.py              # User, UserAuthToken, UserDevice
│   ├── game.py              # Game, GameAlias, UserGamePreference
│   └── session.py           # GameSession, DailyUserStat
├── schemas/
│   ├── auth.py              # Pydantic: LoginRequest, LoginResponse
│   ├── session.py           # Pydantic: SessionCreate, SessionPatch, SessionResponse
│   └── stats.py             # Pydantic: StatsSummaryResponse
├── api/v1/
│   ├── router.py            # Main v1 router
│   └── endpoints/
│       ├── auth.py          # Auth endpoints + get_current_user dependency
│       ├── sessions.py      # POST/PATCH /sessions, GET /sessions/game/{id}
│       └── stats.py         # GET /stats/summary?days=N
├── bot/
│   ├── main.py              # Discord client, /login slash command, on_presence_update
│   ├── session_manager.py   # DB operations for the bot
│   └── self_healing.py      # Reconciliation of ONGOING sessions on restart
└── tasks/
    └── enrichment.py        # Celery task — game metadata enrichment (stub, Phase 3)
alembic/
└── versions/
    ├── 0001_initial_schema.py
    └── 0002_unique_username.py
```

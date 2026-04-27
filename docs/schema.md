# Database Schema

Source of truth: SQLAlchemy models in `app/models/` and Alembic migrations in `alembic/versions/`.

Seven tables total. All timestamps are stored as `TIMESTAMP WITH TIME ZONE` in UTC. Soft-delete is via `deleted_at` columns where applicable.

## Tables

### `users`

The root identity table. Keyed on Discord ID (a snowflake — string, not integer).

| Column | Type | Notes |
|---|---|---|
| `discord_id` | `VARCHAR(32)` | Primary key |
| `username` | `VARCHAR(100)` | Unique. Synced from Discord on every `/login`. |
| `timezone` | `VARCHAR(64)` | IANA tz name. Default `UTC`. Updated on mobile login from device OS, or manually via `PUT /profile/settings`. |
| `weekly_report_enabled` | `BOOLEAN` | Default `true`. Gates the weekly Celery push. |
| `push_enabled` | `BOOLEAN` | Default `true`. Master switch for any push notification. |
| `created_at` | `TIMESTAMPTZ` | |

A user must exist here before the bot will track their presence — the bot is intentionally blind to non-registered users.

### `user_auth_tokens`

Bearer tokens issued by `POST /auth/login`. One row per active session.

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `user_id` | `VARCHAR(32)` | FK → `users.discord_id`, `ON DELETE CASCADE` |
| `token` | `VARCHAR(64)` | Unique, indexed. 32 random bytes hex-encoded (`secrets.token_hex(32)`). |
| `created_at` | `TIMESTAMPTZ` | |
| `last_active` | `TIMESTAMPTZ` | Bumped on every authenticated request |
| `expires_at` | `TIMESTAMPTZ` | Sliding window — bumped to `NOW() + SESSION_TOKEN_EXPIRE_DAYS` on every authed request |

Expired tokens are deleted on the next request that hits them (lazy cleanup).

### `user_devices`

FCM tokens for push delivery. Multiple rows per user (one per device).

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `user_id` | `VARCHAR(32)` | FK → `users.discord_id`, `ON DELETE CASCADE`, indexed |
| `fcm_token` | `VARCHAR(512)` | Unique. ON CONFLICT update on `register-token` reassigns the device between users when needed. |
| `device_type` | `VARCHAR(32)` | Free-form (`ios`, `android`, etc.) |
| `created_at`, `last_active` | `TIMESTAMPTZ` | The hard-delete sweeper purges rows where `last_active < NOW() - 6 months` |

### `games`

Game catalog. Created as stubs by the bot, enriched asynchronously by the Celery worker.

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `primary_name` | `VARCHAR(256)` | The canonical name. For new stubs this is just the Discord process name; enrichment overwrites it with the IGDB/Steam canonical name. |
| `external_api_id` | `VARCHAR(64)` | Optional — IGDB game ID or Steam AppID, prefixed by source. |
| `cover_image_url` | `VARCHAR(512)` | Optional. |
| `cover_source` | `ENUM('EXTERNAL', 'CUSTOM')` | If `CUSTOM`, the enrichment worker will not overwrite `cover_image_url`. Set by `PUT /games/{id}/cover`. |
| `enrichment_status` | `ENUM('PENDING', 'ENRICHED', 'NEEDS_REVIEW')` | `PENDING` on insert; `ENRICHED` when match confidence ≥ 85%; `NEEDS_REVIEW` when no source crossed the threshold. |

### `game_aliases`

Maps Discord process names (what the bot sees on `on_presence_update`) to game records. One game can have many aliases (e.g. a game changes its rich-presence string between versions, or a duplicate game gets merged).

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `game_id` | `INTEGER` | FK → `games.id`, `ON DELETE CASCADE` |
| `discord_process_name` | `VARCHAR(256)` | Unique, indexed. The exact string the bot received. |

The bot looks up via `discord_process_name` first; if no alias matches, it creates a new stub `Game` and a corresponding alias in one transaction.

### `game_sessions`

The core table. State machine described in the [README](../README.md#session-state-machine).

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `user_id` | `VARCHAR(32)` | FK → `users.discord_id`, `ON DELETE CASCADE` |
| `game_id` | `INTEGER` | FK → `games.id` (no cascade — sessions outlive games via merges) |
| `start_time` | `TIMESTAMPTZ` | Always set |
| `end_time` | `TIMESTAMPTZ` | NULL while `ONGOING`. |
| `duration_seconds` | `INTEGER` | NULL while `ONGOING`. Set to `(end_time - start_time)` on transition out of ONGOING. |
| `status` | `ENUM('ONGOING', 'COMPLETED', 'ERROR')` | |
| `source` | `ENUM('BOT', 'MANUAL')` | `BOT` rows go through the state machine; `MANUAL` rows are inserted directly as `COMPLETED`. |
| `notes` | `TEXT` | System-owned — written by Self-Healing as the human-readable reason an ERROR occurred. Read-only via the API. |
| `deleted_at` | `TIMESTAMPTZ` | NULL = live. Set when a user discards an ERROR session. The hard-delete sweeper removes rows where `deleted_at < NOW() - 7 days`. |
| `created_at` | `TIMESTAMPTZ` | |

**Indexes:**

- `ix_game_sessions_user_id_start_time` — composite btree on `(user_id, start_time)`. Used by overlap validation in `POST/PATCH /sessions` and by `/stats/summary` window aggregation. Migration `0004`.
- `ix_game_sessions_deleted_at_partial` — partial btree on `deleted_at WHERE deleted_at IS NOT NULL`. Used by the hard-delete sweeper. Migration `0005`.

**Invariants:**

- Only one `ONGOING` session per user at any time. Enforced by bot logic, not a DB constraint.
- `ERROR` sessions are excluded from all aggregates (`/stats/summary`, `/stats/dashboard`, weekly report) until resolved.
- `ONGOING` sessions cannot be soft-deleted directly — only the bot owns those rows.
- `cover_source=CUSTOM` overrides `enrichment_status` for the cover field — the worker skips `cover_image_url` updates on those games.

### `user_game_preferences`

Per-user metadata layered on top of the global `games` catalog. Not all users have a preference row for every game — absence means defaults.

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `user_id` | `VARCHAR(32)` | FK → `users.discord_id`, `ON DELETE CASCADE` |
| `game_id` | `INTEGER` | FK → `games.id`, `ON DELETE CASCADE` |
| `is_ignored` | `BOOLEAN` | Default `false`. Ignored games are filtered out at the SELECT layer in `/stats/*` and `/games`. Sessions are preserved — set back to `false` and the history reappears. |
| `custom_tag` | `VARCHAR(64)` | Optional user-supplied label. |

Unique constraint on `(user_id, game_id)`. The merge endpoint (`POST /games/{id}/merge/{target_id}`) reassigns these rows transactionally, dropping conflicts where the target already has a preference for the same user.

## Relationships at a glance

```
users ─┬── user_auth_tokens   (1:N, cascade)
       ├── user_devices       (1:N, cascade)
       └── game_sessions      (1:N, cascade)

games ─┬── game_aliases       (1:N, cascade)
       ├── game_sessions      (1:N, no cascade)
       └── user_game_preferences  (M:N pivot with users, cascade both sides)
```

The only "hard" link is `game_sessions.game_id` — no cascade because games can be merged (the merge transaction reassigns sessions before deleting the source row, so the FK is never violated).

## Migrations

| File | Purpose |
|---|---|
| `0001_initial_schema.py` | All seven tables and their constraints |
| `0002_unique_username.py` | Adds `UNIQUE` on `users.username` |
| `0003_user_notif_prefs_and_device_created_at.py` | Adds `weekly_report_enabled`, `push_enabled` to `users`; `created_at` to `user_devices` |
| `0004_game_sessions_user_start_index.py` | Composite index for overlap and stats queries |
| `0005_game_sessions_deleted_at_partial_index.py` | Partial index for the hard-delete sweeper |
| `0006_drop_daily_user_stats.py` | Removed an earlier rollup table — sessions are kept raw indefinitely. Range-partitioning by month is on the [roadmap](roadmap.md#scale) for when the table grows past ~10M rows. |

# Database Schema

Source of truth: SQLAlchemy models in `app/models/` and Alembic migrations in `alembic/versions/`.

Eleven tables total. All timestamps are stored as `TIMESTAMP WITH TIME ZONE` in UTC. Soft-delete is via `deleted_at` columns where applicable.

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
| `is_admin` | `BOOLEAN` | Default `false`. Gates admin-only endpoints. Not settable via API — operators promote users manually. |
| `created_at` | `TIMESTAMPTZ` | |
| `deletion_requested_at` | `TIMESTAMPTZ` | NULL = no pending deletion. Set when the user requests account deletion; starts the grace period. |
| `purge_at` | `TIMESTAMPTZ` | NULL = no pending deletion. `deletion_requested_at` + `ACCOUNT_DELETION_GRACE_DAYS` (default 7). The account becomes eligible for permanent purge at this timestamp. |

A user must exist here before the bot will track their presence — the bot is intentionally blind to non-registered users.

Migration `0020` inserts one reserved row: `discord_id='1'` (a value far below the Discord snowflake range, so it can never collide with a real account), username `GameTrace Reviewer`, timezone `Europe/Warsaw`, `language='en'`, `is_admin=false`. This is the account the permanent Google Play reviewer login code resolves to (see [api.md](api.md#permanent-reviewer-login)). The insert is `ON CONFLICT (discord_id) DO NOTHING`, and `tasks.reset_demo_account` (below) upserts this row back to the same canonical values every night, so it self-heals if ever deleted.

**Indexes:**

- `ix_users_purge_at_partial` — partial btree on `purge_at WHERE purge_at IS NOT NULL`. Migration `0018`.

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
| `cover_source` | `ENUM('EXTERNAL', 'CUSTOM')` | If `CUSTOM`, the enrichment worker will not overwrite `cover_image_url`. Set by `PUT /api/v1/admin/games/{id}/cover` (admin-only). |
| `enrichment_status` | `ENUM('PENDING', 'ENRICHED', 'NEEDS_REVIEW')` | `PENDING` on insert; `ENRICHED` when match confidence ≥ 85%; `NEEDS_REVIEW` when no source crossed the threshold. |
| `first_release_date` | `DATE` | Optional. IGDB `first_release_date` (Unix seconds → DATE). NULL when unknown or when matched only via Steam fallback (Steam doesn't expose this). |
| `genres` | `JSONB` | Array of names from IGDB, e.g. `["RPG", "Adventure"]`. Defaults to `'[]'`. GIN-indexed. |
| `themes` | `JSONB` | Array of names from IGDB. Defaults to `'[]'`. GIN-indexed. |
| `developers` | `JSONB` | Array of company names where IGDB `involved_companies.developer = true`. A company can also appear in `publishers`. Defaults to `'[]'`. GIN-indexed. |
| `publishers` | `JSONB` | Array of company names where IGDB `involved_companies.publisher = true`. Defaults to `'[]'`. GIN-indexed. |

Metadata fields (`genres`, `themes`, `developers`, `publishers`, `first_release_date`) are populated by the IGDB enrichment path only. Steam fallback leaves them at defaults. The `cover_source=CUSTOM` rule applies: the enrichment worker will not overwrite metadata on a CUSTOM-cover game (treats the row as user-owned). Existing ENRICHED rows can be re-queued via the manual `tasks.backfill_metadata` Celery task (not on Beat schedule):

```bash
docker compose exec worker celery -A app.core.celery_app call tasks.backfill_metadata
```

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
| `is_flicker` | `BOOLEAN` | Default `false`. System-owned flag. Set to `true` by the bot when a `source=BOT` session closes with `duration_seconds < SESSION_SHORT_FLICKER_SECONDS` (default 180s). Cleared back to `false` if the same game resumes within `SESSION_STITCH_WINDOW_SECONDS` (stitch-resume). `source=MANUAL` sessions are never auto-flagged. Excluded at SELECT like `ERROR` and soft-deleted rows — `is_flicker` is not exposed in API responses. History is preserved until the GC task runs. |
| `deleted_at` | `TIMESTAMPTZ` | NULL = live. Set by `DELETE /api/v1/sessions/{id}` (soft-delete). The hard-delete sweeper removes rows where `deleted_at < NOW() - 7 days`. |
| `created_at` | `TIMESTAMPTZ` | |

**Indexes:**

- `ix_game_sessions_user_id_start_time` — composite btree on `(user_id, start_time)`. Used by overlap validation in `POST/PATCH /sessions` and by `/stats/summary` window aggregation. Migration `0004`.
- `ix_game_sessions_deleted_at_partial` — partial btree on `deleted_at WHERE deleted_at IS NOT NULL`. Used by the hard-delete sweeper. Migration `0005`.

**State-machine transitions (soft-delete layer):**

- `COMPLETED` or `ERROR` → soft-deleted via `DELETE /api/v1/sessions/{id}` (sets `deleted_at`).
- soft-deleted → `COMPLETED` or `ERROR` (status preserved) via `POST /api/v1/sessions/{id}/restore` (clears `deleted_at`). For `COMPLETED`, overlap is re-validated on restore.
- soft-deleted → permanently gone via `DELETE /api/v1/sessions/{id}?hard=true` (bypasses the sweeper) or automatically by the Hard Delete Sweeper after 7 days.

**Invariants:**

- Only one `ONGOING` session per user at any time. Enforced by partial unique index `uq_game_sessions_user_ongoing` plus per-user Postgres advisory locks in the bot.
- `ERROR` sessions are excluded from all aggregates (`/stats/*`, weekly report) until resolved.
- `ONGOING` sessions cannot be soft-deleted directly — only the bot owns those rows.
- `is_flicker=true` rows are excluded at every SELECT layer (sessions, stats, games, resolve, voice context, overlap validation) — they never surface to the user or cause 409s.
- Config invariant enforced at startup: `SESSION_FLICKER_GC_MARGIN_SECONDS` must exceed `SESSION_STITCH_WINDOW_SECONDS`. This guarantees the GC never removes a flicker row that is still eligible to be a stitch target.
- `cover_source=CUSTOM` — the enrichment worker skips `cover_image_url` and metadata overwrites on those games (user-owned row).

### `user_game_preferences`

Per-user metadata layered on top of the global `games` catalog. Not all users have a preference row for every game — absence means defaults.

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `user_id` | `VARCHAR(32)` | FK → `users.discord_id`, `ON DELETE CASCADE` |
| `game_id` | `INTEGER` | FK → `games.id`, `ON DELETE CASCADE` |
| `is_ignored` | `BOOLEAN` | Default `false`. User-initiated hide — filtered out at the SELECT layer in `/stats/*` and the main `/games` list. Sessions are preserved. |
| `is_accepted` | `BOOLEAN` | Nullable. Only meaningful for `NEEDS_REVIEW` games: `false` = Unrecognized inbox (hidden from main library/stats), `true` = user accepted the stub. `NULL` when not applicable (`ENRICHED`/`PENDING`). Auto-set to `false` when enrichment lands on `NEEDS_REVIEW`; cleared to `NULL` when the game later becomes `ENRICHED`. |
| `custom_tag` | `VARCHAR(64)` | Optional user-supplied label. |

Unique constraint on `(user_id, game_id)`. The merge endpoint (`POST /api/v1/admin/games/{id}/merge/{target_id}`, admin-only) reassigns these rows transactionally, dropping conflicts where the target already has a preference for the same user.

### `reports`

In-app user feedback submitted via `POST /reports`. Triaged by admins via `GET /admin/reports` / `PATCH /admin/reports/{id}` / `DELETE /admin/reports/{id}` / `GET /admin/reports/facets` (see [api.md](api.md#admin)).

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `user_id` | `VARCHAR(32)` | FK → `users.discord_id`, `ON DELETE CASCADE`, indexed |
| `message` | `TEXT` | Free-text feedback, trimmed server-side. `NOT NULL`. |
| `context` | `JSONB` | Diagnostic blob captured client-side — `screen`, `platform`, `osVersion`, `appVersion` (camelCase keys). `NOT NULL`. |
| `status` | `VARCHAR(16)` | `open` \| `triaged` \| `closed`, `CHECK` constraint `ck_reports_status`. Default `'open'`. `NOT NULL`. `PATCH /admin/reports/{id}` allows any status to transition to any other, including reopening a `closed` or `triaged` report. |
| `created_at` | `TIMESTAMPTZ` | Default `NOW()`, indexed. |
| `admin_note` | `TEXT` | Admin's free-text triage note. Nullable, no default. Migration `0017`. |

**Indexes:**

- `ix_reports_user_id` — btree on `user_id`. Migration `0010`.
- `ix_reports_created_at` — btree on `created_at`. Migration `0010`.
- `ix_reports_status_created_at` — btree on `(status, created_at DESC)`, for the admin triage inbox's filter + sort. Migration `0014`.

`ON DELETE CASCADE` on `user_id` — deleting a user removes their reports along with them.

### `account_deletion_events`

Append-only Art. 17 erasure audit trail. **No foreign key to `users`** — rows must outlive hard purge of the account. Written when a deletion is requested, cancelled, or completed by the nightly purge task.

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGINT` | Primary key, autoincrement |
| `discord_id` | `VARCHAR(32)` | Discord snowflake of the account. Not an FK — the user row may already be gone. |
| `event` | `VARCHAR(32)` | One of `requested`, `cancelled`, `purged`. Enforced by `ck_account_deletion_events_event`. |
| `created_at` | `TIMESTAMPTZ` | Default `NOW()`. When the event was recorded. |
| `purge_at` | `TIMESTAMPTZ` | Nullable. Snapshot of the scheduled purge time — set on `requested` and carried through to `purged`; NULL only on `cancelled`. |

**Indexes:**

- `ix_account_deletion_events_discord_id_created_at` — composite btree on `(discord_id, created_at)`. Migration `0019`.

**Ops SQL (read-only trail inspection):**

```sql
-- Full trail for one Discord ID (newest first)
SELECT id, event, created_at, purge_at
FROM account_deletion_events
WHERE discord_id = :discord_id
ORDER BY created_at DESC;

-- Recent purged accounts
SELECT discord_id, created_at
FROM account_deletion_events
WHERE event = 'purged'
ORDER BY created_at DESC
LIMIT 50;
```

### `demo_seed_sessions`

Frozen snapshot of `game_sessions` rows for the permanent Google Play reviewer demo account, captured once by `app/scripts/capture_demo_snapshot.py`. `tasks.reset_demo_account` (below) restores the demo account's live `game_sessions` from this table every night.

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `game_id` | `INTEGER` | FK → `games.id`, no cascade — the catalog is shared, so seed rows ride along with whatever the catalog already holds rather than duplicating game data. |
| `start_time` | `TIMESTAMPTZ` | Shifted by a single delta at restore time so the most recent session lands on the current day. |
| `end_time` | `TIMESTAMPTZ` | Nullable — but the capture script positively selects `status == COMPLETED` rather than excluding a status list, and both `ONGOING` and `ERROR` rows have a NULL `end_time`, so in practice every row here has one. |
| `duration_seconds` | `INTEGER` | Nullable, matching `game_sessions`. |
| `status` | `VARCHAR(16)` | |
| `source` | `VARCHAR(16)` | |

No `user_id` column — the demo account is a singleton, so there is nothing to key rows to. No `is_flicker` column — the capture script drops flicker rows outright rather than snapshotting them, since the flicker GC sweep (`tasks.purge_flicker_sessions`, 04:00) would otherwise delete most of them again the morning after every restore.

### `demo_seed_preferences`

Frozen snapshot of `user_game_preferences` rows for the same demo account, restored alongside `demo_seed_sessions`.

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `game_id` | `INTEGER` | FK → `games.id`, no cascade |
| `is_ignored` | `BOOLEAN` | Default `false` |
| `is_accepted` | `BOOLEAN` | Nullable |
| `custom_tag` | `VARCHAR(64)` | Nullable |

No `user_id` column, for the same reason as `demo_seed_sessions`.

Both seed tables gain two extra remap statements in the game-merge transaction (`POST /api/v1/admin/games/{id}/merge/{target_id}`), alongside the three existing ones for `game_aliases`, `game_sessions`, and `user_game_preferences`. `game_sessions.game_id` has no `ON DELETE CASCADE`, and merge deletes the source `games` row, so without this a merge touching a snapshotted game would either fail outright or (under cascade) silently drop seed rows, breaking the next reset.

**Accepted risks of the demo account, listed together:**

- **Junk catalog rows.** `POST /games` only requires `get_current_user`, so the demo account can create shared `games` rows (e.g. via the manual-session flow). These land as aliasless `NEEDS_REVIEW` stubs, which `GET /games/suggest` hides from every caller who hasn't touched them — the row sits in the catalog but does not surface in another user's typeahead. Accepted as admin-cleanable; closing the write itself would mean blocking manual session creation for the demo account, a headline feature reviewers should see working. The igdb_id-mode `query` alias write on `POST /games` is blocked for the demo account specifically, since `game_aliases.discord_process_name` is globally UNIQUE and a leaked code could otherwise squat a real process name and capture other users' presence onto a junk game — any other authenticated user can still write a `query` alias through this path freely, so the demo exclusion remains a narrow, load-bearing guard rather than something superseded by broader policy. Separately, unrecognized-mode alias binding is no longer something `POST /games` does for anyone: it is now a general, admin-only action (see `docs/api.md` → `POST /admin/games/{id}/aliases`), not a demo-specific carve-out.
- **Reports reach the admin triage inbox.** `POST /reports` also only requires `get_current_user`, so a leaked code can file reports that land in the same queue as real user feedback. Mitigated, not blocked: `tasks.reset_demo_account` deletes any reports filed by the demo account every night, so the exposure never persists past one day.

## Relationships at a glance

```
users ─┬── user_auth_tokens   (1:N, cascade)
       ├── user_devices       (1:N, cascade)
       ├── game_sessions      (1:N, cascade)
       └── reports            (1:N, cascade)

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
| `0007_game_metadata.py` | Adds `first_release_date` + `genres`/`themes`/`developers`/`publishers` JSONB columns to `games` with GIN indexes. |
| `0010_reports_table.py` | Adds the `reports` table (`user_id` FK cascade, `message`, `context` JSONB, `created_at`) with `ix_reports_user_id` and `ix_reports_created_at` indexes. |
| `0014_reports_status.py` | Adds `reports.status` (`String(16)`, default `'open'`, `NOT NULL`) with `ck_reports_status` (`open`/`triaged`/`closed`) and the `ix_reports_status_created_at` index for the admin triage inbox. |
| `0017_reports_admin_note.py` | Adds `reports.admin_note` (`Text`, nullable, no default) for admin triage notes. |
| `0018_user_account_deletion.py` | Adds `users.deletion_requested_at` and `users.purge_at` (`TIMESTAMPTZ`, nullable, no default) with the partial index `ix_users_purge_at_partial`. |
| `0019_account_deletion_events.py` | Adds append-only `account_deletion_events` Art. 17 audit table (`discord_id`, `event`, `created_at`, `purge_at`) with `ck_account_deletion_events_event` and index `ix_account_deletion_events_discord_id_created_at`. No FK to `users`. |
| `0020_demo_account.py` | Adds `demo_seed_sessions` and `demo_seed_preferences` (see above), and inserts the reserved demo `users` row (`discord_id='1'`, `ON CONFLICT DO NOTHING`). The demo identity literals are duplicated in the migration rather than imported from `app.services.demo`, since migrations must not depend on app code that can change after the migration is frozen in history. |

## Scheduled tasks (Celery Beat)

| Task | Schedule (UTC) | Purpose |
|---|---|---|
| `tasks.weekly_report` | Monday 09:00 | FCM digest for users with `weekly_report_enabled` and `push_enabled`; skips accounts scheduled for deletion (`purge_at IS NOT NULL`) |
| `tasks.hard_delete_sweep` | Daily 03:30 | Purge trashed sessions older than `TRASH_RETENTION_DAYS` (default 7); purge FCM tokens idle 6+ months |
| `tasks.purge_deleted_accounts` | Daily 03:45 | Permanently delete every `users` row whose `purge_at` has passed (`DELETE FROM users WHERE purge_at <= now`). `ON DELETE CASCADE` on `user_id` erases that account's sessions, preferences, tokens, devices, reports, and voice usage along with it. Catalog `games` rows have no FK to `users` and are never touched. In the same transaction, the task also inserts one `purged` event per deleted `discord_id` into `account_deletion_events`. |
| `tasks.purge_flicker_sessions` | Daily 04:00 | Hard-delete `COMPLETED` flicker rows whose `end_time` is older than `SESSION_FLICKER_GC_MARGIN_SECONDS` (default 86400s). Runs after `hard_delete_sweep` to keep the two sweepers separate. |
| `tasks.reset_demo_account` | Daily 03:00 | Restore the permanent Google Play reviewer demo account (`users.discord_id='1'`) to a known-good state: delete its `game_sessions`, `user_game_preferences`, `user_devices`, `voice_usage`, and `reports` rows, restore `game_sessions` / `user_game_preferences` from `demo_seed_sessions` / `demo_seed_preferences` with every timestamp shifted by a single delta so the newest session lands on the current day, and upsert the `users` row back to its canonical state (`is_admin=false`, pinned username/timezone/language, `deletion_requested_at`/`purge_at` cleared). Runs as one transaction — a failure partway rolls back entirely rather than leaving the account emptied. Does not touch `user_auth_tokens`; those are bounded instead by the 5-live-token cap on redemption (see [api.md](api.md#permanent-reviewer-login)). |

The account-deletion grace period (`users.deletion_requested_at` → `purge_at`) is 7 days (`ACCOUNT_DELETION_GRACE_DAYS`, default 7). Because `tasks.purge_deleted_accounts` only runs once nightly, the actual purge lands up to ~24h after the 7-day mark — never before it. Purging removes the row from the live database only; any existing database backups taken before the purge expire on their own separate retention schedule.

# Discord Bot

The bot is the only writer of `source=BOT` sessions. It runs as the `bot` service in `docker-compose.yml` and shares the database with the API.

Source: `app/bot/main.py`, `app/bot/commands.py`, `app/bot/session_manager.py`, `app/bot/self_healing.py`.

## What it does

- Listens for Discord rich-presence updates (`on_presence_update`) on every guild it's in.
- Translates "user started / stopped / switched games" into `game_sessions` rows.
- On restart, reconciles every `ONGOING` session against current presence (Self-Healing).
- Exposes `/register`, `/login`, and `/logout` slash commands for user onboarding and session management. Until a user exists in the `users` table (via `/register` or `/login`), the bot ignores their presence entirely.

## Intents and prerequisites

Required Discord intents (set in `app/bot/main.py` before client init):

- `presences` — needed to receive `on_presence_update`.
- `members` — needed for guild member lookups during Self-Healing.

Both are **privileged** and must be explicitly enabled in the Discord Developer Portal under *Bot → Privileged Gateway Intents*. Without them, `discord.py` will fail to connect.

The OAuth2 invite URL must include both `bot` and `applications.commands` scopes — the latter is required for slash commands to register. If the bot was invited without `applications.commands`, re-invite it (this does not remove it from existing servers).

## Slash commands

All three commands read `discord_id` and `username` from the interaction context (no user input — Discord supplies them). Replies are ephemeral (only the invoking user sees them). Logic lives in `app/bot/commands.py`.

### `/register`

Upserts a `users` row:

- New user → INSERT, reply: "Zarejestrowano w GameTrace!"
- Existing user → sync the `username` field in case the user renamed on Discord; reply: "Już jesteś zarejestrowany."

Use this when a user wants to be tracked by the bot without logging into the mobile app yet.

### `/login`

Upserts the `users` row (same as `/register`), then issues a one-time login code stored in Redis (`LINK_CODE_SECRET` must be set):

- **6-digit code** — cryptographically random, displayed as `XXX XXX` (e.g. `482 193`).
- **5-minute TTL** — expires automatically after 300 seconds.
- **Single-use** — redeemed via `POST /api/v1/auth/link`; the code is deleted on successful redemption.
- **Re-run invalidates** — issuing a new `/login` deletes any previous pending code for that user.

Reply: `Twój kod logowania: **XXX XXX**. Wpisz go w aplikacji w ciągu 5 minut.` If `LINK_CODE_SECRET` is unset, reply: `Kody logowania nie są skonfigurowane.`

After running `/login`, the user enters the code in the mobile app to obtain a bearer token.

### `/logout`

Revokes **all** active app sessions for the user:

1. Deletes every `user_auth_tokens` row for the user's `discord_id`.
2. Discards any pending link code in Redis.

Reply: `Wylogowano. Unieważniono N sesji w aplikacji.` (or `Nie jesteś zarejestrowany.` if the user has no `users` row).

## Presence tracking

`on_presence_update` fires whenever any cached member changes activity. The `on_presence_update` handler does:

1. **Filter:** ignore bots; ignore presence changes that didn't change the playing-game name.
2. **Gate:** look up the user in `users`. If they haven't run `/register` or `/login`, return — the bot is "blind" to non-registered users.
3. **Resolve game:** for an active game name, find or create a `games` row via `game_aliases.discord_process_name`. New games are inserted as a stub (just the process name) and queued for async enrichment via Celery.
4. **Apply transition** to the user's current `ONGOING` session (if any):

| `before` activity | `after` activity | Action |
|---|---|---|
| game | none | `complete_session` — set `end_time = NOW()`, `status = COMPLETED` |
| none | game | if the current `ONGOING` is already this game, leave it running (spurious repeat event); otherwise `start_session` for the new game, erroring any stale ONGOING for a *different* game first |
| game A | game B | `complete_session` for A, `start_session` for B |
| same | same | no-op (filtered before reaching the handler) |

Only one `ONGOING` session per user is allowed at a time — this is invariant the handler relies on.

### Flicker suppression and stitch-resume

Discord rich-presence is occasionally flaky: a single continuous play session can fragment into multiple short `ONGOING → COMPLETED` transitions if presence drops for a few seconds. The bot handles this in real time — no extra process, no background scan.

**Suppress at close:** when `complete_session` finishes, if the session is `source=BOT` and `duration_seconds < SESSION_SHORT_FLICKER_SECONDS` (default 180s), the row is flagged `is_flicker=true`. The session is kept in the database (history preserved) but excluded at every SELECT layer — it is invisible to the API, stats, games list, voice context candidates, and overlap validation. `source=MANUAL` sessions are never auto-flagged, regardless of duration.

**Stitch on resume:** when `start_session` would create a new row for game G, the bot first looks back for the most recent `source=BOT, status=COMPLETED` session for that game. If its `end_time` is within `SESSION_STITCH_WINDOW_SECONDS` (default 180s), the bot reopens that row instead of inserting a new one: `status → ONGOING`, `end_time → NULL`, `duration_seconds → NULL`, `is_flicker → false`. The session continues seamlessly; when it finally closes, `duration_seconds` spans the entire range including the dropout gap.

**Config invariant:** `SESSION_FLICKER_GC_MARGIN_SECONDS` (default 86400s) must exceed `SESSION_STITCH_WINDOW_SECONDS` at startup. This is enforced at boot and guarantees the daily GC task (`tasks.purge_flicker_sessions`) never removes a row that could still be a stitch target.

**Repeat start of the same game.** Discord sometimes redelivers a game-start presence update with an empty `before`, so the handler sees `none → game` while that same game is already `ONGOING`. The handler resolves the game first and, when it matches the live session, treats the event as a spurious repeat and leaves the session untouched — no ERROR, no new row. Without this guard the live session was errored and reopened, producing same-second same-game `ERROR` churn. Only a stale `ONGOING` for a *different* game is treated as an orphan and errored.

**Self-Healing is unaffected.** Self-Healing's ERROR path sets no clean `end_time` and never triggers the stitch check. ERROR sessions remain unaffected by flicker logic.

### Write-then-enrich

The bot writes session and stub-game rows immediately, regardless of any user preference (`is_ignored` filtering happens at the API layer, not the bot). It then fires a Celery task `enrich_game_{game_id}` to fetch metadata. The task ID is stable so duplicate enrichments for the same game collapse in Redis. Enrichment failure never blocks session writes — the worst case is a `Game` row with `enrichment_status=PENDING` indefinitely, which is fine.

Game-name matching for enrichment is described in [game-matching.md](game-matching.md).

## Heartbeat and liveness

On `on_ready`, the bot writes `bot:started_at` to Redis (Unix timestamp). A background loop (`@tasks.loop(seconds=30)`) refreshes `bot:heartbeat` with the current timestamp and a 90s TTL.

`GET /api/v1/health` reads these keys to report `bot.status` (`online` / `offline` / `unknown`), `bot.uptime_seconds`, and `bot.last_heartbeat_seconds_ago`. If Redis is unreachable, health fails soft — `bot.status` becomes `"unknown"` instead of erroring. See [api.md](api.md#health).

**Docker Compose note:** the `bot` service does not declare `depends_on: redis` (unlike `api`). Redis usually wins the race on `docker compose up`, but if the bot starts before Redis is ready, the first `bot:started_at` write and a few heartbeat ticks may fail — the loops log a warning and retry every 30s. Until a heartbeat lands, `GET /api/v1/health` reports `bot.status: "offline"`. Session writes are unaffected (Postgres only).

## Self-Healing

`app/bot/self_healing.py`. Runs once on `on_ready` (after slash-command sync, before the bot starts processing presence events).

Bot downtime — restarts, deploys, container kills, network blips — leaves `ONGOING` rows in the database with no corresponding live presence event to close them. Self-Healing reconciles every such row:

```
For each ONGOING session:
  1. Find the user in any guild the bot is in.
     • Not found → ERROR ("user not found in any guild after bot restart")

  2. Check session age.
     • NOW() - start_time > 12h → ERROR ("exceeded 12h threshold")
       (Catches sessions left running through long outages or forgotten games.)

  3. Compare current presence to session's recorded game.
     • Same game → keep ONGOING, do nothing
       (This is the goal: a 30-second container restart should not fragment a real play session.)
     • Different game → ERROR old session ("switched from X to Y"), start fresh ONGOING for the new game
     • Not playing → ERROR ("no longer in-game")
```

Sessions transitioned to `ERROR` are surfaced to the user via the Dashboard banner (`pending_errors` in `/stats/dashboard` and `/stats/summary`). The user resolves them by either supplying an `end_time` (`PATCH /sessions/{id}` → `COMPLETED`) or discarding them (`DELETE /api/v1/sessions/{id}` → soft-deleted).

The 12h ceiling is intentionally generous — it's a backstop for "user fell asleep / forgot to close the game / bot was down longer than expected", not a precision tool. Real sessions almost never reach it.

### Why this design

- **No graceful shutdown of `ONGOING` on bot stop.** A bot restart that closes sessions on the way down would split one continuous play session into two whenever the container redeploys — which it does often. Leaving ONGOING alone and reconciling on startup gives seamless continuation in the common case.
- **`notes` is system-owned.** Self-Healing writes the human-readable reason (`"switched from X to Y"`, `"no longer in-game"`, `"12h threshold"`) into `game_sessions.notes`. The frontend surfaces this read-only in the Napraw/Odrzuć flow so the user knows why a session needs attention.
- **`source=BOT` distinction.** Manual sessions (`source=MANUAL`) are written by the API, skip the state machine, and land directly as `COMPLETED`. Self-Healing only touches `source=BOT, status=ONGOING`.

## Failure modes worth knowing

| Failure | Behaviour |
|---|---|
| Discord rate-limits the bot | `discord.py` handles backoff internally; presence events queue up and replay |
| Database briefly unavailable | The handler raises and `discord.py` swallows it — the missed presence change is lost. Next restart's Self-Healing catches stuck `ONGOING` rows. |
| Celery / Redis down at session start | Enrichment task fails to enqueue; the session is still written. Game stays `enrichment_status=PENDING` until the next presence event for that game (which retries the enqueue). |
| User leaves all guilds the bot is in | Their `ONGOING` session can no longer be reconciled; on next restart Self-Healing marks it `ERROR` with "user not found". |
| Discord rich-presence flicker | Handled by stitch-resume + flicker suppression (see above). Short BOT sessions are flagged `is_flicker=true` at close; if the same game resumes within `SESSION_STITCH_WINDOW_SECONDS`, the session is reopened and the flag is cleared. |
| `LINK_CODE_SECRET` unset | `/login` replies with an error message; `POST /auth/link` returns `503`. |
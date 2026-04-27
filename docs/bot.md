# Discord Bot

The bot is the only writer of `source=BOT` sessions. It runs as the `bot` service in `docker-compose.yml` and shares the database with the API.

Source: `app/bot/main.py`, `app/bot/session_manager.py`, `app/bot/self_healing.py`.

## What it does

- Listens for Discord rich-presence updates (`on_presence_update`) on every guild it's in.
- Translates "user started / stopped / switched games" into `game_sessions` rows.
- On restart, reconciles every `ONGOING` session against current presence (Self-Healing).
- Exposes a `/login` slash command that registers a user in the database. Until a user runs `/login` at least once, the bot ignores their presence entirely.

## Intents and prerequisites

Required Discord intents (set in `app/bot/main.py:18`):

- `presences` — needed to receive `on_presence_update`.
- `members` — needed for guild member lookups during Self-Healing.

Both are **privileged** and must be explicitly enabled in the Discord Developer Portal under *Bot → Privileged Gateway Intents*. Without them, `discord.py` will fail to connect.

The OAuth2 invite URL must include both `bot` and `applications.commands` scopes — the latter is required for the `/login` slash command to register. If the bot was invited without `applications.commands`, re-invite it (this does not remove it from existing servers).

## `/login` slash command

`app/bot/main.py:49`. Reads `discord_id` and `username` from the interaction context (no user input — Discord supplies them) and upserts a `users` row:

- New user → INSERT.
- Existing user → sync the `username` field in case the user renamed on Discord; everything else stays.

The reply is ephemeral (only the invoking user sees it). After running `/login` once, the user can log into the mobile app with their Discord username.

## Presence tracking

`on_presence_update` fires whenever any cached member changes activity. The handler at `app/bot/main.py:73` does:

1. **Filter:** ignore bots; ignore presence changes that didn't change the playing-game name.
2. **Gate:** look up the user in `users`. If they haven't run `/login`, return — the bot is "blind" to non-registered users.
3. **Resolve game:** for an active game name, find or create a `games` row via `game_aliases.discord_process_name`. New games are inserted as a stub (just the process name) and queued for async enrichment via Celery.
4. **Apply transition** to the user's current `ONGOING` session (if any):

| `before` activity | `after` activity | Action |
|---|---|---|
| game | none | `complete_session` — set `end_time = NOW()`, `status = COMPLETED` |
| none | game | `start_session` for the new game (after erroring any unexpected stale ONGOING) |
| game A | game B | `complete_session` for A, `start_session` for B |
| same | same | no-op (filtered before reaching the handler) |

Only one `ONGOING` session per user is allowed at a time — this is invariant the handler relies on.

### Write-then-enrich

The bot writes session and stub-game rows immediately, regardless of any user preference (`is_ignored` filtering happens at the API layer, not the bot). It then fires a Celery task `enrich_game_{game_id}` to fetch metadata. The task ID is stable so duplicate enrichments for the same game collapse in Redis. Enrichment failure never blocks session writes — the worst case is a `Game` row with `enrichment_status=PENDING` indefinitely, which is fine.

Game-name matching for enrichment is described in [game-matching.md](game-matching.md).

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

Sessions transitioned to `ERROR` are surfaced to the user via the Dashboard banner (`pending_errors` in `/stats/dashboard` and `/stats/summary`). The user resolves them by either supplying an `end_time` (`PATCH /sessions/{id}` → `COMPLETED`) or discarding them (`PATCH /sessions/{id}` with `discard=true` → soft-deleted).

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
| Discord rich-presence flicker | Currently produces fragmented short sessions on rapid `ONGOING → COMPLETED → ONGOING`. Debounce is on the [roadmap](roadmap.md). |

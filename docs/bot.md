# Discord Bot

The bot is the only writer of `source=BOT` sessions. It runs as the `bot` service in `docker-compose.yml` and shares the database with the API.

Source: `app/bot/main.py`, `app/bot/commands.py`, `app/bot/session_manager.py`, `app/bot/self_healing.py`.

## What it does

- Listens for Discord rich-presence updates (`on_presence_update`) on every guild it's in.
- Translates "user started / stopped / switched games" into `game_sessions` rows.
- On restart, reconciles every `ONGOING` session against current presence (Self-Healing).
- Exposes `/register`, `/login`, and `/logout` slash commands for user onboarding and session management, plus `/stats`, `/recent`, and `/help` for read-only lookups, and an admin-only `/panel` that posts a button-driven onboarding panel (see [Onboarding panel](#onboarding-panel)) for channels where slash commands are unreachable. Until a user exists in the `users` table (via `/register`, `/login`, or the panel), the bot ignores their presence entirely.
- The read commands (`/stats`, `/recent`) never query Postgres directly for session/game data — they call the FastAPI service over HTTP (`app/bot/api_client.py`), so visibility rules (`library_only`, `is_ignored`, unaccepted `NEEDS_REVIEW`) stay defined in exactly one place, the API layer, instead of being reimplemented against the bot's own DB session.

## Intents and prerequisites

Required Discord intents (set in `app/bot/main.py` before client init):

- `presences` — needed to receive `on_presence_update`.
- `members` — needed for guild member lookups during Self-Healing.

Both are **privileged** and must be explicitly enabled in the Discord Developer Portal under *Bot → Privileged Gateway Intents*. Without them, `discord.py` will fail to connect.

The OAuth2 invite URL must include both `bot` and `applications.commands` scopes — the latter is required for slash commands to register. If the bot was invited without `applications.commands`, re-invite it (this does not remove it from existing servers).

## Slash commands

Seven commands total: `/register`, `/login`, `/logout` for onboarding and session management, plus `/stats`, `/recent`, `/help` for read-only lookups, and `/panel` (admin-only) to post the onboarding panel. All of them reply ephemerally (only the invoking user sees the reply), and all except `/panel` read `discord_id` and `username` from the interaction context (no user input — Discord supplies them). `/panel` is the one command that also posts a **public** message to the channel — the panel itself — alongside its ephemeral acknowledgement. Command logic lives in `app/bot/commands.py`; user-facing copy is isolated in `app/bot/replies.py` so tone/wording can be reviewed in one place independent of the calling code.

### `/register`

Upserts a `users` row (new users get `settings.default_timezone`, not the `UTC` model default):

- New user → INSERT, reply includes a short onboarding blurb about what GameTrace does (and a link to the web app, if `GAMETRACE_WEB_URL` is set).
- Existing user → sync the `username` field in case the user renamed on Discord; terse reply: "Już jesteś zarejestrowany."
- Existing user **scheduled for deletion** → the `users` row still exists (deletion is a grace-period sweep, not immediate), so the plain "already registered" reply would be actively misleading. Instead the reply states the account is scheduled for deletion and the purge date, and directs the user to log into the app **and then** cancel the deletion in settings — two explicit steps, never "already registered". Logging in alone does not cancel anything (see [`/profile/me/deletion`](api.md)).

Use this when a user wants to be tracked by the bot without logging into the mobile app yet.

### `/login`

Upserts the `users` row (same as `/register`, including the `default_timezone` on first creation), then issues a one-time login code stored in Redis (`LINK_CODE_SECRET` must be set):

- **6-digit code** — cryptographically random, displayed as `XXX XXX` (e.g. `482 193`).
- **5-minute TTL** — expires automatically after 300 seconds.
- **Single-use** — redeemed via `POST /api/v1/auth/link`; the code is deleted on successful redemption.
- **Re-run invalidates** — issuing a new `/login` deletes any previous pending code for that user.

Reply: `Twój kod logowania: **XXX XXX**. Wpisz go w aplikacji w ciągu 5 minut.` — first-time users (this call created the `users` row) get the same onboarding blurb as `/register` prepended before the code; returning users get just the terse code line. If `LINK_CODE_SECRET` is unset, reply: `Kody logowania nie są skonfigurowane.`

After running `/login`, the user enters the code in the mobile app to obtain a bearer token.

### `/logout`

Revokes **all** active app sessions for the user:

1. Deletes every `user_auth_tokens` row for the user's `discord_id`.
2. Discards any pending link code in Redis.

Reply: `Wylogowano. Unieważniono N tokenów logowania w aplikacji.` (or `Nie jesteś zarejestrowany.` if the user has no `users` row).

### `/stats`

Reports the caller's last 7 days, mirroring `GET /stats/summary?days=7`: total playtime, top 3 games, count of sessions needing a fix (`pending_errors`), and count of games awaiting review (`GET /games?status=NEEDS_REVIEW`).

- Unregistered caller → `NOT_REGISTERED` reply ("Nie jesteś zarejestrowany..."), no API call made.
- Registered but nothing played in the window → an encouraging "still watching, come back after you play" reply, not a bare empty state.
- Account scheduled for deletion → see [pending-deletion 403 handling](#read-commands-defer-degradation-and-access-path) below.
- Read-only — never creates a `users` row (unlike `/register`/`/login`).

### `/recent`

Reports the caller's last 5 non-ongoing sessions (`GET /sessions?status=COMPLETED&status=ERROR&library_only=true&limit=5`), each rendered with the game name, local start time (caller's `users.timezone`, resolved as described under [Timezone resolution](#timezone-resolution-in-recent) below), and duration (or "błąd, brak czasu trwania" for `ERROR` rows).

- Unregistered caller → `NOT_REGISTERED` reply, no API call made.
- Registered but no history yet → an encouraging empty-state reply, same bar as `/stats`.
- Account scheduled for deletion → see [pending-deletion 403 handling](#read-commands-defer-degradation-and-access-path) below.
- Requests `library_only=true`, so sessions on ignored games or unaccepted `NEEDS_REVIEW` stubs are excluded, matching what the Dashboard "Recents" tile shows.
- Read-only — never creates a `users` row.

### `/help`

Static orientation copy — no HTTP call, no DB lookup, no defer. Explains what GameTrace does for someone who noticed the bot and has no idea why it's there; it does not enumerate the other commands (Discord's own slash-command picker already does that with each command's description). Appends a link to the web app when `GAMETRACE_WEB_URL` is configured.

### `/panel` (admin)

Posts the onboarding panel (see [Onboarding panel](#onboarding-panel) below) as a **public** message in the channel it's invoked in — the one non-ephemeral message in the whole feature. Gated on Discord's own `manage_guild` permission (`app_commands.checks.has_permissions(manage_guild=True)`), not GameTrace's `is_admin` — who may post a message into a channel is a Discord-server question, not app RBAC, so there is no database lookup.

- Success → the panel is posted publicly, then the command acknowledges with an ephemeral confirmation so the admin isn't left guessing.
- Bot lacks **Send Messages** in the channel → the `send()` call raises `discord.Forbidden`, caught and replied to the admin ephemerally, naming the missing permission. Without this, a locked channel where the bot also can't post fails silently.
- Caller lacks `manage_guild` → Discord raises `app_commands.MissingPermissions` before the command body runs; a `@panel_command.error` handler replies with a clean ephemeral refusal instead of leaving a traceback in the logs.

### Read commands: defer, degradation, and access path

`/stats` and `/recent` call `interaction.response.defer(ephemeral=True)` before any I/O — a cold container's DB lookup plus the HTTP round-trip to the API can exceed Discord's ~3-second ack deadline, which would otherwise surface as a false "this interaction failed" to the user. `/help` does neither DB lookup nor defer since it has no I/O to wait on.

Both read commands call the API via `app/bot/api_client.py` using the [bot service credential](api.md#bot-service-credential-internal) (`X-Bot-Service-Secret` + `X-Discord-Id`), authenticated server-side by `get_bot_user`/`get_current_or_bot_user`. If the API is unreachable, times out (5s), or returns a non-2xx/non-JSON response, the command catches `BotApiError` and replies with a friendly Polish failure message (`Nie udało się pobrać statystyk...` / `Nie udało się pobrać ostatnich sesji...`) instead of raising. **This failure mode is isolated to the two read commands** — presence recording (`on_presence_update`) talks to Postgres directly and is completely unaffected by the API being down.

**Pending-deletion 403 is a distinct case, not a generic failure.** `get_bot_user` 403s an account scheduled for deletion with a structured body (`{"detail": {"detail": "Account scheduled for deletion", "purge_at": ..., "days_left": ...}}` — see [`/profile/me/deletion`](api.md)). `api_client._get` recognises this exact shape and raises `PendingDeletionError` (a `BotApiError` subclass) instead of the generic error; `stats_command`/`recent_command` catch it *before* the generic `except BotApiError` and reply with copy naming the purge date and directing the user to log into the app and cancel the deletion there explicitly, instead of the opaque "couldn't fetch" message. A 403 that doesn't match the marker shape (e.g. a genuine authorization failure) falls through to the generic `BotApiError` path unchanged — the distinction is made on body content, not status code alone.

### Timezone resolution in `/recent`

`/recent` resolves the caller's local time with a plain `ZoneInfo(user.timezone)` lookup, falling back to UTC when the value is missing or not a recognised IANA zone (`app/bot/replies.py::_resolve_tz`). This is a **different** rule from the voice pipeline's `resolve_timezone` (`app/services/voice_context.py`), which additionally treats the literal string `"UTC"` as "unset" and substitutes `DEFAULT_TIMEZONE` in that case. `/recent` has no reason to make that substitution — a user whose `users.timezone` is genuinely `"UTC"` should see times in UTC, not silently remapped to the server's default zone. Two distinct timezone-resolution semantics exist in the codebase by design; don't assume they match when touching either one.

## Onboarding panel

`app/bot/panel.py`. Discord requires the **Send Messages** permission to invoke a slash command, so a read-only announcement channel (`@everyone` denied Send Messages) can never offer `/login` or `/register` there. Buttons carry no such requirement, so this module reimplements the entry path — register, get a login code, check stats, view recent sessions, log out — as Components V2 buttons on a persistent message that `/panel` posts.

### Who posts it, and what happens

An admin runs `/panel` in the target channel (see [`/panel` (admin)](#panel-admin) above). This posts one **public** `PanelView` message — a title, body, and three buttons — the only non-ephemeral message anywhere in the feature. Everything a clicker sees after that is ephemeral, exactly like the slash-command replies.

Re-running `/panel` posts an **additional** panel; it does not replace or invalidate the previous one. Every panel's buttons share the same `custom_id`s and dispatch through the same registered views, so old panels keep working — a stale copy left behind after a re-run is cosmetic clutter to delete manually, never a broken button.

### Button map

| Button | Where | Does |
|---|---|---|
| `▶ START` | `PanelView` (public panel) | Looks up the clicker in `users` (read-only). No account → opens the `NewUserView` disclosure, ephemeral. Existing account → opens the `MemberView` menu, ephemeral. |
| `🇵🇱 Co to jest?` (`gt:panel:help`) | `PanelView` (public panel) | Polish orientation screen (`replies.panel_info_pl()`), ephemeral. No I/O. Kept on `gt:panel:help` deliberately — panels already posted to the live server carry a button with that exact `custom_id`, so keeping the id (rather than minting a new one for the Polish screen) means those existing panels keep working after this deploy. |
| `🇬🇧 What is it?` (`gt:panel:help:en`) | `PanelView` (public panel) | English mirror (`replies.panel_info_en()`), ephemeral. No I/O. New `custom_id` — only this button is new. |
| `✓ Akceptuję i zakładam konto` | `NewUserView` (ephemeral disclosure) | Calls `commands.register_user`, then **edits the same message** into a `MemberView` — the disclosure becomes the member menu in place, not a second message. |
| `🔑 Weź kod` | `MemberView` (ephemeral menu) | Issues a login code via `commands.issue_login_code`, same semantics as `/login`'s code step. |
| `📊 Statystyki` | `MemberView` (ephemeral menu) | Same as `/stats`, via `commands.stats_command`. |
| `🕒 Ostatnie` | `MemberView` (ephemeral menu) | Same as `/recent`, via `commands.recent_command`. |
| `🚪 Wyloguj` | `MemberView` (ephemeral menu) | Same as `/logout`, via `commands.logout_user`. |

All three views (`PanelView`, `NewUserView`, `MemberView`) are stateless — instantiated once with no arguments and shared by every clicker, so any per-user data is read only from the interaction, never stored on the view.

### Persistence across restarts

`on_ready` registers every class in `panel.PERSISTENT_VIEWS` (`PanelView`, `NewUserView`, `MemberView`) via `bot.add_view(cls())`, so their `custom_id`s keep dispatching after a bot restart without needing the original message. Because `on_ready` re-fires on every gateway reconnect (not just process start), registration is guarded by a module-level flag in `app/bot/main.py` — the same pattern `_heartbeat_loop.is_running()` uses to guard the heartbeat loop — so repeated reconnects don't re-register the same views.

**One owner per `custom_id`.** discord.py dispatches persistent components by `(component_type, custom_id)` in a single bucket, so if the same pair were ever registered on two views, the second registration would silently steal the first's callback. This is why `gt:menu:*` lives on `MemberView` alone — the accept path reaches it by editing the disclosure message into a `MemberView`, never by duplicating the button onto `NewUserView`.

### Setting up a locked onboarding channel

This is the scenario the panel exists for: a channel where new members land, `@everyone` has **Send Messages** denied (so it stays clutter-free and slash commands are unreachable), and the panel is the only way in.

Required permissions in that channel:

- **The bot** needs **View Channel** and **Send Messages** — explicitly, since a channel-level or role-level deny for `@everyone` does not automatically extend to the bot's own role. Give the bot's role (or a channel-specific permission overwrite for the bot) both, even though `@everyone` has Send Messages off.
- **The invoking admin** needs the `manage_guild` (Manage Server) permission to run `/panel` at all — enforced by Discord itself, not looked up in GameTrace's `users.is_admin`.
- **Embed Links is not required.** Components V2 layouts are not embeds; nothing in this feature uses the embed system.

## Presence tracking

`on_presence_update` fires whenever any cached member changes activity. The `on_presence_update` handler does:

1. **Filter:** ignore bots; ignore presence changes that didn't change the playing-game name.
2. **Gate:** look up the user in `users` via `get_user_if_tracked`. If they haven't run `/register` or `/login`, return — the bot is "blind" to non-registered users. Accounts scheduled for deletion (`purge_at IS NOT NULL`) are treated the same way — `get_user_if_tracked` returns `None` for them, so presence writes stop the instant a deletion is scheduled.
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
- **Accounts scheduled for deletion never get a new `ONGOING` session.** Self-Healing never calls `get_user_if_tracked`, so it needs its own check: it joins `users` on each `ONGOING` row and, on the switched-game branch, still errors the stale session but skips `start_session` when that user's `purge_at` is set. This closes a race the presence gate alone can't: a presence event already in flight when a deletion is scheduled can leave an `ONGOING` session behind, and a later bot restart would otherwise reopen it as a fresh session for an account queued for erasure.

## Failure modes worth knowing

| Failure | Behaviour |
|---|---|
| Discord rate-limits the bot | `discord.py` handles backoff internally; presence events queue up and replay |
| Database briefly unavailable | The handler raises and `discord.py` swallows it — the missed presence change is lost. Next restart's Self-Healing catches stuck `ONGOING` rows. |
| Celery / Redis down at session start | Enrichment task fails to enqueue; the session is still written. Game stays `enrichment_status=PENDING` until the next presence event for that game (which retries the enqueue). |
| User leaves all guilds the bot is in | Their `ONGOING` session can no longer be reconciled; on next restart Self-Healing marks it `ERROR` with "user not found". |
| Discord rich-presence flicker | Handled by stitch-resume + flicker suppression (see above). Short BOT sessions are flagged `is_flicker=true` at close; if the same game resumes within `SESSION_STITCH_WINDOW_SECONDS`, the session is reopened and the flag is cleared. |
| `LINK_CODE_SECRET` unset | `/login` replies with an error message; `POST /auth/link` returns `503`. |
| API unreachable/timeout for `/stats` or `/recent` | The command replies with a friendly Polish failure message (see [Read commands: defer, degradation, and access path](#read-commands-defer-degradation-and-access-path)). Presence recording is unaffected — it never goes through the API. |
| `BOT_SERVICE_SECRET` unset | `/stats` and `/recent` still defer, call the API, and get back `401` from `get_bot_user` — surfaced to the user as the same generic failure message as any other `BotApiError`. |
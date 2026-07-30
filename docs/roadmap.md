# Roadmap

Where GameTrace is headed. Nothing here is committed to a date — items are roughly
ordered by when they become relevant, not by priority.

For detailed incident write-ups and deferred fixes with evidence, see
[tech-debt.md](tech-debt.md). For what already exists, see [api.md](api.md) and the
[README](../README.md).

## Auth

### Two verified login paths

Discord OAuth2 (`POST /auth/discord`, authorization code + PKCE) is live: it verifies
the caller controls the Discord account, auto-provisions the user, and flags
`needs_server_join` when they're not in the bot's server.

The second path is the **link-code OTP handshake**, also live: the Discord `/login`
slash command issues a short-lived, single-use code (delivered as an ephemeral reply),
which the user enters in the app to exchange for a session token via `POST /auth/link`
— Discord-verified identity without OAuth's redirect/deep-link plumbing.

These two are the user-facing login paths. The username-only `POST /auth/login` remains
as a development shortcut, disabled by default and enabled only when its dev secret is
configured; it is not part of the production auth surface.

### Drop `users.username` uniqueness

`users.username` is `unique=True`, a holdover from when login matched on username. Now
that identity is keyed on `discord_id`, the constraint is a liability: Discord
usernames aren't globally stable, so a rename or collision can fail the OAuth user
upsert. The endpoint guards this with a `409` today; the proper fix is a migration
dropping the constraint so usernames become non-identifying display data.

## Pre-release hardening

A bundle of items that don't block any user flow today but should land before the API
is exposed beyond its current private deployment.

- **Hashed token storage** — store auth tokens hashed at rest (SHA-256, compare on
  hash).
- **Token-activity debouncing** — `get_current_user` updates the sliding expiry on
  every request; batching that write (only when `last_active` is older than 5–10
  minutes) removes a per-request DB commit.
- **Request body size cap** — one line in the reverse proxy (`client_max_body_size`);
  per-endpoint inner limits can follow if needed.
- **Rate limit on `/voice/transcribe`** — each call is a paid Whisper request, so this
  gets a per-user budget (`slowapi` + Redis, keyed on the authenticated user, roughly
  10 requests/hour — well above legitimate use). Deferred until the voice pipeline is
  fully validated end-to-end with the frontend, so the happy path is locked in first.
- **MIME sniffing on uploads** — the cover endpoint gets `python-magic` sniffing
  (allow `image/jpeg|png|webp`, derive the on-disk extension from the sniffed type);
  the voice endpoint gets a lighter magic-bytes check (RIFF/WAVE, ID3, MP3 frame sync,
  MP4 `ftyp`, Ogg) to reject obvious garbage before paying for the API call.
- **Centralized logging** — unified logging config in `app/core/observability.py`
  across API, bot, and workers.
- **CI quality gates** — add `ruff` and `mypy` alongside the test run.

### Offsite database backups

Local backups are live: daily `pg_dump` + covers tar to a host path outside Docker
volumes, via the compose `backup` profile and host cron. The remaining step is an
offsite copy to GCS (dedicated bucket, lifecycle retention, service-account auth,
dumps encrypted before upload) — required before the API serves users outside the
local network.

## Voice pipeline

Context-aware extraction is live (timezone-anchored dates, library candidate matching,
Whisper language hint, Gemini structured output). Next steps:

- **Regex fallback when Vertex AI is unavailable** — a rule-based extractor for the
  common phrasings ("I played Hades for two hours yesterday evening") so the feature
  degrades gracefully instead of hard-failing when the model is unreachable.
- **Bring-your-own-key** — let users plug in their own GCP project or OpenAI key,
  stored encrypted, removing per-request cost from the host.
- **Self-hosted Whisper** — `faster-whisper` as an extra compose service (~1–2 GB RAM,
  zero per-request cost); worth it once usage justifies the infrastructure.

## Timezone-aware weekly reports

The weekly digest currently fires Monday 09:00 UTC for everyone. The upgrade is an
hourly fan-out task that dispatches to users whose *local* Monday 09:00 has arrived.
Already designed, not yet implemented.

## Scale

When `game_sessions` crosses ~10 million rows or `/stats/summary` p95 climbs past
100 ms despite the existing indexes, the next move is range-partitioning by month
with native Postgres partitioning — no data loss, no rollups, and partition pruning
keeps time-windowed queries trivial. Not relevant at current scale.

## Manual game tracking (clients)

**Backend is live** (`GET /games/suggest`, `POST /games/match`, `POST /games` — see
[manual-game-tracking.md](manual-game-tracking.md) and [api.md](api.md)). Clients still
need the wizard (library suggest → optional IGDB pick → create/link → `POST /sessions`)
so users can log games that never appeared via Discord presence. Highest-value remaining
frontend gap; not blocked on further backend work.

## Possible future expansions

Ideas that are deliberately **not** in the near-term finish line. Design notes may exist
under `docs/superpowers/specs/`; nothing here is scheduled.

### Steam playtime import

Import a user's Steam library (lifetime minutes per owned game) as a **self-contained
snapshot**, shown on stats behind a source filter (GameTrace / Steam / cumulative). Never
writes to the shared `games` catalog or to `game_sessions` — Steam only returns scalar
`playtime_forever`, not timestamped sessions, so mixing would invent times and double-count.

Parked as product expansion. Spec (when revived):
[docs/superpowers/specs/2026-06-30-steam-playtime-import-design.md](superpowers/specs/2026-06-30-steam-playtime-import-design.md).

## Discord bot onboarding panel

**Shipped.** Every bot reply — slash commands and the new panel below — now renders as a
Components V2 layout (title, separator, body inside a container) instead of plain
markdown text.

Beyond the visual restyle, this closes a real access gap: Discord requires the **Send
Messages** permission to invoke a slash command, so a read-only announcement channel
could never offer `/login` or `/register` to `@everyone`. Buttons carry no such
requirement. An admin (`Manage Server` permission) runs `/panel` once to post a
permanent onboarding message into any channel — including one where `@everyone` has
Send Messages turned off. From there, anyone can register an account, grab a login
code, check stats, view recent sessions, or log out entirely through buttons, with no
typing required. The buttons are registered as persistent views, so they keep working
across bot restarts, and posting `/panel` again simply adds another copy — old panels
are unaffected. See [bot.md](bot.md#onboarding-panel) for the full button map and
required permissions.

## Game covers

Admin-curated global covers are live (`PUT /api/v1/admin/games/{id}/cover`, storing
host-independent relative URLs — see the Admin section of [api.md](api.md)). What
remains:

### Per-user cover persistence — deferred (frontend-owned)

User-added cover photos are stored locally today — per device on mobile, per browser
on web — and don't sync across devices. Server-side persistence is intentionally
deferred: hosting user-uploaded images turns GameTrace into a UGC platform with
content-moderation and legal obligations that are out of scope. Distinct from
admin-curated covers (shared, few, vetted) — this one is per-user and unbounded.
Revisit only if cross-device covers become a real user need.

## Admin panel

A thin admin surface, built in slices behind the shipped RBAC gate rather than as a
monolithic console. The **core is live**: `users.is_admin`, a `require_admin`
dependency gating everything under `/api/v1/admin/*`, game merge and cover curation,
catalog housekeeping endpoints, reports triage, and an audit log line on every admin
write. The frontend will be a separate route on the web client (`/admin`), using the
`is_admin` field the API already returns at login; enforcement stays server-side.

### Catalog ops — live

Admin catalog housekeeping is shipped (see [api.md → Admin](api.md#admin) and
[tech-debt.md](tech-debt.md) for motivating cases):

| Endpoint | Purpose |
|---|---|
| `GET /admin/games?status=NEEDS_REVIEW&q=` | Global catalog review queue — filter, search, sort by session count |
| `POST /admin/games/{id}/enrich` | Re-queue the Celery enrichment task |
| `POST /admin/games/match` + `POST /admin/games/{id}/igdb-link` | IGDB search; admin picks the candidate → apply metadata to the row |
| `POST /admin/games/{id}/aliases` | Add exact Discord process-name variants |

UI v1 can be minimal — a table with action buttons. Admin ops are global and don't
require the caller to have sessions on the game.

### Later — observability + user feedback

- **Server errors:** lean on Sentry rather than rebuilding it — the panel links or
  embeds Sentry issues. Optionally a thin feed of 5xx + selected upstream failures
  (with request id, route, user) for a "Recent failures" tab.
- **In-app bug reports:** `POST /api/v1/reports` (any authenticated user) and admin
  triage (`GET /admin/reports`, `PATCH /admin/reports/{id}`) are live. Subjective user
  feedback stays separate from server stack traces.
- **Logs:** container logs and Flower cover Celery/process visibility; no log tail
  inside the panel unless volume ever warrants it.
- **Audit table:** admin writes currently emit structured log lines; a dedicated
  audit table replaces them if log-based review proves insufficient.

### Polish (unscheduled)

- Review UI for borderline enrichment auto-links
  (see [tech-debt.md → Enrichment v2](tech-debt.md#enrichment-v2--token-subset--llm-adjudicator-design-sketch))
- Periodic `NEEDS_REVIEW` sweep trigger
- Sentry Performance sampling — prerequisite for the stats-cache triggers below

### Explicitly out of scope (v1)

- Per-user session replay or remote client log streaming
- Re-opening the removed public cover/merge routes for non-admins
- Per-user server-side cover hosting (see [Game covers](#game-covers))

## Stats

### Click genre → games

Genres and themes are JSONB arrays on `games` — efficient for aggregation, awkward for
"show me all RPGs" with pagination. Normalization to M2M tables is deferred until
there's a clear UI need for click-through navigation.

### Cache `/stats/dashboard` (short-term)

The dashboard is the one polled stats endpoint, so it's the first to benefit from
caching: per-user serialized response in the existing Redis, short TTL (~30–60 s),
wrapped after auth/serialization so a per-user key can never leak across users, and
**fail open** — if Redis errors, fall through to the live query, never 500.

TTL-only to start, no write-invalidation: bounded staleness on a polling tile is
invisible, and the bot writes sessions straight to Postgres, so there's no single
place to bust the key today. Write-invalidation can ride along once a bot↔API link
exists for other reasons. Trigger to build: dashboard request throughput climbing,
not data volume.

### Cache the fixed-tier stats windows (long-term)

The analytical stats screens are fetched on app-open and manual refresh, not polled,
so caching there is insurance. When it lands, only the fixed window tier (7/30/90 d)
gets cached — those few shared buckets give a usable hit rate, while arbitrary custom
ranges would fragment the key space into read-once entries. If the custom path ever
gets slow, the answer is a better index or pre-aggregated rollups, not a response
cache. Trigger to build: `/stats/summary` p95 latency drifting up despite indexes.

### FCM "dashboard dirty" nudge (long-term)

The only dashboard state changes the client can't anticipate are bot-originated
(a session opening or closing). A lightweight push-on-change — bot publishes a signal,
API turns it into an FCM *data* message, phone fetches once on demand — is the cheap
version of event-driven: reuses the existing FCM stack, no persistent connections.
Explicitly after response caching lands, and only if dashboard freshness becomes a
feature rather than a nicety.

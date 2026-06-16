# Roadmap

Things on the horizon for GameTrace. Nothing here is committed to a date — this is the "we know we want this, here's what it looks like" list. Items are roughly ordered by when they become relevant, not by priority.

## Auth

### Discord OAuth2 login
Replaces the current username-based login. Today, `POST /auth/login` accepts a Discord username and (if the user was pre-registered via the `/login` slash command) issues a 30-day sliding token. This is intentional minimal-friction auth for a homelab build, but the username is effectively a credential — anyone who knows it and has network access to the API can log in.

**Update (Audit 2026-05-14):** This is categorized as a P0 security risk (Identity Impersonation). Transitioning to OAuth2 or an OTP-based handshake is the primary priority for the next development cycle.

### Administrative Access (RBAC)
**New (Audit 2026-05-14):** Destructive endpoints like `POST /games/{id}/merge/{target_id}` are currently open to all authenticated users. We need to introduce a `is_admin` flag on the `User` model and implement Role-Based Access Control (RBAC) to protect global game data.

### Secure Token Storage
**New (Audit 2026-05-14):** Current authentication tokens are stored in plain text. We should transition to storing SHA-256 hashes of the tokens to protect active sessions in the event of a database compromise.

## Pre-release hardening

A bundle of items that don't block any user flow today but should land before the API is exposed publicly (i.e. before sharing with users outside the homelab network).

### Auth Performance: Token Debouncing
**New (Audit 2026-05-14):** The `get_current_user` dependency currently performs a database commit on every single request to update activity timestamps. This is a significant performance bottleneck. We should implement "debouncing" — only updating the database if the token's `last_active` is older than 5-10 minutes.

### Request body size cap
FastAPI/Starlette has no default body size limit. A single 5 GB upload to `/voice/transcribe` could fill the API container's tempdir. Fix is one line in the reverse proxy (`client_max_body_size 10m;` in nginx) — outer ring, zero application code. Per-endpoint inner limits can be tuned later if needed.

### Rate-limit on `/voice/transcribe`
Each call to this endpoint is a paid OpenAI Whisper request. A leaked auth token plus a loop equals a real invoice. Plan: `slowapi` with Redis backend, keyed on `user_id` (not IP — we already have auth context, and the threat is leaked-credential abuse, not anonymous traffic). Budget around 10 requests/hour/user — well above legitimate use, well below "ouch". Stays correct after the OAuth migration since the key is still the authenticated user.

**Update (Audit 2026-05-14):** Added as a P3 security risk.

### Centralized Logging
**New (Audit 2026-05-14):** Individual modules currently manage their own logging inconsistently. We need a unified logging configuration in `app/core/observability.py` to ensure consistent formatting and log levels across the API, Bot, and Celery workers.

### CI Quality Gates
**New (Audit 2026-05-14):** The CI pipeline only runs tests. We should add `ruff` for linting and `mypy` for static type checking to ensure code quality and prevent regressions in type safety.

Why this is deferred: the voice pipeline isn't fully validated end-to-end with the frontend yet. Adding rate-limiting before the happy path is locked in introduces a debugging variable we don't need.

### MIME sniffing on uploads
Two endpoints accept binary uploads (`PUT /games/{id}/cover`, `POST /voice/transcribe`) and currently rely on client-supplied content type. Plan:
- **Cover:** sniff with `python-magic` (libmagic), allow only `image/jpeg|png|webp`, derive the on-disk extension from the sniffed type — never trust the client-supplied filename.
- **Voice:** lighter check — match the first ~12 bytes against known audio signatures (RIFF/WAVE, ID3, MP3 frame sync, MP4 `ftyp`, Ogg). Whisper itself is container-tolerant, so full libmagic would be over-engineered; the goal is to reject obvious garbage before paying for the API call.

Why this is deferred: best-practice rather than blocker. Worth doing before public release once the upload flows are battle-tested with a small group of users on the homelab.

## Voice pipeline

**Shipped:** Context-aware extraction — datetime anchor in `users.timezone`, library candidate matching via `rapidfuzz.partial_ratio`, Whisper language hint, Gemini structured output (`response_schema`). See `app/services/voice_context.py` and the Voice section in [api.md](api.md).

### Regex fallback when Vertex AI is unavailable
If Vertex is down or the GCP project hits a quota, the whole feature still breaks — the context blocks above don't help without the model. A regex-based extractor as fallback would handle the common cases ("I played Hades for two hours yesterday evening") without Gemini. Lower accuracy, but graceful degradation beats a hard error.

### Bring-your-own-key
Let users plug in their own GCP project or OpenAI key, stored encrypted in the `users` table. Removes the per-request cost from the host, removes the rate-limit pressure, and is an obvious requirement if GameTrace ever leaves homelab scope.

### Self-hosted Whisper
Run `faster-whisper` as an extra docker-compose service (~1–2 GB RAM, zero per-request cost). Trades infrastructure load for zero variable cost. Likely worth it once usage justifies it.

## Timezone-aware weekly reports

Current Celery Beat fires the weekly digest on Monday 09:00 UTC for everyone. Users in non-UTC timezones get the report at 11:00 (Warsaw), 04:00 (US East), etc. Upgrade is straightforward: hourly fan-out task that queries users whose local Monday 09:00 is reached, dispatches push notifications for each. Already designed, just not implemented.

## Scale

When `game_sessions` crosses ~10 million rows or `/stats/summary` p95 starts climbing past 100 ms despite the existing indexes, the next move is range-partitioning by month using native Postgres partitioning. No data loss, no rollups, partition pruning makes time-windowed queries trivial. Not relevant at homelab scale.

## Session data quality

### Source flip on user edit — shipped

When a user edits a `BOT` session (fixes an ERROR or adjusts `end_time` via PATCH), `source` flips to `MANUAL` — the session's times are now user-attested, not bot-observed. Implemented in `patch_session` alongside the `end_time` update. See the `PATCH /sessions/{id}` row in [api.md](api.md).

## Stats

### Click genre → games
Currently, genres and themes are stored as JSONB arrays on the `games` table. This is efficient for aggregation but doesn't easily support "show me all RPGs" with pagination. Deferring normalization to M2M tables until there is a clear UI need for click-through navigation.

### Cache `/stats/dashboard` (short-term)
The dashboard is the one polled stats endpoint, so it's the first to benefit from caching. Plan: cache the serialized response per user (key `stats:dashboard:{discord_id}`) in the existing Redis, short TTL (~30–60s), wrapped *after* auth/serialization so a per-user key can never leak across users. **Fail open** — if Redis errors, fall through to the live query, never 500.

TTL-only to start, no write-invalidation: bounded staleness on a polling tile is invisible, and it sidesteps the fact that the bot writes sessions straight to Postgres (not through the API), so there's no single place to bust the key today. Add write-invalidation later, once the bot↔API link exists for other features and can carry an invalidation signal — not before, to avoid coupling the cache to an undefined contract. Known ≤TTL quirk: a cache entry straddling the user's local midnight shows the previous "today" total until it expires — a once-a-day, sub-minute blip, acceptable at this TTL.

**How we'll know it's time:** the trigger is *throughput*, not data volume. Enable Sentry Performance tracing (see Ops / quality) and watch the `/stats/dashboard` transaction's requests/min; when concurrent polling load climbs, build this. (Needs Sentry actually running first.)

### Cache the fixed-tier stats windows (long-term)
The analytical stats screen (`/stats/summary` etc.) is fetched on app-open and manual refresh, not polled, so it carries far less load than the dashboard — caching is insurance, not urgent. When it lands, cache only the **fixed window tier** (7/30/90d), keyed `stats:summary:{discord_id}:{days}`; those few shared buckets give a usable hit rate.

The planned **custom-range** option is deliberately left uncached: arbitrary ranges fragment the key space into read-once entries with a near-zero hit rate, so response caching there is wasted memory. The custom path accepts a longer wait by design; if it ever gets slow, the answer is a better index or pre-aggregated rollups, not a response cache. (Timezone handling for custom ranges is a separate problem, deferred.)

**How we'll know it's time:** here the trigger is *latency*, not throughput (this screen is fetched on open/refresh, not polled). Watch the `/stats/summary` transaction's p95 in Sentry Performance; when it drifts up from single-digit ms toward ~50 ms+ despite the existing index, cache the fixed tier.

### FCM "dashboard dirty" nudge (long-term)
The dashboard is poll-based (the frontend re-fetches `/stats/dashboard` on a timer; the active-session clock ticks client-side from `start_time`). The only state changes the client can't anticipate are bot-originated: a session opening or closing on the user's PC. A lightweight push-on-change would have the bot publish a "dashboard dirty" signal that the API turns into an FCM *data* message, so the phone fetches once on demand instead of on a fixed interval.

Deliberately the cheap version of event-driven: reuses the existing FCM stack, no persistent connections (WebSocket/SSE would mean a bot→Redis pub/sub bridge plus connection lifecycle — over-engineering for a tile whose live element already ticks locally). FCM data delivery is best-effort, so a foreground reconcile fetch stays regardless. Long-term and explicitly *after* response caching lands — caching turns each poll into a cheap Redis read, which already removes most of the motivation. Only worth building if dashboard freshness becomes a feature rather than a nicety.

## Ops / quality

### Verify Sentry + Flower end-to-end
Both were wired but never confirmed working against a live backend. They're gated on empty env defaults, so absent config they silently no-op rather than error — which is exactly how "added but untested" hides.

- **Sentry** — `init_sentry()` no-ops unless `SENTRY_DSN` is set (`example.env` ships it blank). Verify: set a real DSN, trigger a deliberate exception, confirm it lands in the Sentry project. Separately, `traces_sample_rate` is currently `0.0` (errors only) — Performance tracing is off, so there's no endpoint throughput/latency data yet. Raise it (e.g. `0.1`, or a `traces_sampler` scoped to `/stats/*`) and confirm transactions appear. This is the prerequisite for the "how we'll know it's time" triggers on the caching items above.
- **Flower** — runs at `:5555` (internal), reads `GAMETRACE_FLOWER_AUTH` (renamed from `FLOWER_BASIC_AUTH` so the image doesn't auto-pick an empty value into a broken 401 mode — see `docker-compose.yml`). Verify: set `FLOWER_BASIC_AUTH`, open the UI, confirm it authenticates and shows live workers/tasks. No read-only mode, so keep it internal-only.

### Bot flicker handling — shipped

Discord rich-presence is occasionally flaky — a single real play session can fragment into multiple short sessions if presence drops for a few seconds. This is handled in the bot in real time via two mechanisms:

- **Suppress at close:** when `complete_session` finishes, if the session is `source=BOT` and shorter than `SESSION_SHORT_FLICKER_SECONDS` (default 180s), the row is flagged `is_flicker=true`. Flicker rows are excluded at every SELECT layer (sessions list, stats, games, resolve, voice context, overlap validation). `source=MANUAL` sessions are never auto-flagged.
- **Stitch on resume:** when the same game resumes within `SESSION_STITCH_WINDOW_SECONDS` (default 180s) of a just-closed BOT session, the bot reopens that row (`is_flicker → false`, `status → ONGOING`) instead of creating a new one. The final `duration_seconds` spans the entire range including the dropout gap.
- **GC:** `tasks.purge_flicker_sessions` (Celery Beat, daily 04:00 UTC) hard-deletes `COMPLETED` flicker rows older than `SESSION_FLICKER_GC_MARGIN_SECONDS` (default 86400s). A startup invariant enforces `GC_MARGIN > STITCH_WINDOW` so the GC can never remove a row still eligible to stitch.

See [bot.md](bot.md#flicker-suppression-and-stitch-resume) and [schema.md](schema.md) for implementation detail.

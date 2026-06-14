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

### Source flip on user edit
When a user edits a `BOT` session (fixes an ERROR or adjusts end_time via PATCH), the `source` field stays `BOT`. It should flip to `MANUAL` at that point — the session's times are now user-attested, not bot-observed. One-line change in `patch_session`: set `session.source = SessionSource.MANUAL` alongside the `end_time` update.

## Stats

### Click genre → games
Currently, genres and themes are stored as JSONB arrays on the `games` table. This is efficient for aggregation but doesn't easily support "show me all RPGs" with pagination. Deferring normalization to M2M tables until there is a clear UI need for click-through navigation.

## Ops / quality

### Bot flicker debounce
Discord rich-presence is occasionally flaky — a single real play session can fragment into multiple short sessions if presence drops for a few seconds. Fix is at the bot: debounce `ONGOING → COMPLETED → ONGOING` transitions shorter than ~2 minutes into a single continuous session. Independent of any storage decisions; the user's session list just stops looking noisy.

### Short-session threshold
Today every bot-detected session is stored, even if it's a few seconds long — launching a game by accident, a misfired presence event, or a quick "is this still installed?" check all become rows in `game_sessions`. Plan: in `complete_session` (`app/bot/session_manager.py`), if `duration_seconds < N` (proposed: 180s / 3 minutes), drop the session instead of marking it COMPLETED. Manual sessions (`source=MANUAL`) are unaffected — the user is explicitly asserting the time.

Threshold belongs at write time, not at stats time: keeping noise out of storage is cleaner than filtering it on every aggregation. Pairs naturally with the flicker debounce above — debounce first (so a real session split across two short fragments isn't discarded), then apply the floor.

# Roadmap

Things on the horizon for GameTrace. Nothing here is committed to a date — this is the "we know we want this, here's what it looks like" list. Items are roughly ordered by when they become relevant, not by priority.

## Auth

### Discord OAuth2 login
Replaces the current username-based login. Today, `POST /auth/login` accepts a Discord username and (if the user was pre-registered via the `/login` slash command) issues a 30-day sliding token. This is intentional minimal-friction auth for a homelab build, but the username is effectively a credential — anyone who knows it and has network access to the API can log in.

OAuth2 flips the model: Discord owns the auth surface. The backend gets a signed identity from Discord (`identify` scope), maps it to a user record by `discord_id` (already in the schema), and issues its own session token from there. Mobile app handles the OAuth flow via `expo-auth-session`. Backend adds a `/auth/discord/callback` endpoint.

This also closes a related gap: there's currently no per-IP or per-username rate-limit on `/auth/login`. Adding one now would be wasted work — under OAuth, brute-force / enumeration is Discord's problem, not ours.

## Pre-release hardening

A bundle of items that don't block any user flow today but should land before the API is exposed publicly (i.e. before sharing with users outside the homelab network).

### Request body size cap
FastAPI/Starlette has no default body size limit. A single 5 GB upload to `/voice/transcribe` could fill the API container's tempdir. Fix is one line in the reverse proxy (`client_max_body_size 10m;` in nginx) — outer ring, zero application code. Per-endpoint inner limits can be tuned later if needed.

### Rate-limit on `/voice/transcribe`
Each call to this endpoint is a paid OpenAI Whisper request. A leaked auth token plus a loop equals a real invoice. Plan: `slowapi` with Redis backend, keyed on `user_id` (not IP — we already have auth context, and the threat is leaked-credential abuse, not anonymous traffic). Budget around 10 requests/hour/user — well above legitimate use, well below "ouch". Stays correct after the OAuth migration since the key is still the authenticated user.

Why this is deferred: the voice pipeline isn't fully validated end-to-end with the frontend yet. Adding rate-limiting before the happy path is locked in introduces a debugging variable we don't need.

### MIME sniffing on uploads
Two endpoints accept binary uploads (`PUT /games/{id}/cover`, `POST /voice/transcribe`) and currently rely on client-supplied content type. Plan:
- **Cover:** sniff with `python-magic` (libmagic), allow only `image/jpeg|png|webp`, derive the on-disk extension from the sniffed type — never trust the client-supplied filename.
- **Voice:** lighter check — match the first ~12 bytes against known audio signatures (RIFF/WAVE, ID3, MP3 frame sync, MP4 `ftyp`, Ogg). Whisper itself is container-tolerant, so full libmagic would be over-engineered; the goal is to reject obvious garbage before paying for the API call.

Why this is deferred: best-practice rather than blocker. Worth doing before public release once the upload flows are battle-tested with a small group of users on the homelab.

## Voice pipeline

### Regex fallback when Vertex AI is unavailable
Today the voice pipeline is OpenAI Whisper (STT) → Gemini Flash via Vertex AI (text→JSON). If Vertex is down or the GCP project hits a quota, the whole feature breaks. A regex-based extractor as fallback would handle the common cases ("I played Hades for two hours yesterday evening") without the model. Lower accuracy, but graceful degradation beats a hard error.

### Bring-your-own-key
Let users plug in their own GCP project or OpenAI key, stored encrypted in the `users` table. Removes the per-request cost from the host, removes the rate-limit pressure, and is an obvious requirement if GameTrace ever leaves homelab scope.

### Self-hosted Whisper
Run `faster-whisper` as an extra docker-compose service (~1–2 GB RAM, zero per-request cost). Trades infrastructure load for zero variable cost. Likely worth it once usage justifies it.

## Timezone-aware weekly reports

Current Celery Beat fires the weekly digest on Monday 09:00 UTC for everyone. Users in non-UTC timezones get the report at 11:00 (Warsaw), 04:00 (US East), etc. Upgrade is straightforward: hourly fan-out task that queries users whose local Monday 09:00 is reached, dispatches push notifications for each. Already designed, just not implemented.

## Scale

When `game_sessions` crosses ~10 million rows or `/stats/summary` p95 starts climbing past 100 ms despite the existing indexes, the next move is range-partitioning by month using native Postgres partitioning. No data loss, no rollups, partition pruning makes time-windowed queries trivial. Full design notes live in [`internal/scaling_for_released_app.md`](internal/scaling_for_released_app.md). Not relevant at homelab scale.

## Ops / quality

### Bot flicker debounce
Discord rich-presence is occasionally flaky — a single real play session can fragment into multiple short sessions if presence drops for a few seconds. Fix is at the bot: debounce `ONGOING → COMPLETED → ONGOING` transitions shorter than ~2 minutes into a single continuous session. Independent of any storage decisions; the user's session list just stops looking noisy.

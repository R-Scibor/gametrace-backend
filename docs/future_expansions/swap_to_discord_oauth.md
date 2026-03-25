# Future Expansion: Swap to Discord OAuth2

**Status:** Not planned — document only
**Priority:** Low (current username-based auth is adequate for homelab)
**Trigger:** Opening the app to more users, moving off homelab, or security requirements increase

---

## Why Discord OAuth2

The current auth flow (`POST /api/v1/auth/login` with Discord username) was a deliberate simplification for a closed group of friends on a homelab. It works because:
- Discord usernames are globally unique since 2023
- Everyone is pre-registered via `/login` slash command
- No real threat model in a local network

Discord OAuth2 would provide:
- Cryptographic proof of identity — no way to impersonate another user by knowing their username
- No pre-registration step — users authorize through Discord's own UI
- Standard security posture if the app is ever exposed to the internet

---

## What Needs to Change

### 1. Discord OAuth2 Application Setup

In Discord Developer Portal, under OAuth2:
- Add redirect URI: `https://<your-domain>/api/v1/auth/callback` (or local equivalent)
- Required scopes: `identify` (username + avatar + discriminator — no email needed)
- No `guilds` or `bot` scope needed for auth

New env vars:
```
DISCORD_CLIENT_ID=<oauth2 app client id>
DISCORD_CLIENT_SECRET=<oauth2 app client secret>
DISCORD_REDIRECT_URI=http://<host>/api/v1/auth/callback
```

### 2. New Auth Flow

Current flow:
```
User types username in app → POST /auth/login → token returned
```

OAuth2 flow:
```
App opens Discord authorization URL in browser/webview
→ User logs into Discord and approves
→ Discord redirects to /api/v1/auth/callback?code=<auth_code>
→ Backend exchanges code for access token (POST to Discord token endpoint)
→ Backend calls GET https://discord.com/api/users/@me to get discord_id + username
→ Backend upserts user record, returns session token
→ App receives session token and stores it
```

### 3. API Changes

**New endpoints:**

`GET /api/v1/auth/discord` — returns the Discord OAuth2 authorization URL for the frontend to open:
```json
{
  "url": "https://discord.com/oauth2/authorize?client_id=...&redirect_uri=...&response_type=code&scope=identify"
}
```

`GET /api/v1/auth/callback?code=<code>` — backend-side handler. Exchanges the code, fetches user identity from Discord, upserts user in DB, returns session token.

**Removed endpoint:**
`POST /api/v1/auth/login` (username-based) — replaced entirely.

**Unchanged:**
`POST /api/v1/auth/logout` — session token invalidation stays the same.

### 4. Frontend Changes

The login screen (Ekran 1) replaces the username text input with a single "Login with Discord" button. The button opens the OAuth2 authorization URL (from `GET /api/v1/auth/discord`) in an in-app browser via `expo-web-browser`. After redirect, the app extracts the session token from the callback and stores it in Zustand — same as current flow from that point on.

`expo-web-browser` + `expo-linking` handle the deep link redirect back into the app natively on both iOS and Android.

### 5. Bot `/login` Command

The `/login` slash command becomes **optional / deprecated**. OAuth2 creates the user record on first login — no pre-registration needed. The bot can still use `/login` as a convenience command to let users know the app exists, but it is no longer a prerequisite.

The bot's `on_presence_update` handler already uses `discord_id` from Discord events — no change needed there. The bot continues to only track users present in the `users` table.

### 6. Session Token — No Change

The session token mechanism (sliding 30-day expiry, server-side invalidation on logout, 401 → redirect) is unchanged. OAuth2 only affects how the user is initially identified — the rest of the auth lifecycle stays identical.

---

## What Does NOT Change

- Database schema (`users` table — `discord_id` remains the primary identifier)
- Bearer token auth on all other endpoints
- Bot presence detection and session recording
- All other API endpoints
- Frontend beyond Ekran 1

---

## Migration Path

1. Add new env vars (`DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`)
2. Add `GET /api/v1/auth/discord` and `GET /api/v1/auth/callback` endpoints
3. Keep `POST /api/v1/auth/login` alive during transition (deprecation period)
4. Update frontend login screen to OAuth2 flow
5. Once all users have logged in via OAuth2 at least once — remove `POST /auth/login`

No database migrations required. Existing sessions remain valid.

---

## Effort Estimate

| Task | Effort |
|---|---|
| Discord OAuth2 endpoints (backend) | Small |
| Token exchange + `/users/@me` call | Small |
| Frontend: expo-web-browser + deep link handling | Medium |
| Remove `/login` pre-registration dependency | Trivial |
| Tests | Small |

Total: one focused session, most of the complexity is in the frontend deep link wiring.

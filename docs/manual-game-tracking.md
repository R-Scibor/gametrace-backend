# Manual game tracking

**Status:** Implemented — see [api.md](api.md) → Games for the full endpoint reference.

Mobile users can now manually log playtime for a game that never appeared via Discord presence. `POST /sessions` still requires an existing `game_id`, but three new endpoints close the gap: `GET /games/suggest` for global catalog search, `POST /games/match` for live IGDB lookup, and `POST /games` for game creation or linking. A phone user typing "Hades" can obtain a `game_id` even if they have never played it on a tracked device.

This document describes the end-to-end experience and the design principles behind it.

## Problem

| Actor | Constraint |
|---|---|
| Mobile user | No Discord rich presence on the phone |
| Current API | Library membership = at least one non-flicker session |
| Current game creation | Bot-only stub insert → async Celery enrichment |
| Global `games` table | Incomplete catalog — only titles someone's bot has seen |

Expecting a searchable "all games we know" library endpoint is the wrong model. GameTrace is not IGDB.

## Design principles

1. **Library first** — When the typed name matches something the user already played, show it immediately. No external API call, no new game row.
2. **Human-in-the-loop for unknowns** — When the name is new to the user, query IGDB synchronously, return ranked candidates, and let the user pick. Do not auto-commit at the enrichment confidence threshold (0.85); the user *is* the disambiguator.
3. **IGDB before stub** — Do not `get_or_create` on the raw string and enrich later. Create or link the `Game` row only after the user selects an IGDB hit or explicitly chooses "Unrecognized."
4. **Session last** — Game discovery and session creation are separate steps. `POST /sessions` stays `game_id`-only. Avoid orphan sessions and keep overlap validation in one place.
5. **Reuse existing machinery** — Ranking (`_sanitize`, WRatio, number guard) and `NEEDS_REVIEW` stubs already exist in the enrichment worker. Extract "search + rank, return candidates" for the API; commit metadata on user confirm.
6. **No catalog dump to LLMs** — Voice context already sends top fuzzy matches from the user's history, not the full library. This flow does not change that. Mobile discovery is a UI wizard, not a Gemini prompt.

## Intended user flow

Roughly three screens on mobile (voice can reuse the same backend on resolve miss):

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Enter game name                                          │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 2a. Suggestions from global catalog (fuzzy, paginated)      │
│     → pick one → session form (game_id known)               │
│                                                             │
│ 2b. No good match in catalog                                │
│     → IGDB search for query → show top N candidates + art   │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Pick IGDB candidate  OR  "Unrecognized" (small indie)    │
│     → backend creates/links Game row                        │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Enter start/end time → POST /sessions (unchanged)      │
│     → game appears in GET /games via new session            │
└─────────────────────────────────────────────────────────────┘
```

### "Unrecognized"

User attestation that IGDB has no correct match (obscure indie, typo they'll fix later, non-game activity mislabeled, etc.):

- `primary_name` = user string
- `enrichment_status = NEEDS_REVIEW`
- no `external_api_id`
- no `game_aliases` row

Same semantics as a bot stub that failed enrichment, but chosen knowingly. Binding a string to this row (e.g. a Discord process name) is an admin action — `POST /api/v1/admin/games/{id}/aliases` — not something `POST /games` does itself. Because no alias is written, a later bot session for the same game will not auto-attach to this stub — the bot creates its own row (and its own alias) instead, so a manually tracked "Obscure Indie" and a subsequently detected one end up as two separate `games` rows until merged. `POST /api/v1/admin/games/{id}/merge/{target_id}` (admin-only) still handles duplicates.

## Implemented API surface

| Step | Endpoint | Scope |
|---|---|---|
| Library resolve | `GET /games/resolve?name=…` | Exact match in caller's library (unchanged) |
| Global catalog suggest | `GET /games/suggest?q=…` | Global games catalog (all users' games), except an aliasless `NEEDS_REVIEW` row the caller has never touched — that one is hidden, `PENDING` stays visible; paginated fuzzy match on `primary_name` + aliases, scored, relevance floor 0.3 |
| IGDB match | `POST /games/match` `{ query }` | Sync IGDB search; returns ranked candidates (`igdb_id`, `name`, `year`, `cover_url`, `score`) — no DB write |
| Confirm game | `POST /games` `{ igdb_id }` or `{ name, unrecognized: true }` | Create or link global `Game`; IGDB id mode dedupes and creates `ENRICHED`; unrecognized mode creates `NEEDS_REVIEW` stub |
| Log time | `POST /sessions` | Unchanged — `{ game_id, start_time, end_time }` |

`GET /games` (library list) and `GET /games/resolve` are unchanged from their pre-existing behaviour.

## What this is not

- **Not a catalog dump** — `GET /games/suggest` requires a non-blank query and applies a relevance floor (score < 0.3 dropped). There is no endpoint for paginated browsing of all rows in `games`. The DB is a shared cache of seen titles, not a storefront catalog.
- **Not session-independent library membership (v1)** — No wishlist/backlog without playtime unless product asks for it later. Library still derives from sessions; discovery just makes getting a `game_id` possible.
- **Not replacing bot creation** — Discord presence continues to stub games the same way. Mobile discovery is a parallel entry point into the same global `games` table.
- **Not async polling for disambiguation** — IGDB lookup (`POST /games/match`) is synchronous; the Twitch token is cached in Redis. Celery remains for background enrichment of bot stubs and backfill.

## Operational notes

- **Rate limits** — shipped. `POST /games/match` is capped at 20/hour and `POST /games` at 60/hour, per credential, Redis-backed (same mechanism as `/voice/transcribe`). IGDB throttles per Client ID, so the whole deployment plus the enrichment worker share one budget; the caps stop a looping client from draining it.
- **RBAC** — Creating global `Game` rows is lower risk than merge, but still shared data. RBAC has shipped (see the Admin section of [api.md](api.md)); revisit whether creation needs the admin gate if abuse becomes a concern.
- **Voice reuse** — After transcription, if library resolve misses, the app can run the same `match` → pick → session path instead of dead-ending.

## Related docs

- [api.md](api.md) — current Games and Sessions endpoints
- [game-matching.md](game-matching.md) — IGDB/Steam ranking pipeline
- [schema.md](schema.md) — `games`, `game_aliases`, `game_sessions`
- [roadmap.md](roadmap.md) — scheduling and priorities
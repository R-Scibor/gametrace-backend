# Tech debt

Detailed accounts of known gaps, incidents, and deferred fixes. For scheduled features see [roadmap.md](roadmap.md). For matching pipeline reference see [game-matching.md](game-matching.md).

---

## Kingdom Hearts HD 1.5 + 2.5 ReMIX — resolve miss and duplicate games (2026-06-21)

### Summary

Discord rich presence reports the compilation as `KINGDOM HEARTS -HD 1.5+2.5 ReMIX-`. The user's library accumulated **two separate game rows** for the same title (`id=33` ENRICHED, `id=45` NEEDS_REVIEW) because Discord reported the name with different formatting over time. `GET /games/resolve` returned `null` for the Discord string even though the enrichment scorer treats it as a perfect match against the canonical name. Game `id=45` was stuck in `NEEDS_REVIEW` despite IGDB matching the canonical name at confidence 1.0 when re-tested.

**Interim remediation (2026-06-21):** Merged `id=45` → `id=33` (ENRICHED). Added `game_aliases` row for the Discord presence string `KINGDOM HEARTS -HD 1.5+2.5 ReMIX-` on the surviving game.

### What the user saw

- Voice or manual entry using the Discord-formatted name did not resolve to a library `game_id`.
- The Unrecognized inbox showed a `NEEDS_REVIEW` stub (`Kingdom Hearts HD 1.5 + 2.5 ReMIX`, 26 sessions) alongside an already-enriched duplicate (`Kingdom Hearts HD 1.5 + 2.5 Remix`, 10 sessions).
- Playing via Discord with the new presence string would have created a **third** stub (bot matches aliases by exact string only).

### Root causes

#### 1. `GET /games/resolve` is exact-match only

`resolve_game` compares `name.strip().lower()` for equality against `games.primary_name` and `game_aliases.discord_process_name`. It does **not** call `_confidence()` from the enrichment pipeline.

| Query | Stored name / alias | `_confidence` | `resolve` |
|---|---|---|---|
| `KINGDOM HEARTS -HD 1.5+2.5 ReMIX-` | `Kingdom Hearts HD 1.5 + 2.5 ReMIX` | **1.0** | **miss** |
| `Kingdom Hearts HD 1.5 + 2.5 ReMIX` | same | 1.0 | hit |
| `Kingdom Hearts` | same | 0.75 (number guard) | miss |

The voice pipeline compounds this: `voice_context.match_candidates()` uses `fuzz.partial_ratio`, so a short transcript like `"Kingdom Hearts"` can score **100%** against the long library title and nudge Gemini toward a name that `resolve` then rejects.

**Code:** `app/api/v1/endpoints/games.py` → `resolve_game`.

#### 2. `_sanitize()` mangles `1.5+2.5` for IGDB search

For the Discord-formatted name, sanitization runs:

```
KINGDOM HEARTS -HD 1.5+2.5 ReMIX-
  → separators (-) become spaces
  → [^a-z0-9\s] strips . and +
  → "kingdom hearts  hd 1525 remix"
```

`1.5+2.5` becomes `1525` (digits concatenate). IGDB full-text search with `"kingdom hearts hd 1525 remix"` returns **zero hits**. Steam Store Search does return the Discord-exact listing (app `2552430`, confidence 1.0), so enrichment *can* succeed via Steam fallback — but only after IGDB fails.

For the canonical name `Kingdom Hearts HD 1.5 + 2.5 ReMIX`, sanitization yields `"kingdom hearts hd 15 25 remix"`, which IGDB matches at confidence 1.0. Steam returns no results for that query.

| `primary_name` | Sanitized search term | IGDB | Steam |
|---|---|---|---|
| `KINGDOM HEARTS -HD 1.5+2.5 ReMIX-` | `kingdom hearts hd 1525 remix` | 0 results | hit (2552430) |
| `Kingdom Hearts HD 1.5 + 2.5 ReMIX` | `kingdom hearts hd 15 25 remix` | 1.0 | no results |

**Code:** `app/tasks/enrichment.py` → `_sanitize`, `_igdb_search`, `_steam_search`.

#### 3. Bot `get_or_create_game` matches aliases by exact string

When Discord changes the rich-presence format (spacing, casing, `-HD` prefix, glued `1.5+2.5`), the bot treats it as a new game and inserts a fresh stub + alias. No fuzzy dedup at write time. Merges are manual (`POST /games/{id}/merge/{target_id}`) or ops SQL.

**Code:** `app/bot/session_manager.py` → `get_or_create_game`.

#### 4. Stale `NEEDS_REVIEW` after transient enrichment failure

Game `id=45` (`Kingdom Hearts HD 1.5 + 2.5 ReMIX`) had 26 sessions but `enrichment_status=NEEDS_REVIEW` with no cover. Re-running `_igdb_search` on the canonical name returned confidence 1.0. The row was likely marked `NEEDS_REVIEW` when enrichment failed on first queue (429, missing credentials, worker error) and was never re-queued. Enrichment runs once on stub creation; there is no automatic retry for settled `NEEDS_REVIEW` rows except manual `enrich_game` dispatch or `backfill_metadata` (ENRICHED rows with empty genres only).

### Suggested proper fixes

Priority order reflects impact and coupling to existing machinery.

#### P1 — Fuzzy `GET /games/resolve` (or shared matcher)

Replace exact equality with the same `_confidence()` pipeline used by enrichment, scoped to the caller's library (`primary_name` + all aliases for games with visible sessions). Return the best candidate if score ≥ `CONFIDENCE_THRESHOLD` (0.85); break ties by preferring ENRICHED over NEEDS_REVIEW, then most recent session.

Alternative: extract `match_library_game(name, candidates) -> Game | None` into `app/services/game_matching.py` and share between resolve, voice post-processing, and the planned manual-tracking `GET /games/suggest` ([manual-game-tracking.md](manual-game-tracking.md)).

Add API tests for Discord-vs-canonical pairs (this incident as regression fixture).

#### P1 — Preserve version-number tokens in `_sanitize()`

Before stripping non-alphanumeric characters, normalise common version patterns:

- `1.5+2.5` / `1.5 + 2.5` → `1 5 2 5` or `15 25` (consistent with spaced canonical titles)
- Optionally treat `+` between digits as a separator (like `-` / `:`) rather than deleting it

Goal: IGDB search term for the Discord string should recall the same candidates as the canonical name. Add unit tests in `tests/unit/test_confidence.py` with fixture `"KINGDOM HEARTS -HD 1.5+2.5 ReMIX-"`.

#### P2 — Bot alias discovery on near-miss

On `get_or_create_game` miss, optionally score the incoming `process_name` against existing aliases + primary names (same `_confidence`). If above threshold, attach a new alias to the matched game instead of creating a stub. Guard with logging and a high threshold to avoid false merges across sequels (number guard already helps).

#### P2 — Re-queue enrichment for settled `NEEDS_REVIEW`

Periodic Beat task or admin action: re-run `enrich_game` for `NEEDS_REVIEW` rows older than N hours with no `external_api_id`. Prevents permanent stuck state after transient API failures.

#### P3 — Normalise `primary_name` on ENRICHED match

When enrichment succeeds, optionally update `primary_name` to the IGDB/Steam canonical string (keep all Discord aliases). Reduces duplicate stubs that differ only in casing (`Remix` vs `ReMIX`). Requires careful UX: mobile library labels would change.

### Related docs

- [game-matching.md](game-matching.md) — `_sanitize`, WRatio, number guard, search-vs-scoring gotcha
- [api.md](api.md) — `GET /games/resolve` contract
- [manual-game-tracking.md](manual-game-tracking.md) — planned fuzzy suggest / IGDB disambiguation
- [bot.md](bot.md) — presence → stub creation

---

## Heroes III ∙ Horn of the Abyss — abbreviated title below threshold (2026-06-21)

### Summary

Game `id=38` (`Heroes III ∙ Horn of the Abyss`, 4 sessions) is `NEEDS_REVIEW`. IGDB returns the correct row `Heroes of Might and Magic III: Horn of the Abyss` at confidence **0.75** (below 0.85). Steam returns no results (fan expansion, not on Steam). The `∙` separator (U+2219) is **not** the cause — `_sanitize()` strips it identically to `:`, `-`, or no separator; all variants score 0.75 vs IGDB.

### Root cause

Discord uses an abbreviated franchise name. WRatio between sanitized forms is exactly 0.75; number guard does not apply (both sides have digit set `{3}`). `partial_ratio` is 0.833 — still under 0.85. Re-queueing enrichment will not help; this is deterministic.

### Token-subset note

All tokens of the short title are a subset of the IGDB title (`{heroes, 3, horn, of, the, abyss}` ⊆ longer set). A token-subset boost (see enrichment v2 below) would likely auto-pass this case without LLM.

---

## Duplicate stubs — Skyrim Special Edition (2026-06-21)

### Summary

Two ENRICHED rows for the same game:

| id | `primary_name` / alias | sessions |
|---|---|---|
| 54 | `The Elder Scrolls V: Skyrim - Special Edition` | 6 |
| 57 | `Skyrim Special Edition` | 23 |

`_confidence` between them: **0.75**. Token subset: shorter title's tokens ⊆ longer (`{skyrim, special, edition}` ⊆ full name). Discord reported different strings at different times; bot `get_or_create_game` matched neither against the other (exact alias only).

**Interim remediation (2026-06-21):** Merged `id=54` → `id=57` (23 sessions, `Skyrim Special Edition`). Both presence strings now alias game 57: `Skyrim Special Edition`, `The Elder Scrolls V: Skyrim - Special Edition`.

---

## Enrichment v2 — token subset + LLM adjudicator (design sketch)

**Status:** Design only — not implemented.

### Problem class

Three recurring failure modes share the same shape: **retrieval succeeds, deterministic score fails, duplicate `games` rows accumulate.**

| Incident | Discord / stub name | Canonical / other stub | `_confidence` | Token subset | IGDB top-1 |
|---|---|---|---|---|---|
| Heroes III HotA | `Heroes III ∙ Horn of the Abyss` | `Heroes of Might and Magic III: Horn of the Abyss` | 0.75 | short ⊆ long ✓ | correct |
| Skyrim SE (dupes) | `Skyrim Special Edition` | `The Elder Scrolls V: Skyrim - Special Edition` | 0.75 | short ⊆ long ✓ | both ENRICHED |
| Kingdom Hearts | `KINGDOM HEARTS -HD 1.5+2.5 ReMIX-` | `Kingdom Hearts HD 1.5 + 2.5 Remix` | 1.0 | neither ⊆ | search recall broken |

Token subset fixes Heroes and Skyrim scoring; Kingdom Hearts needs **alias linking** (and `_sanitize` version-token fix for IGDB search). LLM covers all three when rules are insufficient, and is the natural place to **propose aliases** and **link to existing DB rows** before inserting a new stub.

### Architecture — tiered pipeline

```
raw name (Discord presence or stub primary_name)
      │
      ▼
  _sanitize() + IGDB/Steam search (unchanged)
      │
      ▼
  _confidence() + number guard (unchanged)
      │
      ├── score ≥ 0.85 ──────────────────────────► ENRICHED
      │
      ├── number guard fired (digit sets differ) ─► NEEDS_REVIEW (no LLM)
      │
      ├── token-subset boost (new, see below)
      │     short tokens ⊆ long tokens AND digit sets equal AND |short| ≥ 3
      │     └── boosted ≥ 0.85 ───────────────────► ENRICHED
      │
      ├── score 0.70–0.85, guard silent ─────────► LLM adjudicator (new)
      │     inputs: raw name + top-N IGDB/Steam candidates + scores
      │     └── match + high LLM confidence ──────► ENRICHED + alias raw name
      │
      └── else ───────────────────────────────────► NEEDS_REVIEW
```

**Bot write path (parallel hook, P2):** before `get_or_create_game` inserts a stub, query global `game_aliases` + `primary_name` fuzzy match; on 0.70–0.85 (or LLM yes), **attach alias** to existing `game_id` instead of `INSERT`. This prevents Skyrim-class duplicates at source. Enrichment v2 and bot dedup share `app/services/game_matching.py`.

### Tier 1.5 — token-subset boost (deterministic, try first)

Add to `_confidence()` after WRatio + number guard:

```python
tokens_a = set(_sanitize(a).split())
tokens_b = set(_sanitize(b).split())
nums_a, nums_b = ...  # existing digit sets

if (
    len(tokens_a) >= 3
    and tokens_a <= tokens_b   # strict subset (not equal)
    and nums_a == nums_b
    and (nums_a or nums_b)     # at least one side has a series number
):
    score = max(score, 0.90)
```

| Pair | Effect |
|---|---|
| Heroes III HotA vs IGDB | 0.75 → **0.90**, pass |
| Skyrim SE long vs short | 0.75 → **0.90**, pass (dedup scoring) |
| Witcher 3 vs Wild Hunt | unchanged (already 0.90 via WRatio) |
| Diablo II vs III | guard caps at 0.75; subset fails (`2` ∉ `{diablo,3}`) |
| Hades vs Hades II | guard caps; `{}` vs `{2}` |
| Kingdom Hearts Discord vs canonical | subset fails (glued `1525` tokens) — falls through to LLM or sanitize fix |

Unit tests: `tests/unit/test_confidence.py` — Heroes, Skyrim, Diablo II/III, Hades/Hades II, KH regression.

### Tier 2 — LLM adjudicator (Vertex Gemini, structured output)

Reuse the voice stack pattern (`response_schema`, `gemini-1.5-flash-002` via ADC). New module: `app/services/game_adjudication.py`.

**When to call (all must hold):**

- Best candidate score in **[0.70, 0.85)**
- Number guard **did not** fire on that candidate
- At least one candidate returned from IGDB or Steam
- `GCP_PROJECT` configured (fail open → `NEEDS_REVIEW` if not)

**When NOT to call:**

- Score ≥ 0.85 (already decided)
- Number guard fired (Diablo II vs III, Hades vs Hades II — deterministic sequel boundary)
- Score < 0.70 (candidates likely wrong — don't ask the model to guess)
- IGDB returned zero hits **and** Steam miss (nothing to adjudicate)

**Prompt inputs:**

```
raw_name: "Heroes III ∙ Horn of the Abyss"   # exact Discord string, not sanitized
candidates:
  - {index: 0, name: "Heroes of Might and Magic III: Horn of the Abyss", source: "igdb", score: 0.75, year: 2011}
  - {index: 1, name: "...", ...}
existing_global_matches:   # optional second pass — see below
  - {game_id: 57, primary_name: "Skyrim Special Edition", alias: "Skyrim Special Edition"}
```

**Response schema:**

```json
{
  "match_index": "integer | null",
  "same_as_existing_game_id": "integer | null",
  "confidence": "number 0-1",
  "proposed_aliases": ["string"],
  "reason": "string"
}
```

**Auto-commit rules (background worker):**

- `match_index` set AND `confidence ≥ 0.90` → ENRICHED from that candidate; `proposed_aliases` includes `raw_name`
- `same_as_existing_game_id` set AND `confidence ≥ 0.90` → do **not** enrich stub; bot hook or post-enrichment step adds `raw_name` as alias on existing row (merge sessions if stub already created — ops path)
- else → `NEEDS_REVIEW`

Log every adjudication: `raw_name`, candidates, response, action taken. Idempotent on retry.

**Existing DB dedup (Skyrim, Kingdom Hearts):** before creating a stub or on enrichment miss, pass top 5 **global** `games` rows where `_confidence(raw, primary_name or alias) ≥ 0.70`. LLM picks `same_as_existing_game_id` when Discord name is an alias variant of a row already in the DB. This is how Kingdom Hearts (`KINGDOM HEARTS -HD 1.5+2.5 ReMIX-` vs enriched `Remix` row) and Skyrim dupes get linked **without** a manual merge.

### Alias assignment — unified model

`game_aliases.discord_process_name` stores **exact** presence strings the bot received. Today one alias is created at stub insert; no automatic growth.

| Trigger | Alias action |
|---|---|
| Bot sees known alias | use existing `game_id` (unchanged) |
| Bot near-miss / LLM `same_as_existing_game_id` | `INSERT game_aliases (game_id, raw_name)` |
| Enrichment LLM match to IGDB | `INSERT game_aliases (game_id, raw_discord_name)` + optionally set `primary_name` to IGDB canonical |
| User merge API | reassign aliases (unchanged) |

Aliases feed `GET /games/resolve` (exact hit today; fuzzy after P1 resolve fix). The LLM proposes aliases; the system stores them verbatim — no model paraphrasing.

### Cost and ops

- Gemini Flash on borderline only — estimate sub-cent per call; volume = new Discord stubs that land in 0.70–0.85 band (small fraction if token-subset catches Heroes/Skyrim-class first)
- Celery worker: call via `asyncio.to_thread()` like IGDB; add per-worker rate limit; fail open on 429/quota
- Not a substitute for manual-game-tracking user pick on **mobile-typed** names — same adjudicator can power a pre-selected IGDB candidate in that wizard, but mobile still confirms

### Implementation order

| PR | Scope |
|---|---|
| 1 | Extract `app/services/game_matching.py` — `_confidence`, token-subset boost, shared by enrichment + resolve + bot |
| 2 | Token-subset tests + Heroes/Skyrim confidence fixtures |
| 3 | `game_adjudication.py` + enrichment hook (IGDB borderline band only) |
| 4 | Bot `get_or_create_game` pre-insert dedup (global alias + fuzzy + optional LLM) |
| 5 | Fuzzy `GET /games/resolve` (reuses same matcher) |
| 6 | `_sanitize` version-token fix (Kingdom Hearts IGDB search recall) |

### Open questions

- Auto-merge sessions when stub already inserted before LLM says "same as game_id=57"? Prefer bot hook (PR 4) before insert over retroactive merge.
- Update `primary_name` to IGDB canonical on ENRICHED match, or keep first-seen Discord string? Affects mobile library labels.
- Periodic sweep: re-run adjudicator on settled `NEEDS_REVIEW` with IGDB idempotency — or rely on PR 4 prevention only?
- RBAC: LLM auto-linking mutates global `games` / `game_aliases` — lower risk than merge, but still shared data.

### Related docs

- [game-matching.md](game-matching.md) — current deterministic pipeline
- [manual-game-tracking.md](manual-game-tracking.md) — user-facing IGDB pick (complementary, not replaced)
- Voice adjudication pattern — `app/api/v1/endpoints/voice.py`, `app/services/voice_context.py`
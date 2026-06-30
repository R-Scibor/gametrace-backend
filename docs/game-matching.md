# Game Name Matching

When the Discord bot detects a game via `on_presence_update`, it records whatever name Discord provides — this can be anything from `"The Witcher 3: Wild Hunt"` to `"witcher3.exe"` to `"Baldur's Gate 3"`. The enrichment worker then queries IGDB to fetch canonical metadata (cover art, external ID). The matching pipeline decides whether a returned IGDB result is the same game.

## Pipeline overview

```
raw name from Discord
      │
      ▼
  _sanitize()          normalise both sides of every comparison
      │
      ▼
  fuzz.WRatio()        best-of-four fuzzy algorithm (rapidfuzz)
      │
      ▼
  number guard         cap score if digit sets differ
      │
      ▼
  score ≥ 0.85?
   ├── yes → ENRICHED (IGDB cover)
   └── no  → Steam exact-match fallback
                ├── hit → ENRICHED (Steam cover)
                └── miss → NEEDS_REVIEW
```

## Step 1 — `_sanitize(s)`

Applied to **both** sides of every comparison before any scoring. This lets the worker match `"witcher3.exe"` against `"The Witcher 3: Wild Hunt"` without false negatives from punctuation or formatting differences.

| Operation | Example |
|-----------|---------|
| Lowercase | `"The Witcher 3"` → `"the witcher 3"` |
| Strip file extension | `"witcher3.exe"` → `"witcher3"` |
| Remove `[bracketed]` content | `"Hades [GOTY]"` → `"hades"` |
| Remove `(parenthesised)` content | `"Game (2023)"` → `"game"` |
| `&` → `and` | `"Banjo & Kazooie"` → `"banjo and kazooie"` |
| Structural separators (`: - _`) → space | `"Dark Souls: Remastered"` → `"dark souls  remastered"` |
| Strip remaining non-alphanumeric | `"Assassin's Creed"` → `"assassins creed"` |
| Roman numerals → arabic digits (i–xv, standalone tokens) | `"Diablo IV"` → `"diablo 4"` |
| Collapse whitespace | `"dark souls  remastered"` → `"dark souls remastered"` |

Words stay space-separated. The whitespace strip needed for substring scoring (e.g. `"witcher3"` vs `"thewitcher3wildhunt"`) lives inside `_confidence`, not here — see "Search-query vs scoring" below.

### Known limitations

- **Parenthesis content is dropped entirely.** `"Dark Souls (Remastered)"` loses the word `"Remastered"`. The score usually still clears the threshold via WRatio partial matching, but information is gone.
- **Standalone `i` and `v` are treated as roman numerals.** A game title containing these as words (e.g. `"I Am Alive"`) gets digits injected (`"1 am alive"`). Same-game comparisons are unaffected since both sides transform identically, but cross-game comparisons involving such titles may produce unexpected number sets.
- **Non-ASCII characters are stripped.** `"Pokémon"` → `"pokmon"`. Because the same transformation applies to both sides, the match still works for the same title; it only fails if the two sides use different encodings of the same accented character (rare in practice).
- **Identical titles across distinct releases (reboots / remakes).** When IGDB contains multiple games with the exact same name (e.g. the 2014/2017 "Lords of the Fallen" vs the 2023 reboot), they all score 1.0. The first result in the IGDB search response wins; there is no year, platform, or recency tie-breaker. This attaches the wrong cover + metadata. See the Lords of the Fallen incident in [tech-debt.md](tech-debt.md). Workaround: use `POST /api/v1/games/match` (returns candidates with `year`) + `POST /games {igdb_id}` + merge if needed.

## Gotcha — search-query vs scoring sanitization

`_sanitize` is used in two different places and the requirements pull in opposite directions:

| Consumer | Needs |
|---|---|
| IGDB / Steam **search query** (the `term=…` we send the API) | Word boundaries preserved — both APIs run word-tokenized full-text search; a glued blob like `thefarmerwasreplaced` or `europauniversalis5` matches **nothing** |
| `_confidence` **scoring** of fetched candidates | Spaces stripped — so `partial_ratio` finds `"witcher3"` as a substring of `"thewitcher3wildhunt"` (~0.90); with the space between `"witcher"` and `"3"` it only reaches ~0.80 |

Resolution: `_sanitize` keeps spaces. The whitespace strip is local to `_confidence` (`sa.replace(' ', '')`). This was an actual regression — the bot couldn't enrich titles like *The Farmer Was Replaced* or *Europa Universalis V* because IGDB returned zero hits for the glued query. Don't re-introduce a `''.join` in `_sanitize`.

## Step 2 — `fuzz.WRatio`

`rapidfuzz.fuzz.WRatio` picks the highest score among four algorithms run on the sanitized strings (with whitespace stripped inside `_confidence` for substring alignment, as noted above):

| Algorithm | Handles |
|-----------|---------|
| `ratio` | Overall edit distance |
| `partial_ratio` | One string is a substring of the other |
| `token_sort_ratio` | Same words, different order |
| `token_set_ratio` | One string contains all tokens of the other plus extras |

This is why `"The Witcher 3"` vs `"The Witcher 3: Wild Hunt"` scores ~0.90 — `token_set_ratio` finds `"the witcher 3"` fully contained in the longer string.

The previous implementation used `difflib.SequenceMatcher.ratio()`, which penalises length differences. `"The Witcher 3"` vs `"The Witcher 3: Wild Hunt"` scored ~0.70 with difflib — below the 0.85 threshold.

## Step 3 — Number guard

WRatio's `token_set_ratio` sees `"hades"` as fully contained in `"hades 2"` and returns ~0.95 — indistinguishable from a genuine match. A number difference signals a different series entry, not a subtitle variant.

After computing WRatio, digit sequences are extracted from both sanitized strings. If the sets differ and at least one string has digits, the score is capped at `0.75` (below the `0.85` threshold):

```python
nums_a = set(re.findall(r'\d+', sanitized_a))  # {"3"}
nums_b = set(re.findall(r'\d+', sanitized_b))  # {"4"}
if (nums_a or nums_b) and nums_a != nums_b:
    score = min(score, 0.75)
```

### Examples

| Pair | Digit sets | Result |
|------|-----------|--------|
| `"Hades"` vs `"Hades II"` | `{}` vs `{2}` | capped → 0.75 |
| `"Diablo 3"` vs `"Diablo 4"` | `{3}` vs `{4}` | capped → 0.75 |
| `"FIFA 23"` vs `"FIFA 24"` | `{23}` vs `{24}` | capped → 0.75 |
| `"The Witcher 3"` vs `"The Witcher 3: Wild Hunt"` | `{3}` vs `{3}` | no penalty → 0.90 |
| `"Cyberpunk 2077"` vs `"Cyberpunk 2077: Phantom Liberty"` | `{2077}` vs `{2077}` | no penalty → 0.90 |
| `"Dark Souls"` vs `"Dark Souls: Remastered"` | `{}` vs `{}` | no penalty → WRatio result |

### Known limitation

Architecture and API-version numbers embedded in process names contain digits:

| Process name | Sanitized | Digit set |
|---|---|---|
| `GameName-Win64-Shipping.exe` | `gamename win64 shipping` | `{64}` |
| `game_dx11.exe` | `game dx11` | `{11}` |
| `game64.exe` | `game64` | `{64}` |

If the canonical IGDB game name has no number, these trigger a false cap and the enrichment falls through to Steam or `NEEDS_REVIEW`. Platform token stripping is not implemented — the assumption is that Discord rich presence typically exposes the game's display name, not the raw process name.

## Step 4 — IGDB search and `alternative_names`

The query is sent using the sanitized name to strip process-name noise before hitting the API:

```
search "sanitized name"; fields name,cover.url,cover.image_id,alternative_names.name; limit 5;
```

For each returned result, `_confidence` is computed against the **primary name and all `alternative_names`**; the maximum score is used. This matters for games with different regional names — the English process name may score poorly against a localized primary name but perfectly against the stored English alternative.

Cover URLs are normalized: `//…` → `https://…`, `/t_thumb/` → `/t_cover_big/` (vertical box art, ~264×352 px).

## Step 5 — Steam fallback

If IGDB confidence is below 0.85, the Steam Store Search API is queried with the **sanitized** name (same `_sanitize()` as IGDB). Each returned result is scored with `_confidence()`; the best candidate must reach `CONFIDENCE_THRESHOLD` (0.85). On a hit: `ENRICHED` with `external_api_id = Steam AppID` and cover `library_600x900.jpg`.

## Company canonicalization

IGDB's `involved_companies` list often contains both a subsidiary and its parent publisher (e.g. Cognosphere and HoYoverse for the same game). Without normalization, company-playtime stats would double-count the same publisher under two names. The enrichment worker canonicalizes publishers at write time so the stored list is already clean.

### Publisher rollup

Each publisher entry is resolved to a canonical name in three steps:

1. **Parent-or-self:** if the company has a `parent` link in IGDB, use the parent's name; otherwise use the company's own name. Only one hop is followed — grandparent chains are not traversed.
2. **Alias map:** the resolved name is looked up in `PUBLISHER_ALIASES` (keyed by casefolded name). This catches subsidiaries that IGDB does not yet link to a parent.
3. **Dedupe:** the resulting list is deduplicated with order-preserving, case-insensitive comparison; first occurrence wins.

### The alias map

The alias map lives in `app/services/company_resolution.py` as a code-maintained dict, keyed by **casefolded** company name. It is the fallback for subsidiaries (or equivalent regional partners) that IGDB has no `parent` link for. There is no runtime configuration — adding an entry is a code change plus a backfill.

Current entries:

| Alias key (casefolded) | Resolves to | Why |
|---|---|---|
| `cognosphere` | `miHoYo` | Global publishing arm; IGDB roots it at miHoYo |
| `hoyoverse` | `miHoYo` | Brand label; IGDB roots it at miHoYo |
| `iwplay` | `Perfect World Games` | Taiwan/SEA regional operator |
| `iwplay world` | `Perfect World Games` | Same operator, longer form |
| `iwplay world interactive entertainment` | `Perfect World Games` | Same operator, full legal name |

**The binding rule:** an alias value must equal the IGDB parent-chain **root** for that entity (the name parent-rollup would produce when IGDB *does* have the link). If it doesn't, a game where IGDB carries the parent link resolves one way and a game where IGDB omits it resolves another — and the same publisher splits into two stats buckets, which is exactly the double-count this feature removes. Because IGDB roots both Cognosphere and HoYoverse at `miHoYo`, the aliases do too; because Iwplay has no IGDB parent at all, its value is chosen to match how `Perfect World Games` (its real corporate operator) already resolves.

### Worked example: Zenless Zone Zero

IGDB returns three publisher entries for this game (verified via a live `involved_companies` query):

| IGDB publisher | IGDB `parent` | Resolves via | → |
|---|---|---|---|
| `miHoYo` | *(none)* | parent-or-self | `miHoYo` |
| `HoYoverse` | `miHoYo` | **parent rollup** | `miHoYo` |
| `Cognosphere` | `miHoYo` | **parent rollup** | `miHoYo` |

All three collapse via step 1 (parent rollup), step 3 dedupes → stored as `["miHoYo"]`. The alias map never fires here — the `parent` links are present, so resolution finishes before the alias lookup matters. The aliases exist only for the *gap* case: a game where IGDB lists `Cognosphere` or `HoYoverse` with no parent link, where the alias backstops to the same `miHoYo` bucket.

Contrast *Neverness to Everness*, where IGDB lists `Iwplay World Interactive Entertainment` (no parent) alongside `Perfect World Games`. With no IGDB link between them, only the alias collapses Iwplay into `Perfect World Games`; without it the two would count separately.

### Adding a new alias

1. **Find the IGDB root.** Query the game's companies and follow the `parent` chain:
   ```bash
   docker compose exec -T worker python -c "
   import httpx, json
   from app.core.config import settings
   from app.tasks.igdb_auth import get_igdb_token
   tok = get_igdb_token()
   r = httpx.post('https://api.igdb.com/v4/games',
     headers={'Client-ID': settings.igdb_client_id, 'Authorization': f'Bearer {tok}', 'Content-Type': 'text/plain'},
     content='search \"GAME NAME\"; fields name,involved_companies.company.name,involved_companies.company.parent.name,involved_companies.developer,involved_companies.publisher; limit 2;')
   print(json.dumps(r.json(), indent=2, ensure_ascii=False))
   "
   ```
   If IGDB already gives the subsidiary a `parent`, you usually need **no** alias — rollup handles it. Add an alias only when the `parent` is missing.
2. **Add the entry** to `PUBLISHER_ALIASES` in `app/services/company_resolution.py`: a casefolded key → the canonical root name. The value must match what parent-rollup produces for the same entity (see the binding rule above).
3. **Add a test** in `tests/unit/test_company_resolution.py` mirroring the existing alias tests.
4. **Re-process history** so existing rows pick up the new mapping — the alias is applied at enrichment write time, not at query time. Run the full backfill described in [Correcting historical rows](#correcting-historical-rows).

### Why developers are not rolled up

Developer entries go through dedupe only — no parent rollup. Multiple studios credited on a single game represent genuine co-development; rolling a studio up to its owning publisher would destroy studio-level attribution. For example, collapsing every first-party studio into its parent publisher would make it impossible to distinguish their individual output in library stats.

### Correcting historical rows

Games enriched before this pipeline was added retain their original (possibly double-counted) publisher lists. To re-process them, run the full backfill manually via the worker:

```bash
docker compose exec worker celery -A app.core.celery_app call tasks.backfill_metadata --kwargs '{"full": true}'
```

Without `full=true`, `backfill_metadata` only re-queues ENRICHED games that have an empty genres list. Passing `full=true` re-queues every ENRICHED game regardless of existing metadata. The existing IGDB rate-limit backoff and Redis dedup key (`enrich_game_{game_id}`) prevent duplicate queuing.

## Threshold and constants

| Constant | Value | Purpose |
|---|---|---|
| `CONFIDENCE_THRESHOLD` | `0.85` | Minimum score to accept an IGDB match |
| `_NUMBER_MISMATCH_CAP` | `0.75` | Score ceiling when digit sets differ |

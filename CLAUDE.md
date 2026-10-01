# MINCE Lottery — Claude Context

## Project Overview

Weighted lottery system for MINCE club popup events. Entries are MIT-affiliated participants; spots are allocated via exponential weighted random sampling based on accumulated lottery history. Groups can enter together (scored by the member with the lowest score).

## How to Run

```bash
uv run lottery.py
```

Before running, edit `lottery.py` to set `current_popup_id` (passed to `Database(...)` in `main()`) to the ID of the event being drawn.

## File Map

- `lottery.py` — Entry point; all configuration lives here
- `database.py` — Core logic: `Database` class, `Entry`/`Guest` dataclasses, CSV parsing (v1 and v2 sheets), scoring, and sampling
- `validation.py` — Validates folder/file structure (metadata sheet, lottery sheets per version, guest sheets) before any processing
- `email_validation.py` — MIT People API integration; classifies emails as STUDENT/STAFF/AFFILIATE/NON_MIT/INVALID
- `plot.ipynb` — Scratch notebook for plotting score distributions
- `TODO.md` — Backlog of algorithm / data-cleaning ideas
- `.claude/commands/clean-lottery.md` — The `/clean-lottery` slash command

## Data Layout

```text
history/
  popups.csv                # Master list of all events (name, date, id, lottery_version, group_type)
  lottery/{id}_lottery.csv  # Signup entries per event (format depends on lottery_version)
  guests/{id}_guests.csv    # Actual attendees per event (name, email); not required for the current popup
  problem_kerbs.yaml        # Kerbs with known API issues; treated as AFFILIATE

scores.csv                # Output: cumulative scores per email
past_attendance.csv       # Output: attendance history per email
affiliations.csv          # Output: email-type breakdown per popup
lottery_results_{id}.csv  # Output: selected winners for current event
```

### CSV Schemas

| File | Columns |
| ------ | --------- |
| popups.csv | `name`, `date` (YYYY.MM.DD), `id`, `lottery_version` (`1` or `2`), `group_type` (`solo`/`group`; required for v2, blank for v1) |
| lottery CSVs (v1) | `names`, `emails`, `notes` — exact match; names/emails are comma-separated for groups |
| lottery CSVs (v2, `solo`) | `name`, `email`, `notes`, plus optional `extraq_*` columns |
| lottery CSVs (v2, `group`) | `name`, `email`, `guest_name`, `guest_email`, `notes`, plus optional `extraq_*` columns |
| guests CSVs | `name`, `email` (one person per row) |
| scores.csv | `email`, `email_type`, `score`, `popups_attempted`, `popups_attended` |
| past_attendance.csv | `email`, `email_type`, `attended_popups` |
| affiliations.csv | `popup_id`, `date`, `student`, `staff`, `affiliate`, `non_mit`, `total` (one row per windowed popup, a `{id} (current)` row, and a `TOTAL (unique people)` row) |
| lottery_results | `names`, `emails`, `email_types`, `notes`, `score`, `weight`, `total_popups_attended`, `popups_attended`, then `extraq_1..N` if the sheet has extra questions |

### Lottery sheet versions

- **v1** (all popups up through `entropy`): one row per entry, groups packed into comma-separated `names`/`emails` cells. Error-prone — most `/clean-lottery` fixes are delimiter/count mismatches here.
- **v2** (starting with `denmark`): one column per person. `group_type` in `popups.csv` declares whether the form was `solo` (one person) or `group` (exactly two: `name`/`email` + `guest_name`/`guest_email`). Columns are checked with `columns_exist` (superset allowed), so extra form columns are fine.
- `reformat_sheet_v2()` in `database.py` converts v2 rows back into the v1 `names`/`emails` shape at load time, so all downstream parsing/scoring is version-agnostic.
- Any column whose name starts with `extraq` is carried through on `Entry.extra_qs` and echoed into `lottery_results_{id}.csv` (e.g. extra form questions).

## Configuration (in `lottery.py`)

All policy decisions are passed as plain Python functions. History-related ones go to `Database(...)`; draw-related ones go to `db.export_lottery_results(...)`. This makes it easy to experiment with different fairness or weighting strategies without touching core logic.

| Parameter | Passed to | Type | Meaning |
| ----------- | ----------- | ------ | --------- |
| `current_popup_id` | `Database` | `str` | ID of event being drawn — **change this each run** |
| `window_size_years` | `Database` | `int` | How far back history counts; older events are ignored entirely |
| `success_penalty_fn` | `Database` | `(score: float) -> float` | Applied to a person's score when they attend; e.g. `lambda x: x - 10` |
| `rebuild` | `Database` | `bool` | `True` forces a history replay, ignoring `.db_cache.pkl` |
| `num_samples` | `export_lottery_results` | `int` | Number of entries to draw (each entry may be 1–2 people for groups) |
| `group_score_reduce_fn` | `export_lottery_results` | `(scores: list[float]) -> float` | Reduces a group to a single score; e.g. `min` gates groups by their least-lucky member |
| `weighting_fn` | `export_lottery_results` | `(score: float) -> float` | Maps a score to a sampling weight; currently `lambda x: math.exp(x / temperature)` with `temperature = 0.5` |

You can swap any of these without touching `database.py`. For example:

- Use a linear weighting function to reduce the advantage of high scorers
- Use `mean` instead of `min` for `group_score_reduce_fn` to be less conservative about groups
- Shrink `window_size_years` for a shorter memory, or set it very large to treat all history equally
- Adjust `success_penalty_fn` to calibrate how much attending penalizes future chances

## Algorithm

1. **Accumulate:** For each past popup within the sliding window, every entrant who did not attend gets `score += 1`
2. **Penalize:** Every entrant who did attend gets `score = success_penalty_fn(score)`
3. **Group score:** `group_score_reduce_fn(member scores + 1)` — the group's draw score is derived from its members' individual scores; the +1 bias ensures a score of 0 still has nonzero weight
4. **Weight:** `weight = weighting_fn(group_score)` — converts scores to sampling probabilities; weights are normalized across all entries
5. **Sample:** `num_samples` entries drawn without replacement via numpy weighted sampling; each entry is 1–2 people, so headcount is in `[num_samples, 2*num_samples]`

Scores can go negative (e.g. if someone attends multiple events back-to-back). Only events within the past `window_size_years` years contribute to scores.

## Common Tasks

### Run a new lottery

1. Ensure `history/lottery/{id}_lottery.csv` exists with signups (columns per its `lottery_version`/`group_type`)
2. Ensure `history/popups.csv` last row matches the new event (name, date, id, lottery_version, group_type)
3. Set `current_popup_id="{id}"` in the `Database(...)` call in `lottery.py`
4. Run `uv run lottery.py`
5. Results in `lottery_results_{id}.csv`

### Clean lottery signup data

After collecting signups, raw CSV data often has formatting errors (missing emails, wrong delimiters, bare kerbs, etc.). Use the `/clean-lottery` slash command to run the full cleaning pipeline:

```text
/clean-lottery
```

This deletes `.db_cache.pkl` and runs `uv run lottery.py` first to identify all `[DATA] Dropping row` lines as the authoritative worklist, then applies four cleaning stages: normalizing email/name formatting, filling in missing names via the MIT People API, filling in missing emails via history cross-reference, and removing unrecoverable rows. It handles both v1 and v2 sheets. Outputs a `changes.md` log and a `review.md` for anything needing human review.

### Add a new popup to history

1. Append a row to `history/popups.csv` — dates must be in increasing order. New popups should use `lottery_version` `2` with `group_type` `solo` or `group`
2. Create `history/lottery/{id}_lottery.csv` with headers matching its version (v2 solo: `name,email,notes`; v2 group: `name,email,guest_name,guest_email,notes`; optional `extraq_*` columns after)
3. Create `history/guests/{id}_guests.csv` with headers: `name,email` (only required once the popup is no longer the last row)

### Record attendance after an event

Fill in `history/guests/{id}_guests.csv` with actual attendees. This data is used in future lottery runs to apply the success penalty.

## Database Internals

`Database` in [database.py](database.py) is the core engine. Construction does all the work; callers then call `export_*` methods to write results.

### State (built during `__init__`)

| Attribute | Type | Meaning |
| --- | --- | --- |
| `scores` | `dict[email, float]` | Running score per person across all windowed popups |
| `attempted` | `dict[email, list[str]]` | Popup IDs where person entered the lottery |
| `attended` | `dict[email, list[str]]` | Popup IDs where person actually attended |
| `email_types` | `dict[email, EmailType]` | Last-seen affiliation per person |
| `popup_entrant_types` | `dict[popup_id, dict[str, int]]` | Per-popup entrant counts by email type (for `affiliations.csv`) |
| `recent_popup_ids` | `dict[popup_id, (date, lottery_version, group_type)]` | Windowed past popups, in order |
| `current_popup_version` / `current_popup_group_type` | `int` / `str \| None` | Sheet format of the popup being drawn |

### Construction flow

1. `get_recent_popups()` — reads `history/popups.csv`, enforces strict ordering and unique IDs, asserts the last row is `current_popup_id` (recording its version/group type), filters past popups to the sliding window
2. Cache check — computes an MD5 fingerprint over all `history/` file mtimes + sizes, `window_size_years`, `current_popup_id`, and the bytecode of `success_penalty_fn`; if `rebuild=False`, loads `.db_cache.pkl` when the fingerprint matches **and** the cache is under 10 minutes old (skipping the remaining steps)
3. `check_history_folder()` — validates `popups.csv`, lottery sheets (v1 exact columns / v2 required columns), and guest sheets via `validation.py`; sets `data_valid=False` and returns on failure
4. `history_playback()` — iterates `recent_popup_ids` in order, calling `process_past_popup()` for each: adds `+1` to `scores` for each entrant, then applies `success_penalty_fn` to attendees
5. Writes cache to `.db_cache.pkl`

### Key helpers

- `load_lottery_entries(popup_id, lottery_version, group_type)` — reads a lottery CSV, applies `reformat_sheet_v2()` for v2 sheets, collects `extraq*` columns, and hands off to `parse_entries`
- `reformat_sheet_v2(rows, group_type)` — maps v2 `name`/`email` (+ `guest_name`/`guest_email` for `group`) into v1 `names`/`emails` strings
- `parse_entries(rows)` — parses rows into `Entry` objects; batches all email validation up front; handles deduplication (later rows win, removing the person from their prior group too)
- `process_row(names, emails, email_types, notes, extra_qs)` — validates a single row; drops on mismatched counts, blank emails (e.g. an empty `guest_email` in a v2 group sheet), invalid emails, or duplicate emails within a group. Blank *names* don't drop the row but log `[DATA] Blank name in row N` (dropping them would change historical v1 scores)
- `parse_guests(rows)` — same idea for guest CSVs; expects exactly one person per row

### Export methods

- `export_cumulative_data()` — writes `scores.csv` and `past_attendance.csv`
- `export_lottery_results(num_samples, group_score_reduce_fn, weighting_fn)` — loads the current popup's entries, computes group scores and weights, runs `np.random.choice` without replacement, writes `lottery_results_{id}.csv` (including `extraq_*` columns)
- `export_affiliations()` — writes `affiliations.csv` with per-popup email-type breakdowns (student/staff/affiliate/non_mit) and a global unique-person total row

## MIT People API

Used to classify emails and look up names during data cleaning.

**Endpoint:** `GET https://mit-people-v3.cloudhub.io/people/v3/people/{kerb}`

**Auth headers:** `client_id` and `client_secret` from `.env`

```bash
curl -H "client_id: $MIT_PEOPLE_API_CLIENT_ID" \
     -H "client_secret: $MIT_PEOPLE_API_CLIENT_SECRET" \
     "https://mit-people-v3.cloudhub.io/people/v3/people/KERB"
```

**Response:** JSON with `item.affiliations[0].type` — one of `student`, `staff`, or `affiliate`. Non-200 status means kerb not found.

**Kerb format:** MIT emails must match `[a-z0-9_]{2,8}@mit.edu` (2–8 lowercase alphanumeric/underscore chars). Anything that doesn't match is classified as `NON_MIT` without an API call. Note: kerbs longer than 8 chars (e.g. `verylongname@mit.edu`) are treated as non-MIT, not invalid.

**`NOT_FOUND` → `INVALID`:** If the API returns non-200 (kerb not in directory), the email is classified as `INVALID` and the entry is dropped. Alumni have `affiliate` records in the API and will be found normally. `NOT_FOUND` means the kerb genuinely doesn't exist — i.e., a typo. To manually allow a kerb that fails the API check, add it to `history/problem_kerbs.yaml` (those are unconditionally treated as `AFFILIATE`).

**`problem_kerbs.yaml`:** A YAML list of `{kerb: ...}` entries for kerbs that are known to be valid but return non-200 from the API (e.g. staff whose records are missing). These are short-circuited to `AFFILIATE` before the API is called.

**Used in two places:**

- `email_validation.py` — batches requests via `ThreadPoolExecutor` during every lottery run to classify all entry emails
- `/clean-lottery` Stage 1 — looks up legal names for group members whose name is missing

## Gotchas

- **popups.csv ordering is strict:** The last row must match `current_popup_id`. Dates must be monotonically increasing. The code asserts this.
- **popups.csv columns are exact:** Header must be exactly `name,date,id,lottery_version,group_type`. v1 rows leave `group_type` blank; v2 rows must set it to `solo` or `group`.
- **Email validation is slow:** Uses MIT People API with concurrent requests (`ThreadPoolExecutor`). Results are cached in-memory per run only.
- **MIT People API credentials:** Stored in `.env` as `MIT_PEOPLE_API_CLIENT_ID` and `MIT_PEOPLE_API_CLIENT_SECRET`. Required at runtime.
- **MIT emails not found in People API resolve to `INVALID`** — they are rejected as likely typos. Alumni have active `affiliate` records and are found normally. Add any kerb that legitimately fails the API to `history/problem_kerbs.yaml` as a manual override.
- **`AFFILIATE` emails are accepted** as valid entrants (not dropped).
- **Blank guest in a v2 group sheet drops the whole row:** `reformat_sheet_v2` always joins in `guest_name`/`guest_email`, so an empty guest becomes a blank email and the row is dropped with `blank email for ...`. This is intentional, so the case isn't silently guessed (forgot the guest vs. coming alone); `/clean-lottery` sends these to `review.md`.
- **Deduplication:** If a person re-submits, their old entry is fully removed — even from groups. The most recent submission wins.
- **Nepos:** "Nepos" (nepotism/invited guests) are added directly to `guests.csv` without going through the lottery. Document them in notes.
- **Cache is live:** `lottery.py` passes `rebuild=False`, so repeat runs within 10 minutes with unchanged `history/` and config reuse `.db_cache.pkl` and **skip history validation**. The fingerprint only covers `success_penalty_fn`; changing `group_score_reduce_fn`/`weighting_fn` doesn't invalidate it (they aren't used in playback). Set `rebuild=True` or delete `.db_cache.pkl` to force a full rebuild.
- **Python 3.10+ required:** Uses `match`/`case` statements.
- **No dry-run mode:** Running `lottery.py` always writes output files.

# MINCE Lottery — Claude Context

## Project Overview

Weighted lottery system for MINCE club popup events. Entries are MIT-affiliated participants; spots are allocated via exponential weighted random sampling based on accumulated lottery history. Groups can enter together (scored by the member with the lowest score).

## How to Run

```bash
uv run lottery.py
```

Before running, edit `lottery.py` to set `current_popup_id` to the ID of the event being drawn.

## File Map

- `lottery.py` — Entry point; all configuration lives here
- `database.py` — Core logic: `Database` class, `Entry`/`Guest` dataclasses, CSV parsing, scoring, and sampling
- `validation.py` — Validates folder/file structure before any processing
- `email_validation.py` — MIT People API integration; classifies emails as STUDENT/STAFF/AFFILIATE/NON_MIT/INVALID

## Data Layout

```text
history/
  popups.csv                # Master list of all events (name, date, id)
  lottery/{id}_lottery.csv  # Signup entries per event (names, emails, notes)
  guests/{id}_guests.csv    # Actual attendees per event (name, email)
  problem_kerbs.yaml        # Kerbs with known API issues; treated as AFFILIATE

scores.csv                # Output: cumulative scores per email
past_attendance.csv       # Output: attendance history per email
affiliations.csv          # Output: email-type breakdown per popup
lottery_results_{id}.csv  # Output: selected winners for current event
```

### CSV Schemas

| File | Columns |
| ------ | --------- |
| popups.csv | `name`, `date` (YYYY.MM.DD), `id` |
| lottery CSVs | `names`, `emails`, `notes` (names/emails are comma-separated for groups) |
| guests CSVs | `name`, `email` (one person per row) |
| scores.csv | `email`, `email_type`, `score`, `popups_attempted`, `popups_attended` |
| past_attendance.csv | `email`, `email_type`, `attended_popups` |
| affiliations.csv | `popup_id`, `date`, `student`, `staff`, `affiliate`, `non_mit`, `total` (one row per popup + a TOTAL row) |
| lottery_results | `names`, `emails`, `email_types`, `notes`, `score`, `weight`, `total_popups_attended`, `popups_attended` |

## Configuration (in `lottery.py`)

All policy decisions are passed as plain Python functions to `Database(...)`. This makes it easy to experiment with different fairness or weighting strategies without touching core logic.

| Parameter | Type | Meaning |
| ----------- | ------ | --------- |
| `current_popup_id` | `str` | ID of event being drawn — **change this each run** |
| `window_size_years` | `int` | How far back history counts; older events are ignored entirely |
| `success_penalty_fn` | `(score: float) -> float` | Applied to a person's score when they attend; e.g. `lambda x: x - 10` |
| `group_score_reduce_fn` | `(scores: list[float]) -> float` | Reduces a group to a single score; e.g. `min` gates groups by their least-lucky member |
| `weighting_fn` | `(score: float) -> float` | Maps a score to a sampling weight; e.g. `lambda x: math.exp(x / T)` for exponential |
| `num_samples` | `int` | Number of entries to draw (each entry may be 1–2 people for groups) |

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

1. Ensure `history/lottery/{id}_lottery.csv` exists with signups
2. Ensure `history/popups.csv` last row matches the new event (name, date, id)
3. Set `current_popup_id = "{id}"` in `lottery.py`
4. Run `uv run lottery.py`
5. Results in `lottery_results_{id}.csv`

### Clean lottery signup data

After collecting signups, raw CSV data often has formatting errors (missing emails, wrong delimiters, bare kerbs, etc.). Use the `/clean-lottery` slash command to run the full cleaning pipeline:

```text
/clean-lottery
```

This runs `uv run lottery.py` first to identify all `[DATA] Dropping row` lines as the authoritative worklist, then applies four cleaning stages: normalizing email/name formatting, filling in missing names via the MIT People API, filling in missing emails via history cross-reference, and removing unrecoverable rows. Outputs a `changes.md` log and a `review.md` for anything needing human review.

### Add a new popup to history

1. Append a row to `history/popups.csv` — dates must be in increasing order
2. Create `history/lottery/{id}_lottery.csv` with headers: `names,emails,notes`
3. Create `history/guests/{id}_guests.csv` with headers: `name,email`

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

### Construction flow

1. `check_history_folder()` — validates file/folder structure via `validation.py`; aborts on failure
2. `get_recent_popup_ids()` — reads `history/popups.csv`, enforces strict ordering, filters to the sliding window, returns `{id: date}` for all past popups (excludes `current_popup_id`)
3. Cache check — computes an MD5 fingerprint over all `history/` file mtimes + sizes, `window_size_years`, `current_popup_id`, and the bytecode of `success_penalty_fn`; loads `.db_cache.pkl` if fingerprint matches and `rebuild=False`
4. `history_playback()` — iterates `recent_popup_ids` in order, calling `process_past_popup()` for each: adds `+1` to `scores` for each entrant, then applies `success_penalty_fn` to attendees
5. Writes cache to `.db_cache.pkl`

### Key helpers

- `get_entries(rows)` — parses a lottery CSV into `Entry` objects; batches all email validation in one `ThreadPoolExecutor` call; handles deduplication (later rows win, removing the person from their prior group too)
- `process_row(names, emails, email_types)` — validates a single row; drops on mismatched counts, invalid emails, or duplicate emails within a group
- `get_guests(rows)` — same idea for guest CSVs; expects exactly one person per row

### Export methods

- `export_cumulative_data()` — writes `scores.csv` and `past_attendance.csv`
- `export_lottery_results(num_samples)` — reads the current popup's lottery CSV, computes group scores and weights, runs `np.random.choice` without replacement, writes `lottery_results_{id}.csv`
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
- **Email validation is slow:** Uses MIT People API with concurrent requests (`ThreadPoolExecutor`). Results are cached in-memory per run only.
- **MIT People API credentials:** Stored in `.env` as `MIT_PEOPLE_API_CLIENT_ID` and `MIT_PEOPLE_API_CLIENT_SECRET`. Required at runtime.
- **MIT emails not found in People API resolve to `INVALID`** — they are rejected as likely typos. Alumni have active `affiliate` records and are found normally. Add any kerb that legitimately fails the API to `history/problem_kerbs.yaml` as a manual override.
- **`AFFILIATE` emails are accepted** as valid entrants (not dropped).
- **Deduplication:** If a person re-submits, their old entry is fully removed — even from groups. The most recent submission wins.
- **Nepos:** "Nepos" (nepotism/invited guests) are added directly to `guests.csv` without going through the lottery. Document them in notes.
- **Cache is always bypassed:** `lottery.py` passes `rebuild=True`, so `.db_cache.pkl` is written but never read. The fingerprint-based cache system in `database.py` exists but is not exercised on normal runs.
- **Python 3.10+ required:** Uses `match`/`case` statements.
- **No dry-run mode:** Running `lottery.py` always writes output files.

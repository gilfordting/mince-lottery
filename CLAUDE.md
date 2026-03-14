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
  popups.csv              # Master list of all events (name, date, id)
  lottery/{id}_lottery.csv  # Signup entries per event (names, emails, notes)
  guests/{id}_guests.csv    # Actual attendees per event (name, email)

scores.csv                # Output: cumulative scores per email
past_attendance.csv       # Output: attendance history per email
lottery_results_{id}.csv  # Output: selected winners for current event
```

### CSV Schemas

| File | Columns |
| ------ | --------- |
| popups.csv | `name`, `date` (YYYY.MM.DD), `id` |
| lottery CSVs | `names`, `emails`, `notes` (names/emails are comma-separated for groups) |
| guests CSVs | `name`, `email` (one person per row) |
| scores.csv | `email`, `score`, `popups_attempted`, `popups_attended` |
| past_attendance.csv | `email`, `attended_popups` |
| lottery_results | `names`, `emails`, `notes`, `score`, `weight`, `total_popups_attended`, `popups_attended` |

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
4. Run `python lottery.py`
5. Results in `lottery_results_{id}.csv`

### Add a new popup to history

1. Append a row to `history/popups.csv` — dates must be in increasing order
2. Create `history/lottery/{id}_lottery.csv` with headers: `names,emails,notes`
3. Create `history/guests/{id}_guests.csv` with headers: `name,email`

### Record attendance after an event

Fill in `history/guests/{id}_guests.csv` with actual attendees. This data is used in future lottery runs to apply the success penalty.

## Gotchas

- **popups.csv ordering is strict:** The last row must match `current_popup_id`. Dates must be monotonically increasing. The code asserts this.
- **Email validation is slow:** Uses MIT People API with concurrent requests (`ThreadPoolExecutor`). Results are cached in-memory per run only.
- **MIT People API credentials:** Stored in `.env` as `MIT_PEOPLE_API_CLIENT_ID` and `MIT_PEOPLE_API_CLIENT_SECRET`. Required at runtime.
- **MIT emails not found in People API resolve to `NON_MIT`**, not `INVALID` — they are allowed to enter the lottery. This affects alumni and possible typos alike (no way to distinguish without further context).
- **`AFFILIATE` emails are accepted** as valid entrants (not dropped).
- **Deduplication:** If a person re-submits, their old entry is fully removed — even from groups. The most recent submission wins.
- **Nepos:** "Nepos" (nepotism/invited guests) are added directly to `guests.csv` without going through the lottery. Document them in notes.
- **Cache is always bypassed:** `lottery.py` passes `rebuild=True`, so `.db_cache.pkl` is written but never read. The fingerprint-based cache system in `database.py` exists but is not exercised on normal runs.
- **Python 3.10+ required:** Uses `match`/`case` statements.
- **No dry-run mode:** Running `lottery.py` always writes output files.

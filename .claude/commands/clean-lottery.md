# clean-lottery

Clean and repair lottery signup CSV data. Fixes name/email mismatches, fills in missing group member emails, and removes irrecoverable rows. Logs all changes to `changes.md` and flags unresolvable issues in `review.md`.

## Sheet formats

Check `history/popups.csv` first: each popup's `lottery_version` and `group_type` determine how its lottery CSV is laid out.

- **v1** (`lottery_version` = `1`): columns `names,emails,notes`. Group members are packed into one cell, comma-separated (e.g. `"Alice Hall, Hannah Ono"` / `"ahall@mit.edu, hono@mit.edu"`). Most of the fixes below were written for this format.
- **v2 solo** (`lottery_version` = `2`, `group_type` = `solo`): columns `name,email,notes` (+ optional `extraq_*`). Exactly one person per row.
- **v2 group** (`lottery_version` = `2`, `group_type` = `group`): columns `name,email,guest_name,guest_email,notes` (+ optional `extraq_*`). Exactly two people per row.

At load time, `reformat_sheet_v2()` in `database.py` joins v2 columns into v1-style strings (`names = "{name}, {guest_name}"`, `emails = "{email}, {guest_email}"`), so the `[DATA] Dropping row` messages for v2 sheets still show `names`/`emails` lists. When fixing a v2 sheet, edit the individual `name`/`email`/`guest_name`/`guest_email` cells. Never add comma-joined values to a v2 cell, and never rename or remove the `extraq_*` columns.

## Pre-flight

Before any cleaning, delete the database cache and run the lottery:

```bash
rm -f .db_cache.pkl
uv run lottery.py
```

Deleting `.db_cache.pkl` matters. `lottery.py` passes `rebuild=False`, so a re-run within 10 minutes with unchanged `history/` files loads the cache and skips history playback. That hides every drop from past popups. Editing a CSV changes the fingerprint and invalidates the cache, but delete it before each re-run anyway to be safe.

This will take a bit of time, because it spins up a lot of network requests. Do not truncate the output with `head` or `tail`; run it once and take the full output.

Every `[DATA] Dropping row` and `[DATA] Blank name in row` line in the output is a cleaning target. Blank-name rows are still entered in the lottery, just with an empty name, but they should be fixed (see Stage 1). These lines don't name the file; each popup's drops come right after its `[DATA] Loaded N lottery entries for popup '<id>'` line, so use that to attribute them. Row numbers match the CSV's line numbers (the header is line 1, so the first data row is row 2). The script already validates every row and tells you exactly what will be dropped and why — use this as your authoritative worklist rather than scanning CSVs manually. Re-run after each stage to confirm the drop count decreases.

If the script aborts with `[ERROR]` lines (not `[DATA]`), the problem is structural: wrong columns, a bad `popups.csv` row, or an invalid email in a **guests** CSV. Fix or report those first, since cleaning can't proceed until validation passes.

## What this does

Run this after collecting a new batch of lottery signups, or to re-clean historical data that has errors. The cleaning pipeline has four stages:

### Stage 0 — Normalize raw email and name formatting

Run these mechanical fixes first so later stages see clean data.

**Email fixes (apply to every email in every row):**

1. **Kerbs missing domain** — bare MIT kerbs with no `@` → append `@mit.edu`
   - e.g. `benjikan` → `benjikan@mit.edu`, `yanswu` → `yanswu@mit.edu`

2. **Truncated domain** — `@mit` with no `.edu` → fix to `@mit.edu`
   - e.g. `ssalwan@mit` → `ssalwan@mit.edu`

3. **Wrong separator character in email** — `&`, `!`, or `.` used instead of `@`:
   - e.g. `haozh&mit.edu` → `haozh@mit.edu`, `cwickert!mit.edu` → `cwickert@mit.edu`, `ash3690.mit.edu` → `ash3690@mit.edu`

4. **Spaces in email addresses** — strip any internal or surrounding whitespace:
   - e.g. `tungtran @mit.edu` → `tungtran@mit.edu`

5. **Stray literal quote characters** inside email field values — remove them:
   - e.g. `"lshoji@mit.edu` or `yukam997@mit.edu"` → `lshoji@mit.edu`, `yukam997@mit.edu`

6. **Multiple emails joined by wrong delimiter** — when a single CSV email cell contains two or more emails separated by space, `and`, `&`, `or`, `;`, `:`, or similar:
   - **v1:** Split into individual emails, rejoin with `,`, and wrap the whole cell in CSV double-quotes so the comma is parsed correctly
     - e.g. `rumilee@mit.edu and matildas@mit.edu` → `"rumilee@mit.edu,matildas@mit.edu"`
     - e.g. `selenal@mit.edu ewong@mit.edu` → `"selenal@mit.edu,ewong@mit.edu"`
   - **v2 group:** if both emails ended up in `email` with `guest_email` blank, move the second one into `guest_email` (and the second name into `guest_name` if it was packed into `name` too)
   - **v2 solo:** a solo form can't hold two people — flag the row in `review.md` and leave it as-is

7. **Prose text in the email field** — after all kerb/domain fixes above are applied, any value that still doesn't look like `something@something.something` is not an email. This includes descriptions like `"second is non-MIT"`, `"Johnny has Harvard email"`, names like `"Rawisara Lohanimit"`, and freeform strings like `"harryh (and spouse)"`. The fix depends on context:
   - If it's the only email for a solo entry → **delete the entire row** — do not silently demote to a solo entry.
   - If it's the second email in a 2-person group → attempt to recover the real email via history cross-reference or the MIT People API (see Stage 2). If unrecoverable, **delete the entire row** — do not silently demote to a solo entry.
   - **Important**: run fixes 1–6 before this check, so bare kerbs like `gting` get normalized to `gting@mit.edu` first and aren't incorrectly flagged here
   - Please note that non-MIT emails should still be kept! Do not delete rows containing non-MIT emails; people who are not MIT students should still be allowed to enter the lottery.

8. **Duplicate emails within a row** — if a group entry lists the same email address for both people (e.g. `ojoshi@mit.edu, ojoshi@mit.edu`), **delete the row entirely** — do not silently demote to a solo entry.

**Name fixes (apply to every name cell):**

1. **Names joined by wrong delimiter** (v1 only; for v2 group, see email fix 6) — the proper delimiter for names is a comma (`,`). If you see `and`, `&`, `:`, `;`, space, a Chinese full-width comma (，), or two adjacent capitalized words with no separator → split and rejoin with `,`:
   - e.g. `Alice Hall Hannah Ono` → `Alice Hall, Hannah Ono`
   - e.g. `Eugene Yoo and Sebastian Prasanna` → `Eugene Yoo, Sebastian Prasanna`
   - e.g. `Wilson Cao，Jenny Cao` → `Wilson Cao, Jenny Cao`
   - e.g. `Sharvaa Selvan:Sarah Su` → `Sharvaa Selvan, Sarah Su`
   - Be conservative splitting space-only names — only split if the email count matches 2 and the split produces two plausible names

**After Stage 0**, re-run `rm -f .db_cache.pkl && uv run lottery.py`. The row counts for names and emails should now be accurate. Proceed to the next stages only once Stage 0's drops are resolved.

### Stage 1 — Fix rows with 2 emails but only 1 name

These are group entries where the submitter provided both emails but forgot to list both names.

These show up as `[DATA] Blank name in row N` lines. In v1 that's an empty slot in `names`; in v2 group it's a blank `guest_name` (or `name`) with the email filled in.

For each email whose owner's name is unknown, look it up via the MIT People API using the known kerb:

```bash
curl -H "client_id: $MIT_PEOPLE_API_CLIENT_ID" \
     -H "client_secret: $MIT_PEOPLE_API_CLIENT_SECRET" \
     "https://mit-people-v3.cloudhub.io/people/v3/people/KERB"
```

MIT People API credentials are in `.env`; these are the two environment variables above.

If the API returns a name, fill it in. If the API fails or returns nothing: **delete the entire row** — do not silently demote to a solo entry. Also:

- Normalize email capitalization (all lowercase)
- If a second "email" is a placeholder (TBD, "none", "guest", etc.) — check history for the partner's real email. If not found in history, **delete the entire row** — do not silently demote to a solo entry.

### Stage 2 — Fix rows with 2 names but only 1 email

Search all other lottery CSVs in `history/lottery/` for prior entries from the missing person. If their name appears in another file with a consistent email, use that email.

If not found in history: **delete the row entirely** — do not silently demote to a solo entry. Both group members are excluded. Do not guess kerbs or use the People API to look up the missing email. Please follow this rule strictly.

**v2 group:** this shows up as `Dropping row N; blank email for names: [name, guest_name], emails: [email, '']`, meaning `guest_name` is filled and `guest_email` is blank. Apply the same history cross-reference to fill in `guest_email`.

**v2 group, both guest fields blank:** shows up as `Dropping row N; blank email for names: [name, ''], emails: [email, '']`. The submitter left the guest empty, which might mean they forgot the guest or meant to come alone; the data alone can't tell which. The script drops these rows. Do **not** delete them, do **not** invent a guest, and do **not** convert them to solo entries. List each one in `review.md` (popup, row, name, email) so a human can follow up.

### Stage 3 — Verify and remove other bad rows

Re-run `rm -f .db_cache.pkl && uv run lottery.py` after Stage 2. Remaining drops should be structural issues:

- Rows where email count ≠ name count and it can't be reconciled: delete
- Rows with TBD/placeholder partner emails: delete

## Output files

After running, update:

- **`changes.md`** — auto-fixed changes. Log every row that was fixed or deleted: old names/emails → new names/emails, and how the fix was found (API lookup, historical cross-reference, etc.)
  - Please make sure that rows that were supposed to be deleted were not demoted to individual entries.
- **`review.md`** — changes that need human review. List rows that need to be manually reviewed, along with the reason.

## Key patterns to watch for

- **Concatenated names**: `"Alice Hall Hannah Ono"` — split on capital letter boundaries or known name separators
- **Email in wrong column**: Sometimes the second email lands in the `notes` column (or, in v2, in an `extraq_*` column) — check and move it to `emails` (v1) or `guest_email` (v2 group)
- **Swapped name/email cells** (v2): an email typed into `name` or a name typed into `email` — swap them back
- **Email case**: `Opalinav@mit.edu` → `opalinav@mit.edu`
- **Recurring errors**: Some people have the same error across multiple popup entries; if this happens, make a note in `review.md`
- **Preferred names**: MIT People API returns legal name; listed name may differ (e.g. "Arielsie" for "Yuanxi") — this is fine, keep the preferred name in the CSV

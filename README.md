# MINCE Lottery Algorithm

HOW TO RUN:

edit `lottery.py`, then run:

```sh
uv run lottery.py
```

## Principles

- weight should be min?max?average? of groups
- only outputs non-nepos
- but nepos should still be documented, in guest csv's

### Weighting function

- should take in a real number (i.e. either positive or negative)
- output a positive number
- increasing first derivative
  - the weight *gained* by going from 2 --> 3 popups missed to 6 --> 7 popups missed should not be equal
- also positive first derivative
- $e^x$ should work well
  - $e^{x/T}$

## database

- sliding window of 5 years (undergrad + MEng)

### deduplication

- if a single guest is then enetered in a pair again, get rid of them
- soft ban of no repeats within the same year, but you can still accumulate lottery points
- attending multiple popups should be weighted negatively
Only most recent submission will count. We deduplicate entries as follows:
- Upon receiving an entry for individual A, we remove past individual entries for A, as well as all group entries containing A.
- Upon receiving an entry for a group, we apply the above rule for all individuals in that group.

### extra info

- allergy? we always ask this, and we should probably always get the most up-to-date info; using the most recent entry is fine imo

## public info

- For every lottery you enter but do not receive a spot, you receive one (1) lottery point towards your cumulative total
- Total resets to 0 if you get in
- Cooldown of 1 year; *can still accumulate lottery points?*

## instructions

- upload guests for the MOST RECENT popup: columns name, email
- upload lottery info for the CURRENT popup: columns depend on the lottery version (see [Data formats](#data-formats))
- edit popups.csv with info of CURRENT popup (including `lottery_version` and `group_type`)
- then init database, and tell it to with ID of the CURRENT popup

## Data formats

### popups.csv

One row per popup, in date order. The last row must be the popup being drawn.

```csv
name,date,id,lottery_version,group_type
Entropy,2026.03.07,entropy,1,
Hygge,2026.04.04,denmark,2,solo
```

- `date`: `YYYY.MM.DD`, strictly increasing
- `id`: unique; names the files `history/lottery/{id}_lottery.csv` and `history/guests/{id}_guests.csv`
- `lottery_version`: `1` or `2` (see below)
- `group_type`: blank for v1; `solo` or `group` for v2

### Lottery sheets, v1 (popups up through `entropy`)

One row per entry. Group members are packed into a single cell, comma-separated, with names and emails in the same order.

```csv
names,emails,notes
Nicole Zheng,nmzheng@mit.edu,No
"Alice Hall, Hannah Ono","ahall@mit.edu, hono@mit.edu",vegetarian
```

- columns must be exactly `names,emails,notes`
- the number of names must equal the number of emails, or the row is dropped
- group size isn't enforced by the code: 1–2 people is the convention, but 3+ would be accepted
- error-prone in practice: wrong delimiters (`and`, `&`, `;`, spaces), missing names/emails, bare kerbs, etc. This is most of what `/clean-lottery` fixes

### Lottery sheets, v2 (starting with `denmark`)

One column per person. The popup's `group_type` decides which columns are required.

**solo**: exactly one person per row

```csv
name,email,notes,extraq_1,extraq_2
Sherry Zhang,sherry29@mit.edu,No,Movie nights,Laughs
```

**group**: exactly two people per row

```csv
name,email,guest_name,guest_email,notes,extraq_1
Alice Hall,ahall@mit.edu,Hannah Ono,hono@mit.edu,,vegetarian
Bob Lee,boblee@mit.edu,Carol Diaz,carol@gmail.com,nut allergy,
```

- required columns must exist; order doesn't matter and extra columns are allowed
- columns starting with `extraq` (extra form questions) are carried through to `lottery_results_{id}.csv`; any other extra column is ignored
- no commas inside cells (`Hall, Alice` breaks the row; v2 is converted to v1 internally by joining on commas)
- **group sheets can't hold solo entries**: a blank `guest_email` drops the whole row (logged as `blank email for ...`) and is sent to human review by `/clean-lottery`; a blank `guest_name` is accepted but logged as `Blank name in row N`
- groups of 3+ aren't possible in v2

### Rules for both versions

- every email must be valid; non-MIT emails are fine, but MIT kerbs not found in the MIT People API are rejected as typos (override in `history/problem_kerbs.yaml`)
- the same email twice in one entry drops the row
- re-submissions: the latest row wins, and the person's earlier entry is removed even if it was a group

### Guest sheets (`history/guests/{id}_guests.csv`)

Who actually attended, one person per row. Same format for every version.

```csv
name,email
Nicole Zheng,nmzheng@mit.edu
```

- every row needs a non-blank name and a valid email (otherwise validation fails and the run aborts)
- include nepos here too (they count as attending for future scoring)

## History of all past popup attendees, and lottery info

- one master sheet with popup info: date, name, ID
- for each popup, two sheets:
  - who went
  - who lotteried, deduplicated
    - can contain people who went -- this shouldn't matter
- database.py will construct a database from a sliding window of the last 5 years
- TODO: venue info?
- no email? give a unique ID that will not be duplicated

## AI

- CLAUDE.md -- might need to be periodically updated (put this in the CLAUDE.md itself?)
- /clean-lottery -- will not always be correct. after the changes have been documented, ask claude to review the changes and see if they followed the rules in clean-lottery.md; if not, fix them

## notes

- year is not actually used from the form data

# MINCE Lottery Algorithm

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
- upload lottery info for the CURRENT popup: columns names, emails, notes
- edit popups.csv with info of CURRENT popup
- then init database, and tell it to with ID of the CURRENT popup

## History of all past popup attendees, and lottery info

- one master sheet with popup info: date, name, ID
- for each popup, two sheets:
  - who went
  - who lotteried, deduplicated
    - can contain people who went -- this shouldn't matter
- database.py will construct a database from a sliding window of the last 5 years
- TODO: venue info?
- no email? give a unique ID that will not be duplicated

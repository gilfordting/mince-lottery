# Things to do

## Algorithm

- Take seniority into account somehow; score being equal, seniors should have priority
  - class year in form
- output group reflects the input distribution; if x% are first-timers in lottery submissions, x% in final 40?
- penalty for going to popup: figure this out. $n/x$?
  - ban of 1 year?
  - increasing negative penalty?
- guidelines for tuning temperature?
- weighting function?
- pairwise penalty, i.e. who's already gotten in should modify the rest of distribution (since it's drawing without replacement)
- higher priority for allergies?
- add a pre-activation bias, if we want to e.g. give vegetarians a higher chance for a given popup
- formatting validation in form

## Data cleaning

- More detail about what rows get dropped
  - common mistakes: mismatched number of names, emails
  - misspelled email
  - kerb only
  - wrong comma character
  - "tbd" guest, or "second is non-mit"
  - "and" instead of comma delimited, or &, or "or"
  - empty lmfao
- can inform what we put on the form?
- fix the errors?

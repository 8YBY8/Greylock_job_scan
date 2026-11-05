# Greylock job scan

Checks the 32 Greylock Techfair 2026 companies for new intern / new grad postings.
A Claude Code cloud routine runs it twice a day (9am / 5pm ET), adds matching US,
non-PhD-only roles to the Google Sheet, then commits the updated `state/` back here.

```
python3 -I greylock_jobs.py check  --state state   # list postings not seen before (writes state/pending.json)
python3 -I greylock_jobs.py commit --state state   # mark them as seen (merges into state/seen.json)
```

`state/seen.json` is the record of postings already checked; don't edit it by hand.

# Candidate selection without starvation

## Verified obstacle

Candidate queries fetched the newest `limit * 2` rows, then removed IDs already
used by that source and mode. With the default limit of 50, 100 recent used rows
could hide fresh unused signals entirely. That caused an empty candidate cycle
without consulting the unused candidates' qualified edge or current quotes.

## Change

Exclude the supplied seen-ID set inside the SQLite query before `LIMIT`.
A connection-local predicate avoids schema writes, huge SQL strings and bind
parameter limits. Returned rows remain bounded at 50 per source and are ordered
by newest timestamp, then newest ID for equal timestamps. No dependency changes.

Age, resolution, configured score and allowed signal-type filters remain.
Live calibration, direction conflicts, fee-aware quote checks, available funds,
position sizing, market minimums and drawdown controls continue after retrieval.
No threshold changes or live orders are part of validation.

## Validation and evaluation

- A regression with 200 recent used signals and 50 unused, fresh signals now
  retrieves all 50 unused rows. The old bounded query returned none.
- Verify stale, settled, low-score and disabled-type signals remain excluded.
- Verify same-second ordering, source isolation, zero limits and 40,000 seen IDs.
- Run the full backend suite and Practice with zero live buying power.
- In recorded replay/Practice, compare candidate counts, exclusion reasons,
  submitted orders versus actual fills, fees, net return and drawdowns. More
  discovered candidates are not evidence of improved returns. Keep the existing
  chronological model qualification and out-of-sample gates.

This fixes missed candidate evaluation; it does not prove profitable trading.

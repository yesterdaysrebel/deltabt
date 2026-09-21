# deltabt

**Read `CONTEXT.md` before planning anything.** It is the reconstructed state of the project — what
is deployed, what is closed and why, and the traps — and it replaces the lost session transcripts.

- Two halves: `deltabt/` research backtester (programme CLOSED 2026-08-23, no edge, the nulls are
  the deliverable) and `app/`+`live/` forward test. **`app/` must NEVER import `live/`** —
  `tests/live_exec/test_boundary_preserved.py` enforces it.
- **`README.md` is stale**: it still claims no order placement and no API keys. `live/` signs
  requests and places real orders on Delta testnet.
- **Every loss-cutting exit mechanism is measured dead** (`docs/exit_fill_review_2026-09-13.md`).
  Winners and losers pull back identically, so no give-back rule can separate them. The honest
  lever for "lose less" is position size, not stop width or a trail.
- **Results predating #54 have no loss tail** — the old engine filled every stop AT the stop price.
  Re-run on master with `stop_fill="ltp_close"`, and bracket any new result at
  `stop_fill_fraction` 0.0 and 1.0 before believing it.
- **Unpaired backtests cannot resolve an exit mechanism here.** `cooldown_bars` 11 → 12 swings the
  unchanged baseline by more than any effect ever found. Use paired variants and studentised max-T.
- **Merging is free; deploying is not.** The guard skips stacks with a RUNNING experiment, but any
  merge touching `user_data` replaces the host immediately, mid-experiment, even with termination
  protection on. `only_stack=X` bypasses the guard and resets the sample to zero.
- **Infra tests grep text, not wiring** — a green suite has shipped six broken links. Run
  `tofu plan` against real state (recipe in `CONTEXT.md`).
- **Update `CONTEXT.md`'s current-state sections in place** when a decision closes or a session
  ends — don't append a new dated entry.

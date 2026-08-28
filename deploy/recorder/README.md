# Option quote surface recorder

Captures what Delta does not keep.

The exchange serves candle history for traded premium and for mark price going
back to 2024-01, and none whatsoever for **quotes, implied vol or greeks**.
`best_bid`, `best_ask`, `bid_iv`, `ask_iv`, `mark_iv` and the greek set live
only in the current `/v2/tickers` response.

This matters because the quoted spread is the **larger half of the round trip**
on this venue -- 1.34% of mid at the median against a 1.18% fee term, on an
ATM straddle whose total friction is 6-11% of premium
(`docs/options_feasibility.md` §3). Until a real spread record exists, every
options backtest here is conditional on today's spread having applied on a past
date, which is an assumption and is labelled as one throughout.

## Running it

Locally, for a quick look:

    python -m deltabt.data.quote_recorder --once        # one snapshot, then exit
    python -m deltabt.data.quote_recorder               # poll every 900s

Durably, on a host that stays up:

    sudo cp deltabt-quote-recorder.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now deltabt-quote-recorder

A session-scoped background process is fine for a day and wrong for a quarter:
the gap left by a laptop closing cannot be backfilled from any endpoint.

## What lands on disk

One Parquet file per UTC day under `$DELTABT_DATA/quotes/`, appended per
snapshot and deduplicated on `(snapshot_ts, symbol)`. Roughly 103k rows/day at
the 900s default, near 1 GB/year.

Both the local poll instant and the exchange's own timestamp are stored, so
clock skew stays measurable rather than assumed away.

## When it becomes useful

Not immediately -- it is building a record, and a week of it says little. The
point at which it changes a conclusion is when there is enough history to
replace `DEFAULT_HALF_SPREAD_FRAC` in `deltabt/options_costs.py` with a
measured, time-varying, per-moneyness spread. Until then it costs one process
and about 3 MB a day.

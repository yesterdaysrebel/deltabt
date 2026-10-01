# CONTEXT — where this stands and what to do next

Written 2026-09-21. **Provenance:** reconstructed from the working tree, git history, and the
session memory notes — not from a transcript. Repo facts (layout, branch, code line numbers) were
re-verified against the checkout today. Deployed-state facts carry the UTC timestamp of the last
session that actually read the venue; re-verify anything load-bearing before acting on it.

The container is ephemeral. Everything that matters must be in git.

## What this repo is — two halves with one wall between them

**Research half** (`deltabt/`, `scripts/`, `out/`, `docs/`): a pre-registered backtest programme
on Delta Exchange India perpetuals. `docs/PROGRAM_SUMMARY.md` is its closing summary, written
2026-08-23: thirteen pre-registered hypothesis families, **no edge found**, and the nulls are the
deliverable. Its central result is the cost law — `cost_r = round_trip_rate / stop_pct`, round trip
a constant 0.158% on this venue — which is why a 1-minute strategy must beat ~42% win rate at 2R
before it earns anything, and why every intraday family died.

**Live half** (`app/` paper bot, `live/` real-venue execution, `deploy/`, `infra/`): the forward
test. `app/` must **NEVER** import `live/` — `tests/live_exec/test_boundary_preserved.py` enforces
it; cross the boundary through the registry in `app/forwardtest/identity.py`.

**`README.md` is stale on one point.** It still says "Backtest and research only. No order
placement, no API keys, no live trading." That was true until `live/` landed in #58 (2026-09-15).
The repo now signs requests and places real orders on Delta testnet. Do not quote the README's
safety claim to anyone.

## Deployed state — tnet last read 2026-09-28 10:16 UTC (paper stacks 2026-09-21)

| stack | venue | experiment | state |
|---|---|---|---|
| `atr` | paper | baseline `manual_scalp_both_t3` | rolled 09-15, hash `41e764beceaf` |
| `ladder` | paper | `MANUAL_SCALP_BOTH_T3_LADDER-5-20260916-1e5c102` | runs to **2026-09-30** |
| `ltp` | paper | `MANUAL_SCALP_BOTH_T3_LTP-5-20260916-1e5c102` | runs to **2026-09-30** |
| `tnet` | Delta **testnet** | `LIVE-MANUAL_SCALP_BOTH_T3-5-20260917-f08a017` | bound since 09-17 11:13 UTC |

All four are ungated (max_drawdown 1, max_daily_loss 1, consec_losses 0, cooldowns 0, min_rr 1) —
those are **global** Terraform vars, so every stack shares them.

`ladder` and `ltp` run to 2026-09-30 **by operator instruction**, with the stopping rule written in
`variables.tf` and `deltabt/catalog.py` before the first bar. **Expect UNDECIDED on P&L** (~24
trades/arm against ~350 needed for -0.166R/trade at 80% power). Read them for MECHANISM: how often
a rung arms, whether promoted stops cut trades that reached target, and LTP-arm stop overshoot
against the mark baseline.

`tnet` as of 2026-09-28 10:16 UTC (live pull: `scripts/report.sh status` + SSM probe): same host
`i-0161071571ade0664`, image `f08a017`, up 10 days, 0 restarts, healthz 200, no tracebacks in 72 h,
still bound. Ledger == venue on both open positions (BTC long 1 @ 83739; ETH short 7 @ 2670.85),
both bracketed, stops inside liquidation. 15 closed since binding: 4 take-profits (~+3R) and 11
stop-losses (~-1R) = about +0.56R, **net +$1.29 after $4.23 fees**. Since the 09-22 read (+$13.08 on
5 closed): 10 closes, 1 win, 9 losses. Venue USD $435.75. SOL: 0 trades from 1,053 signals (book
guard). New reject reason seen: "position rounds to zero contracts" ×53. Ratios withheld (<30
trades). This sample ran **without a time stop** — see "Live and venue traps".

## Git state — the local refs lie

Fetched 2026-09-21: `origin/master` is **`f08a017`** — #86, "Use password database auth again",
which is exactly the image `tnet` runs. Local `master` is a stale ref 33 commits behind at #53;
**use `origin/master`, not `master`.**

The branch `fix/live-image-password-auth-for-now` @ `3e8bb42` is #86's pre-squash version and is
already merged — do not build on it. Before a rebuild, local refs had last been fetched
2026-09-17 10:41 UTC, which made `origin/master` read one PR behind reality; **`git fetch` before
reasoning about what is on master.**

## What is CLOSED, and why — do not re-test without new information

Full record: `docs/exit_fill_review_2026-09-13.md` (six blind external reviewers, own code), on
master since #54.

**Every loss-cutting exit mechanism on `manual_scalp_both_t3` is dead.** Breakeven at every
threshold; trailing across 33 cells; scale-out, 8 of 8 ladders negative; the operator's staircase
ladder (-0.048R vs +0.127R, t=-2.55, 3R targets reached collapse 73 → 9); adverse tightening, 16
cells, all below baseline; bare stop-limit (3 non-fills at -6.73, -7.41, -12.00R). Against the
engine's own fill, **nothing survives studentised max-T over 23 variants** (best t=+0.69, FWER
p=0.91). Only stop-limit + 5-minute market fallback does — and its sign depends on a fill
assumption 1m candles cannot check.

**The root cause, and the reason this is closed rather than untuned:** winners and losers pull back
*identically*. Deepest give-back from a trade's own running peak while in profit, over 385 trades:
winners that reached +3R, median 1.50R; losers that went green, median 1.51R. **Same distribution.**
No give-back distance separates them. The arm's edge IS its 3R tail, so every give-back rule taxes
the edge. Keeping 90% of winners needs G ≥ 2.5R, by which point the stop never binds.

**A wider stop is not the answer either.** Money lost on losing trades falls monotonically with
stop width (robust, 11-cell grid) but the return improvement fails out-of-sample: choosing 6xATR on
blocks 0-1 costs **-0.0992 R/trade** on blocks 2-3. Per symbol on BEATUSD (92% of the sample) the
edge is flat, +0.115 at 4x vs +0.116 at 6x. Widening reduces losses only by trading less (336 → 202
trades) — a position-size cut dressed as a strategy change. **The honest lever for "lose less" is
position size**: same effect, reversible, no overfitting risk, and it does not change the arm's
identity mid-experiment.

**Do NOT drop a symbol.** The per-symbol ranking inverts live: AKEUSD backtest -0.241 vs live +0.757
(n=5); BEATUSD backtest +0.141 vs live -0.649 (n=7). Hold cap 72h is already the peak.

## Measurement rules this repo learned the hard way

- **The old engine had no loss tail.** `portfolio.py`/`engine.py` triggered stops on MARK and filled
  them AT `stop_price`, so nothing in `out/` predating #54 can contain a loss worse than ~-1.34R.
  Run on master with `stop_fill="ltp_close"` and `Book.fill_ltp`/`fill_mark` set. Bracket any new
  exit or stop result at `stop_fill_fraction` 0.0 and 1.0 before believing it.
- **`resample_tradable` was off by one bar** for the whole series and mislabelled 11-14% of bars.
  Every number recorded before that fix is suspect.
- **Unpaired backtests cannot resolve an exit mechanism here.** Holding the exit fixed and moving
  `cooldown_bars` 11 → 12 swings baseline sumR from -7.8R to +49.6R; every mechanism effect ever
  found (±20R) sits inside that. Only the stop TRIGGER and the stop FILL leave the entry set
  roughly intact and stay measurable.
- **Two yardsticks in this repo are wrong for paired work.** The *selection premium* is calibrated
  on unpaired entry-grid cell means — applied to paired exit/fill variants it would reject a real
  +0.049R repair carrying t=7.2. Use studentised max-T. And *"3 of 4 blocks"* is a 2.2:1 likelihood
  ratio, not a property: under a zero-edge null P(≥3 of 4) = 0.31, and blocks 0-2 are BEATUSD alone.
- **Live-13 replays flatter every rule twice over** — they hold entries fixed AND book stops at the
  stop price. The same trail reads +0.39R/trade on the live 13 and -0.035R on 385.
- **Paper trading can settle exactly one mechanism**, the ladder, precisely because its predicted
  effect is large and negative (~64 days, ~33R ≈ $1,500 of the $10k paper account). The gentler
  adverse tightenings need 4.6 to 30 years.
- `manual_scalp_both_t3` has **no pre-registered stopping rule** — the one in `catalog.py` belongs
  to `manual_scalp_cross_both_t3`, and the "30" in the daily report is `MIN_CLOSED_TRADES`, the
  report's own default.

## Live and venue traps

- **`/v2/positions` is single-product** and 400s without one; `/v2/positions/margined` enumerates.
  `/v2/orders/client-oid` is not a route — it is `/v2/orders/client_order_id/{cid}`. `/v2/products`
  names the key `id`, not `product_id`, and needs `contract_types=perpetual_futures`.
- **No slippage protection on market orders, no BBO or pegged type.** A marketable limit is the only
  way to cap a fill. A **loose** cap (1.5R beyond the stop) is free insurance: binds on 2 of 265 stop
  events, moves the mean by +0.0000, improves the worst trade -1.97R → -1.73R. Tight caps are all
  slightly negative.
- **Brackets are `pending`, not `open`** — `get_open_orders()` queries `states=open` and never lists
  SL/TP legs.
- **Leverage is inherited, not set** unless the leverage-from-stop path runs. Before #80, tnet
  inherited ETH 100x / BTC 50x and stops sat *beyond* liquidation: two positions were liquidated and
  the ledger recorded both as MANUAL_CLOSE, because `live.ledger.exit_reason` has no mapping for
  `liquidation_order`. The dataset hides it.
- **SOL is untradeable on testnet.** The book sits 1.3-1.9R off the signal feed, so the #83 guard
  refuses every entry (215 refusals in 48h, 0 trades since 2026-09-16 20:00 UTC). The guard is
  correct; SOL simply produces no data. The real throughput limiter is `already holding` — one
  position per symbol across only 3 symbols; MAX_OPEN=6 never binds.
- **`DELTABOT_SYMBOLS` is DEAD on the live path.** `live/config.py:symbols_for()` returns the venue
  universe. Do not read the container env as the traded universe.
- **Open bug:** `/api/positions` 500s on live — `app/api/app.py:137` reads `p.last_price` and
  `LivePosition` has no such attribute. Paper is unaffected; `/api/trades` is fine (its keys are
  `entry/stop/target/pnl/r/reason/opened_ist`, not DB column names).
- **The 72h time stop was paper-only until `fix/live-time-stop` (2026-09-28).** `DELTABOT_MAX_HOLD` →
  `max_hold_seconds` reaches settings and `risk_hash` (`app/config/settings.py:244`), but only
  `PaperBroker` enforced it; `LiveBroker` was built without it and nothing in `live/` read it. tnet
  (image `f08a017`) therefore never time-stops — a BTC long opened 2026-09-24 15:05 UTC was 91 h old
  on 09-28. The fix: `LiveBroker.max_hold_seconds` + `LiveTradingBot._enforce_time_stop()` (poll
  loop, every 60 s): market time minus the ledger's `opened_at` ≥ limit → reduce-only market close,
  recorded as `TIME_EXIT` (a bracket that fills first still records as the bracket). Tests:
  `tests/live_exec/test_live_time_stop.py`. **For the prod run.** tnet keeps running `f08a017`
  until it is deliberately rolled; its first poll on the new image would close anything past 72 h.
- **Check a live bot by heartbeat `orders` > 0 and zero `reservation gate` refusals — not readyz.**
  On 2026-09-16 tnet was `(healthy)` with all 7 checks green, 41 signals approved and **zero orders
  ever placed**; the 6 WORKING ledger rows were ghosts.

## Deploy and infra rules

- **`deploy.yml` is gone.** It is now `deploy-paper.yml`, `deploy-testnet.yml`, `deploy-prod.yml`
  (dispatch-only), all calling `_roll.yml`, with separate concurrency groups.
- **Merging is free; deploying is not.** The deploy guard skips any stack with a RUNNING experiment,
  so merging `app/**` or `deltabt/**` does not end a bound run — verified repeatedly. Shipping an
  execution change INTO a bound bot needs `only_stack=X`, which **bypasses the guard, retires the
  run and resets the sample to zero**.
- **But any merge that changes `user_data` (run.sh, run_live.sh, the template) REPLACES the host
  immediately** — including paper hosts mid-experiment. `disableApiTermination` does **not** stop it
  (it destroyed tnet twice on 09-16); the `ec2.tf` comment claiming otherwise is stale.
- **`var.live_venue` is ONE GLOBAL, not per-stack.** Flipping it turns `tnet` into a prod bot.
  `_roll.yml` therefore reads the host's own `/deltabt/paper/<stack>/delta_env` before the guard and
  fails closed.
- **`deploy/aws/run.sh` is at its size ceiling** — gzipped into user_data, 16,384-byte cap with an
  85% budget enforced by `tests/live/test_user_data_size.py`; last measured 13,917 against 13,926.
  One extra `-e` line (~20 rendered bytes) fails the build. Put logic in Python, not the shell.
- **The infra tests grep text, not wiring.** They assert that strings appear in files, never that one
  resource references another. On 2026-09-15 the whole suite was green over a live stack that could
  not have started — six broken links, each shipped in #58, each passed review. Run `tofu plan`
  against real state instead (see below), read the additions by name, and for any new test delete
  the thing it pins and confirm it fails.
- **The retire step runs `app.cli stop` inside the RUNNING container with its start-time DSN**, so a
  rotated DB password fails the roll. Restart the service first — the same image rebinds the same
  experiment.
- **IAM database auth is OFF and must stay off until the DB is upsized.** `deltabt-paper` is
  db.t4g.micro (~120 MiB free); AWS needs 300-1000 MiB more for IAM auth. Enabling it swapped
  75 → 340 MiB and logins hung 60s then failed. Revisit only with t4g.small+ and after 2026-09-30.
  The RDS master password rotates on a 21-day CLI schedule (next ~2026-10-08), not in Terraform.
- **The one failure shape behind every 09-16 break:** two sides computing the same fact from two
  sources with nothing making them agree. Green health checks never assert the universe or the
  binding — check what a bot WARMED, SUBSCRIBED TO, and whether it logged `bound to experiment`.

## Working in this repo

```bash
pip install -e ".[dev]"                            # .venv here is Python 3.11
python -m deltabt.cli screen                       # which symbols are usable
python -m deltabt.cli backtest --mode corrected    # with the review fixes applied
pytest tests/                                      # 6 pre-existing failures in test_h1_momentum.py
python -m live.smoke_testnet BTCUSD                # ~15s; refuses prod by design
```

A real `tofu plan` from this container (`terraform` here IS OpenTofu, same version CI uses):

```bash
cd infra/terraform
export AWS_REGION=ap-south-1
eval "$(aws configure export-credentials --format env)"   # the Go SDK cannot read `aws login`'s cache
tofu init -input=false \
  -backend-config="bucket=deltabt-tfstate-132203050472-ap-south-1" \
  -backend-config="region=ap-south-1"
tofu plan -lock=false -var="github_org=yesterdaysrebel" -var="github_repo=deltabt" \
  -var="github_owner_id=256862558" -var="github_repo_id=1331985440" \
  -var="aws_region=ap-south-1" -var="alarm_email=paraniyai@gmail.com"
```

**Pass the real variable values.** Fake ones produce a misleading plan — an empty `alarm_email`
alone showed `2 to destroy`. Get them from `gh variable list` and `gh api repos/:owner/:repo`.

Probing a live box (the recipe that works): SSM send-command with `--cli-input-json` (the
`commands=` shorthand mangles newlines), running
`docker exec -i -e PYTHONPATH=/app deltabot python - <<'PY'`. stdin leaves no file behind;
container `/tmp` is not deletable by host root via `docker exec rm`, so a probe *script* leaks until
the next roll. For the DB use `asyncpg.connect(**connect_kwargs(os.environ["DATABASE_URL"]))` from
`app.persistence.db_auth`. The daily report does **not** cover `tnet`, and the local `deltabt/.env`
keys are 401 against testnet. Local `aws login` sessions expire mid-task; a crash-looping container
cannot be `exec`'d — use `docker run --entrypoint python`.

## Environment and recovery after a rebuild

- `/data/` (22 MB of candle Parquet + meta) is **gitignored** — it dies with the container and is
  re-fetched through the data client. So are `out/**/trades_*.csv`, plots, `.env`, `*.parquet`,
  `.venv/`, and all Terraform state/tfvars. `out/experiments.jsonl` and the result tables ARE
  committed; that registry is the negative result and must never be pruned.
- `.env` holds Delta keys and is gitignored. **Never upload credentials to a rented box** — see the
  workspace rule; the local keys are production-scoped and 401 on testnet anyway.
- Delta testnet is a separate signup (https://demo.delta.exchange); keys do not cross over from
  production, and a Trading key needs IP whitelisting before Delta will issue it.

## Open threads

1. `/api/positions` 500 on live (`LivePosition.last_price`) — small, still open since 09-19.
2. The **sizing mismatch**: bot sizes against internal equity 10,000, venue balance is ~$445.
   Must be settled before anything touches prod.
3. **No entry-deviation guard existed before #83 and no post-fill bracket verification exists at
   all** — a fill past its own stop gets NO bracket and Delta drops the legs silently. Required
   before prod.
4. `ladder` and `ltp` report on **2026-09-30**, for mechanism, not P&L.
5. **Live time stop added 2026-09-28 (`fix/live-time-stop`), not yet deployed.** Needed for prod;
   tnet's current sample (15 closed on `f08a017`) was taken without it — a live exit rule the paper
   baseline has and the live arm did not. See "Live and venue traps".
6. Whether stop-limit + fallback is worth carrying is decidable only with **tick data**; the entire
   overshoot is worth at most +0.0154R/trade, ~12% of the arm's edge.

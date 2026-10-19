# CONTEXT — where this stands and what to do next

Written 2026-09-21; current-state sections updated 2026-10-16 (record-only coins, switch-rule amendment). **Provenance:** reconstructed from the working tree, git history, and the
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

## CURRENT GOAL (2026-10-13, owner) — the owner's way, with discipline

The repo was closed and all AWS infrastructure destroyed on 2026-10-13, then **re-opened the same day**.
The owner: "I was looking for a way to make a bot which trades like I do, just add discipline which I
lack, we turned it into research project." So the goal is a working bot, not edge research. Build what
the owner asks; state the evidence once and do not gate on it.

How the owner trades and what was chosen (2026-10-13):
- picks coins from Delta's **top gainers** page → `live/universe.py`: at start-up the top 10 crypto
  perps by 24h last-price change, operational, 24h turnover ≥ $250k; coins still held are kept. The bot
  exits 30 s past each UTC midnight and systemd restarts it with a fresh list (`live/__main__.py`).
- **RECORD-ONLY coins (owner 2026-10-16, branch `feat/record-wider`, not yet deployed):** to grow the
  learning data ~3x the bot also WATCHES gainers ranked 11–30 (same rules) and a fixed watchlist
  (BTCUSD, ETHUSD, BEATUSD, AKEUSD; unlisted ones skipped with a log line), and only RECORDS their
  signals (`features.record_only` true, `rank` 11–30 or None, `skip` "record_only"). Trading is
  unchanged: top 10 + held coins only. Defaults in code (`live/universe.pick_wide`; optional env
  `DELTABOT_RECORD_TOP_N` / `DELTABOT_RECORD_EXTRA`, NOT forwarded by `run_live.sh`, which sits at its
  16 KiB cap); top_gainers mode only. Safety, each layer on its own: the risk engine refuses a record-only
  coin FIRST (`limit_name` "record_only", before the day roll or any gate); the bot sends it down a
  separate path (`TradingBot._process_record_only`: no clock, no broker, no risk-event row, no `_place`;
  only evaluations with a direction are stored, to spare the host's 10 GB volume);
  `_place` refuses any coin outside the trade universe BEFORE the reservation; the paper broker
  (`NotTradeable`), `DryRunBroker` and `LiveBroker` (`OpeningRefused`) refuse it too, and no broker gets
  a record-only product id. `tests/live/test_record_only.py` drives the bot on a synthetic market: with
  two record-only coins firing ~95 signals each, the trade coin's 101 decisions, orders, 4 positions,
  risk state, clock and risk events are identical to a bot that does not watch them (and a negative
  control — the same coin made tradeable — does change them). Record-only coins warm AFTER READY in the
  background (4 at a time, own REST client at 4 req/s; measured 8 coins in 6.8 s, so ~20 s for 24); a
  cold coin's candles are ignored; their gaps show as `record_only_gaps`, not in `/healthz` gaps.
- **at most 2 trades a day**, one position at a time (`max_trades_per_day` 2; `max_open_positions` must
  be 1 on the stack — the Terraform global is still 6, set it when the stack is rebuilt); a filled entry
  counts even if flattened as invalid.
- entry = `manual_scalp_both_t3_st` (variant `SPEC:manual_scalp_both_t3_st@5`): the owner's %R on 5m + 1m
  **plus Supertrend (10, 2) aligned on both** (owner, 2026-10-13), 4×ATR stop, **take profit 3R**,
  $250 at 2% risk ($5 per stop), position ≤ **5× equity** (owner 2026-10-13; was 3×), 4h per-coin pause after a loss, fully automatic.
- **watch-only first** for 1–2 days (dry_run "1", read-only key), then real orders with a Trading key.
- Runs unbound (no forward-test experiment): the list changes daily, so `forward-test start` is refused.
- Sizing entry `bot` in `live.tf` `live_sizing`; the stack itself is not yet in `var.stacks`.

Evidence, said once: a 2/day cap made hold-to-3R worse in backtest (+0.044R → −0.084R); top-gainer
selection was never tested. No-trade minutes (branch `fix/quiet-minutes`, 2026-10-13): live bars now carry
them as flat `volume=0` bars like Delta's archive, the warm-up and the backtests, so the %R window matches;
they are not gaps in /healthz (`quiet_minutes` counts them) and a primary bar holding one is untradable live,
as in the backtest. Rule in `TradingBot._repair_gaps`; Delta REST forward-fills only after a traded minute
inside the request window (measured), which is why the old one-minute repair found nothing.

Backtest of the bot as built (owner asked 2026-10-13; `scripts/top_gainers_backtest.py`, result in
`out/top_gainers/result.txt`): daily top-10 gainers, 2026-01-01..10-12, $250, 2%, 5x, 2/day, 4h pause,
flat no-trade minutes kept. Net R/trade (± 1 SE): 5m/1m +0.003 (±0.110, 264 trades; $250 → $439 by
02-28 → 50% drawdown latch, no entries after 06-29, ends $219); 15m/5m −0.037 (±0.103, 226, ends $205);
1h/15m −0.079 (±0.181, 55, ends $221); 4h/1h +0.081 (±0.205, 9 trades in 9 months). None is
distinguishable from zero; every pair was better Jan–May than May–Oct. Fees only ~0.02–0.03R (wide
stops). 10x = 5x exactly (the cap never bound). Survivorship: only coins listed today could be picked.

Signal-features lab (owner asked for a model that learns from every signal; `scripts/signal_features_lab.py`,
pre-registered 8598b2c/7ec97f7, results in `out/signal_lab/*.txt`): 5,273 takeable signals of the bot as built
(every coin, no daily cap), ~40 readings each + price paths. **All signals lose about −0.07R a trade after fees
(Jan–Jul −0.065 ± 0.028, Aug–Oct −0.084 ± 0.045, ~3 SE below zero)**; the account backtest's ~0R came from which
264 it happened to take. Model (gradient boosting) **FAIL** on the sealed Aug–Oct: AUC 0.521, top 30% −0.088R
vs −0.084R all. Of 9 Jan–Jul findings 4 "held" by rank correlation (stop width, gainer rank, day gain, funding
side) but none makes money: dev-chosen cut-offs on Aug–Oct −0.107 / −0.086 / −0.064, all three −0.165R; the
stop-width link is mostly tight-stop slippage (win rate 27% vs 26%). Chart patterns and k-means families did
not hold. Paths: 31% of losers were +1R first; 45% of winners dipped below −0.5R first. Per the
pre-registration: record every signal live (recorder) and re-test on more data; do not filter with it.

Signal recorder (owner 2026-10-13, branch `feat/signal-recorder`, built on `feat/top-gainers`): every signal
with a direction, taken or skipped, gets the lab's readings in `strategy_signals.features` (JSONB: `lab` = the
~40 readings from `deltabt/signal_features.py`, which the lab's `_coin` now calls — parity test against the
pre-refactor output; `live` = ticker + L2 book; `pre_24h`; `skip` = taken/daily_cap/open_position/…), written by
a background task after the decision (`app/learning/recorder.py`; never blocks, NaN on failure, off with
`DELTABOT_SIGNAL_FEATURES=0`). `python -m live label-signals` labels each signal as if taken with the lab's
engine on public 1m candles (`signal_outcomes`; reproduces the lab exactly on a synthetic market);
**Takeable (2026-10-13, branch `fix/takeable-signals`):** live, the %R edge re-fires ~2×/hour per coin (33
signals in the first 100 min, 1 taken, 32 `open_position`), while the lab's 5,273 rows are only TAKEABLE signals
(~2/day per coin). After labelling, `label-signals` also sets `signal_outcomes.takeable` per coin in time order:
takeable iff the engine accepted the entry (has an R; ATR stop floor applied from the recorded primary ATR), the 5m
bar is tradable on the REST candles, and no earlier takeable signal on the coin is still open or within the
engine's 10-bar cooldown after its exit bar (`detail.free_from`); a signal waits while an earlier one on its coin
is unlabelled. On the golden market, every edge labelled and marked gives exactly the lab's 50 trades
(`tests/live_exec/test_takeable.py`; dropping the cooldown or the tradable check breaks it).
`python -m live grade-model` grades the lab's fixed model weekly on the last full UTC week, on TAKEABLE live
signals only (`model_grades`;
history from `$DELTABT_SIGNAL_HISTORY` or `out/signal_lab/signals.parquet`, tolerated absent). The model never
filters trades (`tests/live_exec/test_model_never_trades.py`). Daily report: 3-line "Learning" section
("recorded N (takeable M; taken K)"; with record-only coins "recorded N (trade coins M, record-only K;
takeable T (R record-only); taken X)"), fed by `app/reports/learning_probe.py`, copied verbatim into
`deploy/aws/db_probe.py` (a test keeps the two identical). Not yet scheduled on a box.
Known gap: live readings use the bot's ~7-8 days of bars, the lab full history, so Wilder-smoothed readings on
240m bars (st_4h_agrees) can differ slightly.

**SECOND PAPER ACCOUNT `metals` (owner, 2026-10-19: "second account but we only trade metals there"):** stack
`metals` in `live.tf` (own host/EIP/db `deltabt_metals`/report), `SPEC:flip_st_short@5/1`, watch-only, $250 at 2%,
2/day, 5x; `DELTABOT_UNIVERSE=metals` → trade = XAUT/PAXG/SLVON/OIL (whichever are listed), record-only = the other
liquid non-crypto perps by turnover (`live/universe.pick_metals`). History in the image: the tradfi flip_st_short
dataset (1,008 signals; `DELTABT_SIGNAL_HISTORY_METALS`, picked by `_default_history` when universe=metals). Its
grades/switch count are its own (own database). After the host exists: copy the venue secret to
`deltabt-paper/live/metals/venue-credentials` (server-side), the OWNER adds the new EIP to the read-only key's
allowlist, then deploy-prod rolls both stacks (approval) — and START the service (see the gotcha above).

**US-TOKEN / METAL PERPS EXPLORED 2026-10-19 (owner asked):** Delta lists 41 non-crypto perps (tags tradfi/xStock/
metal), 26 with 24h turnover ≥ $250k; taker fee 0.01–0.02% (crypto 0.05%); IM 1–4%. Metals trade every minute
round the clock (XAUT 98–100% of minutes, PAXG 75–95%, oil ~50%); US stock tokens are THIN even in US hours (TSLA
46%, NVDA 40%, META 39%, SPY 21% of minutes; SOXLB/SNDKB 60–80%), so the no-dead-minute entry rule rarely lets a
5m signal through. Backtest on the 26 from listing (Feb–Jul) to 10-19 (`out/tradfi*/`, `out/signal_lab_tradfi_flip_short/`):
flip_st_short −0.081R/signal (1,008; metals −0.094, stock tokens −0.063; Jan–Jul +0.09, Aug–Oct −0.22), account
−$99; live-era rule −0.291R, −$100. Metal stops are 0.3–0.5% (4×ATR on low vol) so the 5x cap binds and noise stops
dominate. Not traded. Cheap option offered: record-only on the 4 metals + a few liquid stock tokens.

**LIVE SINCE 2026-10-19 14:31 UTC (8:01 PM IST): `SPEC:flip_st_short@5/1`, watch-only, on the replaced host
`i-0aac6d85d802033bf` (EIP 13.207.73.230 kept, pg volume re-attached, 11 old-rule trades and 1,402 old-rule signals
intact).** GOTCHA: a host replacement leaves `deltabt.service` ENABLED BUT NOT STARTED (user_data only enables it; the
deploy roll's SSM document is what starts it). After the replacement the bot was simply absent for 10 min until
`systemctl start deltabt.service` over SSM; a deploy-prod roll would have done the same. Model on the flip_st_short
history (sealed Aug–Oct): FAIL — AUC 0.53, top-30% +0.015R vs −0.093R all, random matches it 15% (bar 5%).
Switch-on count restarted; earliest eligibility ~mid/late Nov.
**RULE SWITCH 2026-10-19 (owner: "allow model to learn and record; deploy this change now"):** the live rule becomes
`SPEC:flip_st_short@5/1` (the owner's flip setup, shorts only; the 1m confirm MUST be stated — this family takes its
trend from the confirmation chart; hash `dbd723813926…`), still watch-only. The model history shipped in the image
is the flip_st_short dataset (`out/signal_lab_flip_short/signals.parquet`, 2,176 signals; `DELTABT_SIGNAL_HISTORY_FAMILY`
names it and `grade-model` ignores a file for another rule). Labelling/takeability/grading/switch-on count run per
rule (`strategy_config_hash`); the old rule's 1,800+ live signals and its week-12-Oct grade stay unmixed and unused;
the switch-on count restarts. The variant is baked into user_data, so this is a HOST REPLACEMENT: deploy the new
image first (deploy-prod), then replace the host (infrastructure dispatch with allow_replace), or the new host boots
the old image, fails closed on the unknown family and crash-loops until the image lands. EIP and pg volume survive.
Known data-dependent test: `tests/live/test_spec_arm.py::test_it_reproduces_the_backtest_signal_bar_for_bar` fails
on master too once BTC 1m data extends past 2026-10-13 (live refuses a decision bar holding a no-trade minute, the
backtest signal fires); it does not run in CI.

**ENTRY-RULE CHECK 2026-10-19 (owner spotted today's GTC short / ARC long as "wrong side").** Execution was
correct (Supertrend and %R recomputed from Delta's 5m candles match the bot's recorded indicators), but the coded
rule `variant_a` ("%R above −80 and rising", no ceiling) CHASES: it bought ARC at %R −8 (top of its 140-bar range)
and shorted GTC at −75. The owner's intended setup is buy-the-dip / sell-the-bounce: Supertrend aligned and %R
crossing back OUT of the band (`cross_levels`; catalog `dip_st`, and `dip_both_st` = plus 1m %R variant_a).
Backtested on the same 9-month top-gainer data (`out/top_gainers_dip*/result.txt`, signal-level
`out/signal_lab_dip/`): **dip_st −0.156R/signal (2,587) vs the live rule's −0.071R (5,273)**, worse in both halves
(−0.14 / −0.21) and on both sides (longs −0.22, shorts −0.12); account run −$125 and the 50% latch by March;
dip_both_st −0.385R/trade, latch by February. In both rules and both windows LONGS lose more than shorts (gainers
mean-revert). Owner's second reading (10-19): "flip of ST and WPR just crossing −80/−20" → `flip_st` (both events within 6 bars,
1m ST agrees; `event_window_bars` = 4th hash-omitted spec field; `flip_recent` / `cross_recent` modes, window 1 ==
`flip` / `cross_levels`) and `flip_st_tight` (3 bars). Signal-level (`out/signal_lab_flip/`): **−0.124R/signal
(3,015)**, −0.11 / −0.17 by half, longs −0.23 / shorts −0.07. Account: flip_st −$122 and the latch by May (161
trades, −0.19R); 15m/5m −0.33R; flip_st_tight −$106 (217 trades, −0.11R). Every owner entry tested loses faster
than the live rule; shorts lose far less than longs under all three. Shorts-only (owner "option 2"; `sides` = 5th hash-omitted spec field): flip_st_short −0.054R/signal (2,176; account
−$121, latch by May); the live rule's shorts −0.041R (account twin −$127, latch). HOURS AFTER THE RE-PICK (found
10-19, pooled then checked by half): the live rule's signals in UTC 00–06 average −0.13R in BOTH halves vs −0.007 /
−0.038R for the rest of the day; the first-come account takes ~40% of its slots in the first hour, and 10 of the
live bot's 11 trades were entered 5:30–7:10 AM IST. But the account replay with entries only 6h+ after the pick
(`manual_scalp_both_t3_st_late`, entry_hours_utc=(6,24)) is NOT better (−0.068R, −$81 vs 0.00R, −$31): account
replays of ~100–260 trades swing ±0.12R and cannot resolve 0.1R; the signal-level reads are the reliable ones. For
the flip shorts-only rule the hour effect reverses in Aug–Oct. Nothing tested is positive; the ~−0.04R group (live
shorts, flip shorts-only, live rule 6h+) is the least bad and indistinguishable. Recommendation given: keep the live
rule; test the hour effect on the live record (the recorder already stores every signal's hour) before deploying
a window. None of dip_st / flip_st / *_short / *_late is deployed.
DRY-RUN REPLAY (owner asked 10-19; `out/top_gainers/picks_dryrun.csv` = the bot's logged daily lists, TG_PICKS/TG_FROM):
the live rule replayed over 10-13 13:22 UTC → 10-19 gives 10 trades, 1 win, −0.745R, −$36.29 vs the dry run's actual 11,
1 win, −0.75R, −$35.85 — same quality, but only 3 of 11 trades coincide: when several coins fire on one bar the bot
takes its evaluation order and the engine the sorted name (10-13 13:20 UTC: bot STX, replay BTW), and the paths
diverge from there. flip_st_short on the same days: 6 trades, 3 at 3R (BTW, INJ, SIREN), +5.63R, +$27.92 — one
week, ±0.9R SE per trade, against −0.054R/signal over 9 months; not evidence, recorded as such. Deploying any rule change
replaces the host once (variant is baked into user_data; EIP and the Postgres volume survive).

**MODEL SWITCH-ON RULE fixed 2026-10-15 (owner approved, before any grade): `docs/model_switch_on_rule.md`.**
Grades now test LIVE signals only (history runs into the first graded week), store per-signal scores, and grade a
second target "reaches +1R" (owner asked; history: 49% reach +1R, a 1R exit ≈ −0.075R/trade, 1R model AUC 0.51).
Eligible after ≥4 weeks of ≥30 live signals, pooled top-30% beats random at p ≤ 0.05 and (3R) mean R > 0 / (1R) reach
share > 52%. The live-only order-book readings, and since 2026-10-15 ORDER FLOW (taker buy/sell USD, imbalance, trade counts over 1/5/15 min and the largest print, from the all_trades feed; `app/market_data/trade_flow.py`, stored as `features.flow`), are recorded but NOT model inputs (history lacks them); a live-only
model needs a few hundred takeable live signals first.
**Amendment 2026-10-16 (owner, before the first grade on 10-19):** with record-only coins recorded, each scored
row carries a trade-universe flag (5th element; older 4-element rows count as trade coins), and a target is
ELIGIBLE only if the rule passes on ALL live signals AND separately on the trade-universe subset (same test and
thresholds; a subset week qualifies with ≥30 subset signals). `switch_check` → `trade_universe`; tests in
`tests/live_exec/test_learning.py`.

**BOT LIVE, WATCH-ONLY, since 2026-10-13 13:22 UTC (6:52 PM IST).** #116 + #117 merged; infrastructure apply
green (16/16 preflight; `--expected-rds 0`); deploy-prod run 38400636237 approved by the owner in GitHub. First
picks: S, DUSK, STX, BTW, OPN, INIT, AKE, TIA, BTC, SKL; unbound (no experiment); re-pick 00:00:30 UTC. First
simulated trade 13:25 UTC: LONG STXUSD 0.41708, stop 0.40680, target 0.45120, 450 contracts (~$4.6 risk); AKE/BTW/TIA
signals refused (one position) and recorded with `features.skip = open_position`. Recorder rows carry `lab` (~40
readings), `live` (spread, depth, funding, OI, mark/last gap), `pre_24h` (288 bars), computed in 3–5 s. deploy-prod is
manual dispatch only; the prod-deploy approval is the owner's (Claude Code may not self-approve).

**AWS REBUILT 2026-10-13 (owner yes):** bootstrap applied (state bucket `deltabt-tfstate-132203050472-ap-south-1`,
OIDC provider, `deltabt-github-deploy` / `deltabt-github-plan`; bootstrap's LOCAL state copied to
`s3://<bucket>/bootstrap/terraform.tfstate`). Main stack applied: 56 resources, NO RDS (`create_rds` false),
stack `bot` = `i-02f745358a7c7e611` (t4g.small), EIP **13.207.73.230**, log group `/deltabt/paper/bot/bot`,
Postgres on the host's 10 GB volume. About $20/month (the `estimated_monthly_cost_usd` output is a stale
string). The host rests (exit 90) until an image tag and the venue secret
`deltabt-paper/live/bot/venue-credentials` (`{"api_key","api_secret"}`, READ-ONLY key on 13.207.73.230) exist.
Still to do: GitHub workflows/vars/`prod-deploy` env, label/grade scheduling via the monitor document,
ship `out/signal_lab/signals.parquet`, merge to master, first deploy (owner approves in GitHub).

Next: rebuild AWS (bootstrap + one `bot` stack, Postgres on host — RDS still referenced in `ec2.tf`,
`docs/db_on_host.md` phase B), re-enable the GitHub workflows/vars/`prod-deploy` environment, owner
creates a read-only Delta key for the box's EIP; after the watch-only read, a Trading key and dry_run "0";
first real order = a small resting probe placed and cancelled.

## (HISTORY — destroyed 2026-10-13) Deployed state — ONE prod DRY RUN (`dryrun`) since 2026-10-02 10:39 UTC; no order is ever sent

**2026-10-09, amendment 2 (owner): baseline exit (hold to 3R) + a 4h per-coin pause after a loss**
(`RiskConfig.pause_symbol_after_loss_seconds` = 14400, delivered via `live_sizing` → `run_live.sh` →
`DELTABOT_PAUSE_SYMBOL_AFTER_LOSS`). The research did not support the pause (`scripts/loss_pause_exact.py`,
research branch). New experiment `DRY-MANUAL_SCALP_BOTH_T3-5-20261009-ca7ddd5` registered 12:23:31 UTC on
`i-0d484d4c927b3a7f8` (same EIP), image `ca7ddd5` (#113), read due 2026-10-30; the trail experiment
stopped 12:22:24 UTC with 46 closed trades.

Last read 2026-10-03 ~06:30 UTC. First night: 4 closed (1 BEAT win, 3 AKEUSD losses of ~$11.9
each), equity $229.38, 9.3% below peak. **Amendment 2026-10-03 (owner):** AKEUSD floor 5% → 3%, the
simulated account runs the TRAIL exit (hold-to-3R and ladder stay as shadows and now keep running
after the trail exits), drawdown latch 20% → 50% for the dry run (the report states when 20% would
have fired). Host replaced; new experiment `DRY-MANUAL_SCALP_BOTH_T3_TRAIL-5-20261003-5683929` registered
11:55:35 UTC on `i-0b59a7984b76f2154` (same EIP), read due 2026-10-24. Replacing a host under a RUNNING
experiment needed #108 (deploy waits on the LATEST infra run; the retire step asks the database on a
replaced host).

| stack | venue | experiment | state |
|---|---|---|---|
| `dryrun` | Delta India **PROD**, read-only key | `DRY-MANUAL_SCALP_BOTH_T3-5-20261009-ca7ddd5` | RUNNING since 2026-10-09 12:23:31Z on `i-0d484d4c927b3a7f8` (EIP `15.207.211.127`), image `ca7ddd5`, IAM-token DB login, `deltabt_dryrun` |

**What it is.** The live code path (`live/dry_run.py`) on prod books with a read-only key. Every
approved entry runs the live checks (kill switch, entry deviation ≤ 0.25R, spread ≤ 0.5R, mark not
beyond the stop, leverage plan) and is journaled as `DRY_RUN_ORDER` or `DRY_RUN_REFUSED` in
`system_events`; the position itself is filled and exited by the paper fill model on prod ticks.
Entries are the baseline entry rule; since amendment 2 (2026-10-09) the account's exit is
**hold to 3R** (`manual_scalp_both_t3@5`, BEAT/AKE/BANK) and a coin is paused 4h after a loss on it. Every position is shadowed under
**baseline** (hold to 3R), **ladder** (rungs) and **trail** (0.5R behind the peak from +0.5R) —
one row per position per rule in `shadow_exits`, one stop rule in `deltabt/exits.py`; legs that
outlive the real position keep running in-process (lost on restart → "not recorded"). Sizing:
equity $250, 2% risk ($5/R), AKEUSD may take 1 contract if it risks ≤ 3%; gates 50% drawdown latch
(dry run only; the plan's 20% is reported as a date), 10% daily loss, 8 consecutive losses, 72 h
time stop.

**What it cannot say:** whether any exit has an edge (~0.1–0.17R standard error at 100 trades vs
0.02–0.06R differences), or how Delta fills, attaches brackets or runs its own trailing stop.

**Daily report** (`scripts/brief_report.py`, 7:00 AM IST / 01:30 UTC, email + the `monitor` run
page): by owner instruction (2026-10-02) ONLY two parts — the attention list (unhealthy, restarts,
log errors, latch, missing data, D7 self-check, entries the simulator did not open) and the trade
journal as the THREE-EXIT COMPARISON: open trades (current price, R, $), then every trade closed this
run (carried-over ones marked *) as three lines — hold to 3R (the real simulated trade), ladder,
trail — each with closed time, exit price, how, held, net R and $ (other exits priced at the real
trade's $/R), then per-exit totals. Live stacks run the probe in journal mode
(`DELTABT_PROBE_MODE=journal`, monitoring.tf): no /api/trades section, no `economics`/`approvals`,
and the oldest closed trades are dropped (and counted) if the output would pass SSM's 24,000 bytes.
The long `daily_report.py` remains for paper stacks.

**After the read (owner's plan, not decided):** real money, $250 on each of two Delta sub-accounts
(`baseline`, `ladder`), Trading keys IP-allowlisted, exit chosen from this record. Before any real
money: a testnet probe of Delta's trailing stop (units, sign, edit on an open bracket). The baseline
sub-account's read-only key is the one in `deltabt-paper/live/dryrun/venue-credentials`.

### How we got here (2026-10-01 → 10-02)

- 10-01 ~20:15Z the four earlier stacks (`atr`, `ladder`, `ltp` paper; `tnet` testnet) were stopped by
  instruction and destroyed by #90 (records in the databases; log groups gone). Their 09-30 read:
  no arm's P&L distinguishable from zero; LTP worse on paired fills (12 worse / 3 better); the ladder
  behind baseline by 0.12R/trade on shared signals, its dollar lead was turnover, closed by backtest
  (#89). Paired backtest over 423 trades (`scripts/paired_exits_lab.py`): baseline +0.037R/trade (DD
  42.7R), ladder −0.018 (25.1R), trail +0.006 (17.2R) — none distinguishable from zero.
- #87 RDS → `db.t4g.small` + IAM (applied early by the owner 10-02); #93 five live-path fixes (DELTA_ENV
  source, gate values, sizing plumbing, spread/mark book guard, …); #97 trail arm + shared stop rule;
  #98 per-stack secrets, `live_venue = "prod"`, IAM tokens in the live image (the `prod-deploy` GitHub
  environment, reviewer = owner, master only, was created by the owner); #99 shadow exits; #100 dry-run mode; #101 the `dryrun` stack and its pre-registration.
  #94/#95 merged into stack branches by mistake and were re-landed by #96.

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

**Symbol choice cannot rescue `manual_scalp_both_t3` (2026-10-05, `scripts/symbol_screen.py`).** The
dry run's own spec on 32 Delta India perps (the 27 most-traded with history, plus the cached five),
2025-12-20..2026-09-30, 4,950 paired trades: the entries earn **nothing before fees** (gross −0.009R
per trade) and lose **−0.083R / −0.085R / −0.082R** per trade after them under hold-to-3R / ladder /
trail (t clustered by close day −2.2 / −5.4 / −5.7; net positive on 8 / 5 / 5 of 32). **Backtest P&L
does not persist across halves** (rank correlation −0.12 and −0.08; first-half winners −0.077R in the
second half, first-half losers −0.078R), so picking symbols by past result picks noise. The cost law
holds (fees 0.11–0.13R on 1.2–1.4% major stops, 0.02R on thin wide-stop symbols). Only ETHUSD, XRPUSD
and SOLUSD are *measurable* on $250 (size cleanly, trade every minute, ≥ 15 trades / 21 days);
BEATUSD is idle 31% of minutes at $0.3M/day; AKEUSD is one $325 contract. More symbols = more trades
= more fees: on $250 the trail lost $57 (BEAT only), $164 (SOL+BEAT), $337 (ETH+SOL+XRP), $393
(+BEAT) over the nine months (`scripts/paired_exits_universe.py`).
**Re-selecting "the profitable coins" daily does not work either
(`scripts/daily_symbol_selection_lab.py`, walk-forward, 40 selector cells per exit, judged against
random picking).** Under the trail every recent-profit selector is negative and worse than trading
everything, and recent winners lost to recent LOSERS in 12 of 12 matched cells. The one selector that
helps is the cost law — the coins with the lowest fee cost per R — and it lifts the result to about
breakeven, not profit: by fee cost per trade, net R is +0.034/−0.008 (under 0.03R), −0.035/−0.051,
−0.103/−0.097, −0.176/−0.142 (over 0.10R) for hold-to-3R/trail. Those cheap coins are the thin ones,
where the simulated fill is least trustworthy.
**An ML entry filter on candle features finds nothing (`scripts/entry_filter_ml_lab.py`).** The
memecoin repo's method applied here — LightGBM on 29 features known before the entry bar, labels used
only once a trade has closed, threshold fixed from the previous month, walk-forward April..September,
5 seeds, judged on net R against random picking. Out-of-sample AUC 0.490 (months 0.42–0.52); the
model's picks are no better than random on hold-to-3R at 10/25/50% depth and worse than trading
everything; shuffled labels look the same (leak check passes). The one-line cost rule beats it at
every depth (lowest-fee 10%: +0.112R, 356 trades, not distinguishable from zero). Reproduced byte for
byte 2026-10-09; a planted weak signal (alone AUC 0.576) comes back at 0.534, so the null is not a
blind pipeline.
**Open interest, the signal bar and regime beyond 24h do not rescue it either (2026-10-09,
pre-registered in `docs/entry_filter_bundle_prereg.md` before the data was fetched,
`scripts/entry_filter_bundle_lab.py`).** Delta serves full perp OI history through the undocumented
`OI:<SYMBOL>` candle series (`scripts/fetch_oi_history.py`; 1m/5m/1h agree exactly on 3,744 checked
pairs). 25 new features on top of the 29, no fee inputs, 5-seed mean, random pickers matched by day:
VALID (planted 0.560) and FAIL on all three rules — AUC 0.516 (bar 0.530); 25% picks −0.053R /
−0.028R (p 0.66 / 0.062); below the cheap-fee rule on both exits. OI adds nothing (AUC 0.487 vs
candle-only 0.490). **Still untested, because no history exists:** trade flow by aggressor side
(public trades endpoint returns the last 50 only; the bot's `all_trades` feed carries the side but
`normalize_trade` drops it and nothing is saved), order book, liquidations. Those need a forward
recorder.
**A daily trade cap does not raise the win rate (2026-10-09).** Prod universe backtest, engine's own
`max_trades_per_day`: hold-to-3R +0.044R uncapped → −0.084R at 2/day; trail −0.028R → −0.048R; the
first 2 entries of a day did no better than 2 random ones. The dry run's own 46 closed trades agree
(win rate lower under the cap; p 0.24–0.42 vs random) — far too few to decide anything.
**Pausing after losses does not filter losing trades (2026-10-09, `scripts/loss_pause_lab.py` then
`scripts/loss_pause_exact.py`, both pre-registered).** Virtual mode after N losses (equity-curve filter)
does nothing; account-wide it is worse than nothing. On hold-to-3R the trade after a win on a coin does
beat the trade after a loss (prod universe 36.7% vs 24.9% wins; 32 coins 29.9% vs 25.4%), and a
filter on the trade list made a 4–12h per-coin pause look like +0.044R → +0.23R. **Re-run inside the
engine (`RiskGates.pause_seconds`, off by default) it is −0.015R**, beaten by 32% of 200 random
pauses: a skipped trade frees the coin and the next entry comes sooner than the filter assumed.
Random 8h pauses alone spread hold-to-3R from −0.151R to +0.049R — judge any gate against random
firing of the same gate, never one baseline run. A 4h pause on hold-to-3R, run post-hoc at the
owner's request: +0.022R vs +0.044R with no pause, halves +0.108 / −0.057, p 0.21 vs random — also fails. Per coin (same exact runs vs random pauses on that coin):
BEAT 4h +0.133R vs +0.037R (p 0.06) but 8h +0.015R (p 0.43); AKE −0.293R / −0.207R, as bad as random
pauses (AKE's result swings −0.3R from ANY pause — path dependence); BANK 29–41 trades. Six post-hoc looks,
none below 0.05, and the same coin flips between 4h and 8h: no per-coin rule either.

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

**The ladder's "turnover" edge is closed (2026-10-01, `scripts/ladder_turnover_lab.py`, branch
`research/ladder-portfolio`).** The paper ladder arm's +$266 came from entries created in freed slots,
and within those from one post-hoc row: entries within 6 h of a promoted-stop exit on the opposite
side (+0.25R, n=45 live). Pre-registered and run on the archive before 09-16 with four slots: that row is
**−0.074R on 312 trades (t −1.36)** with filler bars dropped and −0.029R on 345 with them kept;
all created entries −0.066R on 1,016 (t −2.23); ladder vs baseline paired on 217 shared entries
−0.133R/trade. `run_portfolio` now implements `ladder_rungs` (1m walk inside the 5m bar, exits before
rungs, promotion recorded on the trade); on the paper arm's own 127 entries it reproduces 74/76 exit
types and 70/76 promotion decisions, median R difference 0.000. Also fixed there: `exit_cost`
double-billed slippage on `ltp_close` stop fills.

## Measurement rules this repo learned the hard way

- **The archive's filler bars break live parity on thin symbols (found 2026-10-01).** Delta's REST
  history returns a flat zero-volume bar for every untraded minute — 49% of BEATUSD minutes, 79%
  BANKUSD, 80% WIFUSD, 1% AKEUSD — and the live `CandleBuilder` only has minutes that traded. Both
  sides share `resample_complete`, but it counts minutes, so the archive keeps every 5m bucket while
  the bot keeps 29% / 10% / 8% of them, and the %R(140) window spans a different stretch of time.
  Measured against the ladder bot's own `strategy_signals`: engine on the archive fires 2.4–3.7× the
  bot's signals on the thin three; with filler bars dropped the counts match and 93–97% of the bot's
  bars are hit (WIFUSD 69%, open). **Drop rows with volume 0 and o=h=l=c before resampling.** Every
  thin-symbol backtest in `out/` ran on the inflated bar set; exit studies survive (fills reproduce on
  identical entries), signal-timing and trade-count claims do not. And even with 95% signal
  agreement the realised trade LIST diverges after one differing bar, because the rule fires ~80×/day
  per symbol on one slot — reproduction standards here are signal-stream agreement and exits on
  forced identical entries, not "same trades".
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
- **`var.live_venue` is ONE GLOBAL, not per-stack** — `"prod"` since 2026-10-02. Every live stack is a
  prod stack; a testnet stack cannot coexist with it. `_roll.yml` reads the host's own
  `/deltabt/paper/<stack>/delta_env` before the guard and fails closed on a mismatch.
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
- **IAM database auth is live (2026-10-02).** RDS is `db.t4g.small` with IAM enabled (#87, applied
  early by the owner) and the live image sets `ENV DB_IAM_AUTH=1` (#98). The DB-first order was the
  rule: tokens before the instance accepts them is #84 again (every login refused); a micro cannot
  run IAM auth (~120 MiB free). Paper images still use the password, which rotates on a 21-day CLI
  schedule (next ~2026-10-08), not in Terraform.
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

1. **Dry run in progress** — read at the first of 100 closed positions, 2026-10-24, the latch, or a
   defect (`docs/prod_dry_run_prereg.md`). No interim read changes anything.
2. **Before any real money:** testnet probe of Delta's trailing stop on an open bracket; Trading keys
   for the `baseline`/`ladder` sub-accounts created only after their hosts exist (EIP allowlist);
   a real-money pre-registration frozen before the first order.
3. **Postgres on the bot host instead of RDS — built, NOT merged** (draft PR, branch `infra/db-on-host`,
   `docs/db_on_host.md`). RDS is ~80% of a ~$41/month bill; after the switch and phase B (RDS removed)
   about $8/month. Owner decides on merge after the dry run's read; merging replaces the host and
   starts a new experiment.
4. Housekeeping the owner may choose: delete the old global secret
   `deltabt-paper/live/venue-credentials`; delete the snapshot `deltabt-paper-pre-resize-20261002`.
5. The RDS master password rotates ~2026-10-08; the live image logs in with IAM tokens, and #102 moves
   the monitor probe to tokens too.
6. Whether stop-limit + fallback is worth carrying is decidable only with **tick data**; the entire
   overshoot is worth at most +0.0154R/trade.

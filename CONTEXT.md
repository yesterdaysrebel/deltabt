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

**(SCRAPPED 2026-10-28, see GAINER-FADE below) METALS ACCOUNT LIVE 2026-10-19 20:47 UTC (2:17 AM IST 10-20)** on `i-00a4fee08c1b6882a` (EIP 13.205.134.107,
owner whitelisted it on the read-only key): deploy run 39303652988 rolled both stacks (two prod-deploy approvals, one
per stack); the metals roll's LAST step "start the successor experiment" failed because `_roll.yml` only skipped
registration for `"universe":"top_gainers"` — fixed to cover every dynamic mode. The bot itself was started by the
deploy document and runs. The crypto bot rolled to image 070dbcd unchanged.
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
**QUICK TRADES ON THE 26 NON-CRYPTO PERPS — ALL FAIL (2026-11-16, owner "unpopular strategies with quick entry and
exit for futures, US stock perps").** Pre-registered `docs/tradfi_quick_prereg.md`; data `scripts/tradfi_quick_data.py`
(1m trade + `.DE<sym>` index candles from listing); test `scripts/tradfi_quick.py` -> `out/tradfi_quick/report.txt`.
Costs are a third of crypto's (taker 0.01-0.02%, live spreads 0.001-0.045%). TRAP: Delta's REST forward-fills no-trade
minutes as flat volume-0 candles -- 76-90% of stock-token minutes (real trades in 10-24% of minutes; PAXG/XAUT 95%+);
with them counted the perp-vs-index snap-back "passed" (+0.13%/trade strict, t 7.2) on stale prints; with volume > 0
only it is -0.12% strict / -0.04% lenient (gross ~0). Judged on 2026-08-15..11-15 with DEV-frozen cells: snap-back,
fade the off-hours move into the US open (DEV +1.3%, TEST -0.27%), follow it, PAXG/XAUT pair (gross ~+0.04%, costs
0.08%), last-30-min intraday momentum -- 0 of 5 pass, every TEST mean negative under both fills.

**VERDICT 2026-11-09 — THE FADE HAS NO PROVEN EDGE; DO NOT SCALE REAL MONEY ON IT.** Pre-registered Binance test
(`docs/fade_binance_prereg.md`, `scripts/fade_binance_test.py`, `out/gainer_fade/binance_judge.txt`; 2021-2024, 2,609
trades, 309 coins incl. delisted, before funding): rule A pooled −0.002R (z −0.20), QUICK +0.001R (z +0.08), each > 0
in only 2 of 4 years; the ladder kept over flat in 2/4 years; rolling-90d and win-rate step-ups 0/4 — all FAIL. On Delta
with funding + the bot's real entry (`scripts/fade_costed.py`): A 2025 −0.013R, 2026 +0.087R; QUICK 2025 +0.003R, 2026
+0.037R. Cost model verified on the real account's 4 trades (funding matches Delta to ~$0.0001; fee is 0.0531%/side incl.
GST vs 0.05% modelled). An independent review agent found no code bug (`scripts/fade_ladder_account.py` reproduced to the
dollar) but: Jan-Jul 2026 is the tuning period, and the ladder's lead over flat sizing is day-order luck (shuffle control).
Also tested and failed today: 1:1 target, 75%-deployed tight stop, 0.3x target, top 1/3/4 picks, fixed coin list, size
after a loss, monthly stops, 15% ladder, leverage > 1x. The Quick branch `feat/fade-live-quick` is NOT merged.
Harness checks: an independent review of `scripts/fade_binance_test.py` found no bug (picks and 7 trades rebuilt from raw
files; a generous daily-only rerun also gives ~0R); the positive control (`--control`, same code on Binance 2025-2026,
`out/gainer_fade/binance_control.txt`) reproduces Delta's direction (A +0.031R / +0.065R, QUICK +0.012R / +0.020R). Six
Binance years for rule A, before funding: −0.018, +0.036, −0.035, +0.009, +0.031, +0.065R — positive in the last two only.

**SKIPS (owner 2026-11-11 "how to address the skips?").** Run 3 skips a pick whose stop is > 32.5% away (every
backtest measured only those <= 32.5%; 1x liquidation alone would allow ~48%). Paper picks 10-29..11-11: 11 of 28
skipped, 0 of the first 10, 11 of the last 18; the 4 graded far-stop paper trades averaged −0.38R. **Option 2 —
replace a skipped pick with the next gainer (ranks 3-10) whose stop fits — FAILS** its pre-registration
(`docs/fade_next_pick_prereg.md`, `scripts/fade_next_pick.py`, `out/gainer_fade/next_pick.txt`): Binance 2021-24
substitutes 203 trades, −0.040R pooled (z −1.35), > 0 in 2/4 years; control 2025-26 −0.008R (300). Live rule
unchanged: skip, no substitute. On Binance 9% of top-2 picks were skipped in 2021-24, 23% in 2025-26.
**Paper NO_DATA bug (fixed in #153, deployed 2026-11-11 ~09:58 UTC):** Delta serves no candle in a window until its first
traded minute; `_monitor` fetched from `last_checked`, so a quiet 23:45-23:55 left the day-end close with no bar
and booked NO_DATA at the entry (INITUSD 11-05, CAPUSD 11-06, PIEVERSEUSD 11-10 — real made +0.64R there). Now
fetched from the entry. The three old rows (A, LADDER) are still wrong in the DB: re-open them after the deploy
so the fixed monitor re-walks them.

**LADDER vs FUNDING COLLECTION, same window 2025-02-13 .. 2026-10-27 (owner "compare them with ladder").**
`scripts/compare_ladder_funding.py`, `out/compare_ladder_funding.txt`, each from $1,500. Rule A ladder (as the real
account runs): $5,315 (+254%) but 2025 −38% ($1,500 → $928) then 2026 +472%; worst drop 67%; win 57% of 983 trades;
worst month −21%, best +120%. Ladder + skip negative funding: $2,899 (+93%), 2025 +66%, 2026 +17%, drop 42%.
Funding collection (timing-fixed 2026-11-11, see below): $4,094 (+173%), 2025 +32%, 2026 +107%, drop 25%, 13 of 20
months green, worst month −14%. Monthly correlation ladder vs funding +0.25; both red in 2 of 20 months; half in
each: $5,740, 14/20 green, worst drop 22% (month-end).

**2/3-AT-+0.2R LADDER WITH MORE RISK AND LEVERAGE (2026-11-13, owner "raise risk and leverage, more money per trade?").**
`fade_profit_ladder.py --risk23`, `out/fade_risk23.txt`. Fixed risk, 3x / 5x caps, 50% margin a day: 5% avg +$0.90 a
trade, $2,562, drop 54%; 10% +$0.76, $2,419, drop 82%; 15% −$0.35..−0.44, ~$1,200; 20% −$1.0, $446-602. 5x changes
nothing vs 3x (the notional rarely needs more). Dollars per trade peak at ~5% risk.

**OWNER'S SCALP STYLE, IDEAS FROM OTHER MODELS (2026-11-13, owner "find ways to make what I am asking work, use diffrent
models").** Prompt `out/review_pack/2026-11-13-scalp/PROMPT_SCALP.md` (self-contained, for pasting into external
models; OpenRouter key now returns 401). Fable, Sonnet, Haiku answered (`reviews/`): all three say tight stop + quick
target + high leverage cannot work here (bracket wins at ~S/(S+T), costs 0.3% round trip ~ the whole drift). Their 21
testable rules in `scripts/scalp_ideas.py` -> `out/scalp_ideas.txt` (Binance 2021-26 rule A picks, realistic fills,
bar: TEST 2024-26 each year > 0, t >= 2, beats baseline): **0 pass.** Best: BTC prev day down (+0.040R TEST, t 1.46,
2024 -0.009); funding gate (+0.032, t 1.23). Clock exits 04/08/12 h with tighter stops, 48/72 h holds, 12 h profit
lock, noon entry, long-the-pump-then-short, premium/taker/spot/L-S/listing-age filters all fail. Untested: top-5
breadth (data has ranks 1-2 only), spot-hedged premium trade.
Round 2 (`--round2` -> `out/scalp_ideas_round2.txt`): owner pasted Gemini (6) and DeepSeek (6) answers; proposers'
own thresholds, same bar: **0 pass.** Closest: DeepSeek A' (funding >= 0.03%, prev-day sum > 0, OI +5%; 1.75 ATR stop,
0.75x target, out 08:03): TEST +0.081R t 1.84 but 2024 -0.041, n 41/yr -- a funding-family lead like the earlier
funding filter, not adopted. Gemini's top picks (08:03 fade +0.031R t 0.57; liquidity sweep +0.039R t 0.81) fail; long
the opening wave -0.159R t -4.7. Several rules almost never fire on these picks (G3 n 1, DeepSeek A n 0, C n 5-11).
Round 3 (`--round3` -> `out/scalp_ideas_round3.txt`): a fifth pasted answer (5 rules): **0 pass.** 06:00 "proof of
profit" cut +0.014R TEST (rule A +0.019); funding+OI-falling filter n 13/yr; young-listing short -0.037R t -3.5; long
00:03-04:00 -0.087R t -4.3. Total across 6 models: 47 rules, 0 pass, none with TEST t >= 2.
Round 4 (`--round4` -> `out/scalp_ideas_round4.txt`): a sixth pasted answer: **0 pass.** Maker entry (limit +0.2% above,
30 min): +0.039R better than a market entry on the SAME filled picks, but the 30% never filled are the best shorts
(+0.10R TEST), so per pick it nets about the same as rule A. Hold winners to 48 h: no gain. **Crowded-long family is the
one recurring lead:** Binance prev-day funding sum >= 0.05/0.10/0.20% -> TEST +0.043/+0.056/+0.095R but 2024
negative at every threshold; premium >= 0.2% TEST +0.157R t 1.75, n 35/yr, 2024 -0.103. On DELTA's own 1,022 picks
(real fills) funding sum >= 0.05% -> +0.061R t 1.45 (2025 +0.068, 2026 +0.055; n 324), >= 0.10% -> +0.084R t 1.49
(both years positive) vs all picks +0.021R. t < 2 everywhere: a lead, not an edge. (The earlier >= 0 filter was the
one that swapped years.)
**THE STOP-LOSS PROBLEM, DIAGNOSED (2026-11-15, owner: "find a way to solve the stop-loss problem"; all on the
careful dataset, every family double-implemented, train 2025 / test 2026+real):**
- REVIEW 2026-11-16 (owner "run a review on all the studies"): 16 studies of 11-15/16 re-run -> byte-identical to the
  saved outputs (`out/review/rerun.sh`). FEES WERE OVER-CHARGED in every careful_* script: 0.12% round trip vs Delta's
  real fills (GST in) maker 0.0212% / taker 0.0531% = 0.042% (resting target exit) / 0.074% (stop, time). New
  independent grid at the real fees (`scripts/review_real_fees.py`, validated on 1,524 trades against
  careful_one_pct.p1; `out/review/real_fees.txt`), % of the position per trade, top pick, 381 trades: every target
  0.5-1.5% x stop 1.5-10% stays negative under STRICT, LENIENT and even PERFECT fills (1% / 2.25%: -0.46 / -0.16 /
  -0.05%); the verdict does not flip. Positive in both years only with a 5-10% stop and a >= 5% or no target (no
  target, 10% stop: +0.37% strict .. +0.70% lenient before funding, t 0.6-1.1) = the wide-stop lead below, unproven.
  The dataset's flat minutes (46%) are Delta's forward-filled no-trade minutes; they cannot trigger anything. Picks
  spot-checked against Delta daily candles (14 of 14 rose on D-1, mean +14.2%).
- OWNER'S HAND-TRADE RECORD, FULL (2026-11-16, owner "the venue is fine, you can't fine-tune the setup" -> uploaded Delta's
  fill history, order history, annual summary to `out/manual/`, gitignored, repo is PUBLIC: never commit the per-trade
  rows). `scripts/hand_trades_review.py trips|candles|report` -> `out/manual/report.txt`. 8,266 perp fills 2026-05-18 ..
  11-07 = **766 round trips, 77 days, 66 coins: gross -$17,536, trading fees -$4,299, liquidation charges -$8,007, net
  -$29,843** (= Delta's annual summary -25.3 lakh INR at 85 INR/USD, to the rupee; Delta's order-history Realised P&L
  matches per coin to the cent). Liquidation charge = Delta's summary "Fee" minus the fills' "Fees paid", only on coins
  with liquidations, ~0.47% of the liquidated notional -- NOT in the fill export. 302 wins +$41,979 (avg +$139, median
  move +1.0%), 263 clean losses -$18,880, **201 liquidations (26%) -$52,942** at a median 2.79% against (~30x). Median
  size $6.9k, hold 8.6 min. Price move per trip BEFORE fees: -0.33% of the position (t -3.0): the entries have no edge.
  No pre-trade feature separates winners from liquidations: trade number of the day (1st -$44/trade, 2-3 +$5, 21+
  -$128, 45% liquidated), previous result, gap, long/short, hour, size, maker/taker, coin -- every bucket with 60+ trips
  is negative or ~0. Liquidated trades had chased a +2.1% move in the last 15-60 min (wins +1.0%); entries with a 3%+
  1 h move behind them: 257 trips, 35% liquidated, -$14.6k (vs faded: -$2.0k; difference t ~1.5, not reliable). Rules
  that drop trades: max 3 a day -$2,855, stop after the day's first liquidation -$4,738 -- still negative. WHAT-IF on
  761 trips with Delta 1m candles (as traded -$28,807; two independent implementations agree to the cent), STRICT ..
  LENIENT: stop 0.5% -$30.7k .. -$8.2k, 1% -$32.4k .. -$12.0k, 2% -$29.7k .. -$15.0k; liquidations HELD at lower
  leverage (same size) with a stop at 5% -$21.0k .. -$14.7k, 10% -$22.0k .. -$18.9k, 20% -$17.3k .. -$16.2k (86% come
  back to entry within 72 h, the other 14% lose 20%). 39% of the wins went 1%+ against first. **No stop width and no
  leverage turns the owner's own record positive.** "Good money per trade" is true of the wins (top 10 = +$11.2k,
  median move +10%) and of 24 of 77 days; it is the liquidation tail that the record never pays for. Supersedes the
  15 Jul-8 Sep cut (369 trips, commit 2760df4). INDICATORS AT THE REAL ENTRIES (owner 11-16: "I work with wpr 140,
  super trend 10,2"; `deltabt.indicators` on Delta 1m candles, last closed bar, 752 entries; inline run, per-entry
  values in `out/manual/indicators_at_entry.pkl`): trade side = 1m Supertrend on 80% of entries (chance 50%), = 5m
  Supertrend on 54% (chance); longs sit at 1m %R above -20 on 43% (random minute 25%), shorts spread out. The owner
  trades WITH the 1m Supertrend, buying near the top of the 140-bar range. It does not pay: both STs agree 363 trips
  avg -$46 (move -0.47%/trip), both AGAINST 104 trips avg -$9 (+0.30%/trip, difference t ~2.3, one of 4 groups);
  every %R zone on 1m and 5m is negative (-$26..-$52/trip); the coded readings on the owner's own entries (dip band,
  band cross, ST flip within 6 bars) avg -$62..-$68 vs -$30 for the rest. Same verdict as the 9-month backtests.
  THE RULE TAKEN MECHANICALLY ON THE OWNER'S OWN COIN-DAYS (owner: "1m mostly, 5m for confirmation sometimes";
  `scripts/hand_rule_test.py` -> `out/manual/rule_test.txt`; 66 coins, 538,590 1m bars = 374 coin-days around the
  hand trades; second implementation agrees to 4 decimals): 1m triggers flip / %R breakout through -20 with ST /
  %R dip back through -80 with ST, each with and without 5m ST; exits +1% target with a 2.8% or 1% stop (60 min),
  or ride the 1m ST. **All 18 cells negative at both bounds**: STRICT -0.18..-0.40% of the position per trade
  (t -1.4..-13), LENIENT (maker entry at the open, no slippage) -0.008..-0.18%. Target +1% / stop 2.8% WINS 57-65% of
  the time and still loses -0.07..-0.12% lenient -- "mostly targets are hit" is true and does not pay.
- %R(140) BAND BEHAVIOUR, DESCRIPTIVE (owner 11-16: "1m exhausted, 5m just started -> the 5m always tries to reach the
  other band ... just study how the wpr band behaves"; `scripts/wpr_band_behaviour.py` -> `out/wpr_band_behaviour.txt`,
  `scripts/wpr_5m_carry.py` -> `out/wpr_5m_carry.txt`; data A = owner's coin-days, B = careful dataset top gainers
  2025-01..2026-11; key numbers re-derived by a second implementation). Same on 1m and 5m, same on A and B: %R is in a
  band ~40% of the time, a stay lasts a median 3 bars; **after leaving a band it falls back into the same band 83-86%
  of the time (a third on the next bar, ~55% within 3) and crosses to the other band 14-17%**, taking a median 31
  bars; past -50 it finishes 62-65%, past -30 84-88%. Chance of the far band first ~ distance (15-18% just outside
  the band, ~45% at the middle, 81-85% near the far band). Within 140 bars the 5m does reach the other band 52-57% of
  the time, mostly after going back first. THE OWNER'S CONDITION IS REAL BUT SMALL: 5m just outside its band with the
  1m already at the far band -> crosses 20-23% vs 15-16% (1m in the middle) vs 13% (1m not started); the same ~+7
  points at every 5m level. PRICE: a 5m crossing = median +3.6..+4.9%, a fall-back = -0.7..-0.8%; mean over all trips
  -0.16..-0.17% before fees. Traded (in at the 5m leave, out at the other band, stop at the range edge or 2.8%):
  exhausted-1m subset -0.62..-1.15% strict, -0.24..-0.44% lenient on both datasets.
- SUPERTREND(10,2) WITH THE %R(140) BAND, DESCRIPTIVE (owner 11-16; `scripts/st_band_behaviour.py` ->
  `out/st_band_behaviour.txt`; same data A / B, no costs; second implementation reproduces legs and trip splits
  exactly). TRAP: on no-trade minutes (Delta's flat bars) Supertrend's ATR collapses and it flips every bar (B
  unfiltered: median 1m leg 1 bar), so stretches with > 20% flat 1m bars are dropped (A keeps 54% of bars, B 26%).
  A LEG (flip to flip): median 12 / 8 bars on 1m, 15 on 5m; **33-36% of legs end in profit**, median -0.19% (1m),
  -0.64..-0.97% (5m), mean -0.05..+0.02% (B 5m -0.15%), best tenth +1.2..1.5% (1m), +4.4..4.6% (5m). %R at the flip:
  median -50; the %R level at the flip does NOT change the leg (30-35% profitable from every zone). A %R TRIP: with
  Supertrend on its side when %R leaves the band 15-16% cross vs 11-12% against; no trip ever crosses without
  Supertrend coming onto its side (it does within ~2 bars on 34-39% of the against-trips); fresh flip (<= 3 bars)
  16-19% vs older 10-16%. LEVEL x SIDE: Supertrend matters most mid-range (5m at -60..-40: 51-62% with vs 38-49%
  against), 2-3 points near either band. TWO TIMEFRAMES: 1m trip with both STs with it 22-28% cross (1.5% of trips;
  price mean still ~0); 5m trip with 5m ST with but 1m ST against 5-8% (worst cell); 5m ST almost never flips against
  the 1m ST. Price over a trip is ~0 or negative on average in every Supertrend cell. PRICE FROM EVERY STATE (owner
  "do you see a setup forming?"; inline, liquid stretches): mean price move from a bar to %R's next band close, by
  %R level x Supertrend side x other-timeframe Supertrend, is noise around zero (+-0.05% on 1m, +-0.4% on 5m, no
  neighbouring cells agree, A and B disagree in sign on most) -- the odds improve exactly as the remaining payoff
  shrinks. ONLY CANDIDATE: 1m %R low in its range (-80..-60, own terms) with BOTH the 1m and 5m Supertrend on side
  (a 1m pullback in a 5m trend): A +0.40% / +0.07% (t < 1), B +0.00% / +0.22% (t 1.4 / 3.1), ~1.5-4% of bars; its
  traded cousin (`hand_rule_test.py` dip+5m) was -0.18..-0.39% strict, -0.01..-0.18% lenient on A. Not a setup; a
  pre-registered test on untouched data is the only honest next step.
- **1m PULLBACK INSIDE A 5m TREND -- PRE-REGISTERED, FAIL (2026-11-16, owner "approved").** `docs/pullback_5m_trend_prereg.md`
  (written before the download), `scripts/pullback_data.py` (30 most-traded Delta crypto perps, 1m, 2026-08-01..11-15,
  `out/pullback/`), `scripts/pullback_5m_trend.py` -> `out/pullback/result.txt`; second implementation identical
  (2,977 / 3,020 trades, same means). Entry = onset of [1m ST and 5m ST on side, 1m %R(140) in -80..-60 own terms],
  exit = %R's next band close. STRICT -0.278% of the position per trade (t -10.2), LENIENT -0.149% (t -5.9), gross
  before any fee -0.066%; both halves, longs and shorts, and all 13 coins with 30+ trades negative; %R reached the
  band ahead on 35% of trades. Dead, nothing tuned. NOTE: 2,335 of 3,066 coin-days in Delta's top 30 by turnover
  have > 20% no-trade 1m bars -- outside BTC/ETH/SOL/XRP and a few hot coins a 1m Supertrend on Delta is mostly
  reading flat bars.
- **FADE THE %R BAND EXIT -- PRE-REGISTERED, FAIL (2026-11-16, owner "with wpr what setup can you find").**
  `docs/wpr_fade_leave_prereg.md`, `scripts/wpr_fade_leave.py` -> `out/pullback/fade_leave_result.txt` (second rule on
  out/pullback; second implementation identical). The only regularity in the %R studies -- it falls back into the
  band it left 86% of the time, +0.16% gross on 5m in data A/B -- traded on liquid untouched data: 5m STRICT -0.192%
  per trade (t -5.8, 7,819 trades), LENIENT -0.053% (t -1.7) = gross +0.03%, i.e. the A/B figure was mostly
  stale-bar / bid-ask bounce. 86% of trades do go back (+0.33..+0.47%), the 14% that cross lose 3.3% on average
  (worst trade -45%, worst day -119% of a position). 1m: -0.21% strict, -0.07% lenient. With this, %R(140) has been
  tested WITH the move and AGAINST it on 1m / 5m (and with the move on 15m / 1h): no reading has a gross edge.
  ZERO FEES, GENEROUS FILLS (owner 11-16: "they do have winners, don't worry about fees from now on"; inline re-runs
  with TAKER = MAKER = 0): the 18 trigger x exit cells on the owner's coin-days -0.12..+0.07% per trade (best:
  %R breakout + ride the 1m Supertrend +0.049%, t 1.75; win 34%, avg win +1.67%, avg loss -0.79%); the same 18 on
  the untouched liquid data -0.05..+0.01% (that best cell: +0.003%); pullback -0.066%; fade +0.03% (5m) / +0.017%
  (1m); 5m carry -0.05..+0.03%, with the 1m exhausted -0.32..-0.37%. Winners are real (33-66% of trades, avg win
  0.6-6%) and are cancelled by the losers BEFORE any fee: the zero is in the price path, not in the costs.
- **"AVERAGE LOSS LESS THAN WIN" SEARCH -> ONE LEAD, CONFIRMATION RUN PRE-REGISTERED (2026-11-16).**
  `scripts/win_gt_loss.py` -> `out/win_gt_loss.txt`: 6 entries (flip / %R breakout / %R dip, +-5m ST) x stop 0.5/1/2% x
  way out (ride 1m ST, ride 5m ST, 3R target), ZERO fees, three fill models. Every cell has avg win 2-8x avg loss
  with a 14-40% win rate; GENEROUS (stop at its price, no gap) is positive almost everywhere = the no-loss-tail trap;
  STRICT is negative everywhere (-0.04..-0.54%); EXACT (stop at its price, or the open when the bar gaps through)
  leaves ONE family positive on both data sets: **%R breakout (1m ST up, %R closes above -20; mirrored) + stop 2% /
  target 6%: A +0.276% (t 4.0), U +0.187% (t 2.8), win 30-31%, avg win ~+5%, avg loss ~-2%, median hold 27-51 min**
  (1%/3%: +0.05 / +0.08%). CONTROLS (inline, own simulator reproduces the cells): on A random entries with the same
  bracket make +0.18% (owner-chosen trending coin-days: A is no evidence); on U random longs +0.19%, random shorts
  -0.10% (rising market), signal longs +0.35%, shorts +0.02% -> excess over random ~+0.15%, t ~2. 15-minute version
  (scalper window): +0.01..+0.03% on U. SCALPER OFFER, from the owner's fills: exit fee zero on 99% of trades
  closed within 15 min, ENTRY fee still charged (0.036% avg); liquidations pay in full. Confirmation:
  `docs/breakout_3r_prereg.md`, `scripts/breakout_oos_data.py` (same 30 coins, 2026-03-01..07-31, `out/breakout_oos/`),
  `scripts/breakout_3r.py` -> `out/breakout_oos/result.txt`. RESULT: **FAIL** (second implementation identical). 2026-03..07, 615 liquid coin-days,
  2%/6% EXACT +0.046% per trade before fees (t 0.57, 1,198 trades, win 38%, avg win +3.15%, avg loss -1.82%), first
  half +0.20%, second half -0.07%, MID -0.011%, STRICT -0.22%, with real fees -0.06%; signal longs -0.015% vs random
  longs -0.016%, signal shorts +0.109% vs random +0.033% (no real excess). 1%/3%: -0.019%. The Aug-Nov result was the
  rising market; the entry adds nothing over a random bar with the same bracket.
- SPIKE SELL, WIN RATE x WIN SIZE BEFORE FEES (owner 11-16: "find something with better avg win and bigger win rate";
  inline, `review_real_fees.trade` with zero fees, both picks, 617 trades 2025-01..2026-11, STRICT / LENIENT):
  no target / stop 10% / 24 h: win 52%, avg win +10.1%, avg loss -9.1% / -8.6%, mean +0.86% / +1.11% (t 1.8 / 2.4),
  2025 +0.76%, 2026 +0.96% -- the only cell with win > 50%, avg win > avg loss and both years positive. Target 8% /
  stop 10%: win 61%, +7.05% vs -9.2%, mean +0.72% / +0.93% (t 2.1 / 2.9). Target 10% / stop 10%: 58%, +7.97% vs
  -9.15%, +0.79% / +0.99%. Target 5% / stop 10%: 69%, +4.8% vs -9.4%, +0.35% / +0.53%. Stops of 3-5% and targets of
  1-2% are negative or ~0 as before. Consistent with `wide_account.txt` (+0.055..+0.080R at R = 10%). The 8% / 10%
  targets and 15-20% stops are NEW cells on a data set already searched: in-sample, t ~2.
- SPIKE SELL WIDE STOP: THE FLIP BACK (owner 11-16: "I don't like 24 h hold, it generally flips back and we don't
  have a mechanism to cut the trade"; `scripts/spike_flip_back.py` -> `out/careful/spike_flip_back.txt`; plain case
  asserted equal to `review_real_fees.trade`; second implementation equal trade by trade). Before fees, stop 10%,
  617 trades, STRICT / LENIENT. A SHORTER CLOCK COSTS NOTHING: 8 h win 56%, +7.36% vs -7.47%, mean +0.82% / +1.01%
  (t 2.1 / 2.8; 2025 +0.58%, 2026 +1.09%); 12 h +0.91% / +1.13%; 24 h +0.86% / +1.11%; 4 h +0.51% / +0.68%; 2 h
  +0.19%; 1 h +0.04%. CUT RULES (break-even after 2-5%, lock half after 3-8%, trail 3-5% after 2-8%): under STRICT
  every one is WORSE than the plain clock (early ones ~0: BE 2% +0.05%, trail 2%/3% -0.16%; latest ones +0.63..0.66%);
  under LENIENT the late ones are level or slightly better (lock 8% +1.20%, trail 8%/5% +1.18%). They raise the win
  rate (61-77%) by shrinking the win. Same lesson as the 2.25%-stop exits: a moved stop's result is its fill. The
  live code's exit C (break-even at ~0.6%, then 5% trail) is the early kind.
- QUICK EXITS ON THE OWNER'S OWN 761 ENTRIES (owner 11-16: "my trades that you analysed, you should find something
  there with quick entry and exit"; inline on `out/manual/`, real fill price and time, Delta 1m candles, before fees;
  two codings of the bracket agree). PRICE GOES AGAINST HIM AFTER THE ENTRY: -0.05% by the end of the entry minute,
  -0.16% at 5 min, -0.27% at 10 min (t -2.9), -0.62% at 60 min. 60 brackets (target 0.3-2% x stop 0.5-2.8% x 5/10/15
  min): all negative at EXACT fills (-0.07..-0.22%). WHERE IT SITS: his CHASES (entered with a 15-min move of 1%+):
  395 trades, gross -$15,164, -0.41% at 10 min, negative in both halves; trades 6+ of the day: 466 trades, gross
  -$16,197, -0.43% at 10 min in BOTH halves (t -3.6); first 5 of the day: gross -$489, ~0. ONLY POSITIVE SUBSET: his
  FADES (entered against a 15-min move of 1%+): 93 trades / 35 days, +0.5% at 5-10 min, as traded gross +$729; with
  target 1.5% / stop 1% / out in 15 min: win 52%, avg win +1.39%, avg loss -1.03%, mean +0.22% (t 2.05, halves +0.32
  / +0.12%) -- one of ~9 subsets looked at, small. MECHANICAL VERSION FAILS (`scripts/fade_15m_move.py` ->
  `out/fade_15m_move.txt`; second implementation agrees): fade ANY 15-min move of 1%+ and leave after 5-60 min, on
  his coin-days and on U and O: 1-2% moves within +-0.04%, bigger moves inconsistent in sign (5%+: A -0.29%, U
  +0.29%, O +0.04% at 10 min). So his fades' result is his selection (or luck), not the 15-min move itself.
- **THE OWNER'S FADE MADE MECHANICAL ("fade a sharp move once it has turned") -- PRE-REGISTERED, FAIL (2026-11-16,
  owner "yes test it").** Profile of his 93 fades vs every mechanical fade chance on his coin-days (medians): move
  faded 2.5% vs 1.4%, 15-min range 6.7% vs 2.5%, entry 3 min after the extreme vs 1, 41% of the way back vs 16%,
  last minute already his way (+0.30% vs -0.25%); inside his fades "last minute his way" +1.0% at 10 min (58
  trades) vs -0.16% (35). Rule frozen in `docs/turn_fade_prereg.md` (15-min move 2%+, extreme 2-7 bars ago, 25-66%
  retraced, last bar closing the fade's way; target 1.5% / stop 1% / out in 15 min), `scripts/turn_fade.py` ->
  `out/turn_fade.txt`, signal logic unit-checked on synthetic prices, second implementation identical. U + O liquid
  days, before fees: 2,418 trades, win 41%, avg win +1.31%, avg loss -0.97%, mean -0.043% (t -1.7); U -0.037%, O
  -0.065%; MID -0.089%, STRICT -0.39%; random entries with the same bracket +0.009%, plain fade +0.014%. Looser
  rule -0.009%; all days -0.02%; and on HIS OWN coin-days -0.071% (t -2.9). Waiting for the turn is worse than not
  waiting. His 93 fades cannot be reproduced from the price bars: selection the bars do not show, or luck.
- **TWO FORWARD TRIALS STARTED 2026-11-16 (owner "Try it" on: spike fade on paper + record what the bars do not show
  for his hand trades). Nothing deployed, no orders, real account untouched.**
  (1) SPIKE FADE 10% STOP / 8 h CLOCK, PAPER, FORWARD: rule frozen in `docs/spike8h_forward_prereg.md`; picks = the
  paper runner's own rule A picks from 2026-11-15 (prod `fade_positions`, SELECT only); `python
  scripts/spike8h_forward.py pull` (host, aws ssm) then `... run` (container) appends finished days to
  `out/careful/spike8h_forward.csv`. Verdict at 60 and 120 filled trades (mean > 0 strict after real fees AND win >=
  50%); earlier = progress note. First rows: 2026-11-15 EVAAUSD stopped after 75 min (-12.0% strict / -10.1%
  lenient), SOPHUSD stopped after 9 min (-10.1%). NOT scheduled: someone has to run the two commands.
  (2) TAPE + ORDER-BOOK RECORDER for the owner's hand trades: `scripts/tape_recorder.py` (public data only: websocket
  all_trades with aggressor side; REST l2orderbook depth 20 every 10 s; 12 biggest 24 h movers + 8 most traded
  crypto perps, re-chosen every 15 min) -> `out/manual/tape/{trades,book}_YYYYMMDD.csv`, `symbols.log`. Started
  13:17 UTC inside the dev container `deltabt-py` (`docker exec -d`): it DIES when the dev container stops -- a
  permanent home (the bot host) is the owner's deploy decision. OWNER, SAME DAY: "I am not going to hand trade,
  that's why we are building the bot" -> the hand-trade plan is DROPPED. The recorder is kept for the only thing it
  is still good for: a mechanical test of order-flow / order-book signals (aggressor imbalance, depth) once enough
  days exist -- the one input never tested because Delta keeps no history of it. OWNER, SAME DAY: "Stop everything
  no time for it" -> recorder STOPPED (about 12,000 prints kept in `out/manual/tape/`, 13:17 UTC onward, one
  afternoon only); the forward paper test is NOT being updated (ledger holds 2026-11-15 only). Nothing is running.
  Both can be restarted with the commands above if the owner asks.
- %R RE-CHECK ON CHART BARS (owner 11-16: "Check wpr again"; inline). The one way my %R could differ from his chart:
  Delta REST fills no-trade minutes as flat bars, a chart has no bar there, so chart %R(140) = last 140 TRADED bars.
  Both versions side by side, before fees: same zone 97% of minutes on his coin-days (18% no-trade minutes), 92% on
  the 30 perps all days (47% no-trade). After a close above -20 / below -80: filled +0.023 / +0.032 / +0.024% at
  5 / 15 / 60 min vs chart +0.021 / +0.029 / +0.018% (random bar ~0) on A; ~0.00% both ways on U. The 18 entry x
  exit cells: every pair within 0.01-0.07% of each other, all within +-0.08% of zero (A) / +-0.03% (U). The
  filled-bar column reproduces the earlier zero-fee table exactly. Conclusion unchanged; the bar convention is not
  the reason.
- **SPIKE FADE (+5% entry, 10% stop, 8 h) -- TESTS MEANT TO BREAK IT (owner 11-16 "Run more tests";
  `scripts/spike_robust.py` -> `out/careful/spike_robust.txt`; base asserted equal to `spike_flip_back.trade` on all
  617 trades; before fees, STRICT).** Base +0.823% (t 2.14), lenient +1.010% (t 2.75). CONTROLS on the same picks,
  same stop and clock: short at a random minute -0.074% (t -0.6, 20,980); short every pick at 00:02 +0.212% (t 0.75,
  1,049) -- the waiting-for-the-spike entry is what pays, not the picks. (The 432 picks that never reached +5% were
  +5.2% shorts from 00:02 -- hindsight, the rule cannot know.) MARKET: a BTC short over the same windows 0.00%,
  correlation +0.12, result with BTC's share removed +0.825% (t 2.15): not market direction. WHERE: 2025 +0.58%,
  2026 +1.09%; half-years +0.75 / +0.45 / +1.43 / +0.69% (none significant alone); pick 1 +0.68%, pick 2 +1.05%;
  FILLED 00:03-06:00 +1.15% (t 2.2, 407) vs FILLED LATER +0.20% (t 0.35, 210); months 14 up / 9 down, 2026-08 -92%
  then 2026-09 +168% (sum of trade returns); without the 5 best days +0.52% (t 1.4); day-resampling 5-95%
  +0.20..+1.44%, <= 0 in 1.5%. NEIGHBOURS: entry +3/5/7/10% x stop 8/10/12/15% x clock 6/8/10/12 h: 64 of 64
  positive, +0.38..+1.35%, smooth (wider stop and +7% entry a little better). Survives every attack; still t ~2,
  leans on a handful of big days, and the first forward day was two stops.
- **SPIKE FADE AUDIT (owner 11-16 "run as many tests as you want, check the strategy"; `scripts/spike_audit.py` ->
  `out/careful/spike_audit.txt`).** FUNDING, never charged before: known for 611 of 617 trades, median 2 settlements
  per 8 h trade (hourly coins up to 8); mean -0.10% of the position (the short pays), median 0; five trades carry
  37% of it (SOPHUSD 2025-12-20: -10.5% on a +9.5% trade; INITUSD -6.1%); without those five -0.06%. ALL COSTS IN
  (real fees + funding): STRICT +0.648% (t 1.68; 2025 +0.39%, 2026 +0.93%), LENIENT +0.835% (t 2.27). LIQUIDITY:
  fill minute median $14k traded, under $1,000 on 7%, under $5,000 on 26% -- result the same in every liquidity
  bucket (+0.71..+1.23%); but 74% of trades leave by the clock and 39% of ALL trades leave in a minute with under
  $1,000 traded (13% with no trade at all): the 0.1% exit slippage is optimistic there (an extra 0.3-0.5% on those
  = -0.12..-0.20% per trade). WALK-FORWARD over 64 settings: best-on-2025 (+7%, 15%, 8 h) makes +1.26% in 2026;
  best-on-2026 makes +0.73% in 2025; 62 of 64 positive in both years; rank correlation of settings between years
  -0.30 (no setting is reliably best -- do not tune). LOSS TAIL: 158 stops (26%), average -10.8%, 11 worse than
  -12%, and ONE at -59% STRICT (API3USD 2025-08-19, stopped one minute after the fill in a one-minute spike;
  lenient -10.1%) = -18% of the account at 3% risk. ACCOUNT from $3,184, all costs: 2% risk $6,326 .. $8,052, deepest
  drop 31% .. 29%, worst month -18%, 14-15 of 23 months up, longest losing run 7, under water up to 182 days; 3%
  risk $8,196 .. $11,875, drop 44% .. 40%. Universe is today's listed perps (delisted coins absent: survivorship,
  direction unknown). Breadth test on untouched coin-days pre-registered (`docs/spike_breadth_prereg.md`,
  `scripts/spike_breadth.py`) -> RESULT below.
- **SPIKE FADE BREADTH TEST -- RESULT (2026-11-16; `out/careful/spike_breadth.txt`; second implementation identical).**
  3,821 untouched coin-days 2025-01..2026-10; 931 dropped for no trade in 00:00-00:02 (no entry price, same convention
  as the picks). Before fees, STRICT. RANK 3-5 (pre-registered test): 639 trades, win 55%, +5.1% vs -6.0%, mean
  +0.159% (t 0.55), 2025 +0.19%, 2026 +0.13%, after real fees +0.085% -- passes the letter of the bar (> 0 overall and
  in both years), a fraction of the picks' +0.82% and indistinguishable from zero. By rank (post hoc): 3rd +1.14%
  (t 2.3, 245), 4th -0.15%, 5th -0.84%. CONTROLS: other gainers (rank 6+) -0.35%, non-gainers -0.73% (t -2.1):
  selling any 5% intraday spike LOSES; the effect is confined to the top 2-3 previous-day gainers and fades with
  rank. Strengthens "the pick matters", weakens "the size of the edge": the two-pick +0.82% is the top of a falling
  gradient, so the true edge is probably below what the 617 trades show.
- SPIKE FADE ENTRY WITH A TIGHT STOP / EARLY EXIT / HIGH LEVERAGE (owner 11-16: "high leverage, early exit, same
  point, avg win > avg loss, more money per trade"; `scripts/spike_tight.py` -> `out/careful/spike_tight.txt`; 24 h
  cells asserted equal to `review_real_fees.trade`). 100 cells: stop 1/1.5/2/3/5% x target none/2/3/5 stops x clock
  30 m..8 h, before fees. Avg win > avg loss in 87, but STRICT > 0 in only 19, in both years in 8 (all at the 5%
  stop), and NONE beats the wide version per unit of risk (+0.082R): best STRICT +0.076R = stop 5%, no target, 8 h:
  win 44%, +7.4% vs -5.2%, mean +0.38% strict (t 1.15) / +0.68% lenient (t 2.24), after fees +0.30%, ~10x leverage
  possible; stop 5% / 4 h: 50%, +5.5% vs -5.0%, +0.26% / +0.54%. Stops of 1-2% (20-25x) are negative under STRICT in
  every cell (best -0.08R, worst -0.43R); 3% is zero. Tightening the stop raises leverage, not money per unit
  of risk: the spike keeps running a few percent before it gives back, and a tight stop is inside that noise.
- SPIKE SIZE (owner 11-16: "find another strategy ... losers should not equal winners in number or size, high
  leverage quick exit"; `scripts/spike_size.py` -> `out/careful/spike_size.txt`; entry 5/7/10/15/20/30% above the
  00:02 close x stop 5/10% x clock 1/2/4/8 h, before fees, STRICT; picks = discovery, breadth coin-days = check).
  A BIGGER SPIKE DOES NOT GIVE BACK MORE: on the picks +7% is the top (5% stop, 8 h: 508 trades, win 45%, +8.1% vs
  -5.3%, +0.80%, +0.159R, t 2.1; 10% stop, 8 h: win 56%, +8.2% vs -8.0%, +1.08%, t 2.3), +10% about equal to +5%,
  +15% zero, and shorting +20% / +30% spikes LOSES (-0.4..-1.3% / -1.4..-3.6%, t to -2.8): monster spikes keep
  running. CHECK does not back the +7% choice (3rd-5th gainers at +7%: -0.07..-0.35% in all 8 cells; other coins
  negative throughout) -- consistent only with "the effect is in the top two gainers at a modest spike". No cell
  anywhere has win > 55% AND avg win clearly > avg loss with a 5% stop or a clock under 4 h; the nearest are
  +7% / 10% / 8 h (56%, 8.2 vs 8.0) and +5% / 5% / 4 h (50%, 5.5 vs 5.0). Both properties at once would mean
  +1% or more per trade; the largest real effect in this data is +0.2..+0.8%.
- **SEVEN NEW INTRADAY FAMILIES (owner 11-16: "you seem to look at the same families, search for families";
  `docs/intraday_families_prereg.md`, `scripts/intraday_families.py` -> `out/intraday_families.txt`; 30 liquid Delta
  perps, 1m, DEV 2026-03..07, TEST 2026-08..11, in at the next open, out N minutes later, before fees; per family
  the best DEV variant + direction is the only one judged on TEST, bar t >= 2.7 and both TEST halves > 0).**
  F1 BTC LEAD-LAG (27 variants): TEST -0.006% (t -0.2) FAIL. F3 VOLUME CLIMAX (6): -0.02% (t -0.1) FAIL.
  F4 HOURLY CROSS-SECTION: not runnable as written (rarely 8 liquid coins a day); deviation with traded coins,
  2 a side: DEV -0.007 / -0.012%, TEST +0.007 / -0.009% -- nothing. F6 YESTERDAY'S HIGH / LOW (3): +0.09% (t 0.4),
  halves +0.32 / -0.09% FAIL. F7 HOUR OF DAY (24): DEV's best (short 13:00 UTC, t 2.7) is -0.03% on TEST FAIL.
  F5 SQUEEZE BREAKOUT (3): following LOSES in all three holds in both periods; the mirror -- FADE a close outside a
  quiet hour's range, hold 4 h -- DEV +0.10% (t 1.65), TEST +0.20% (t 1.92, halves +0.22 / +0.18%), win 54%, avg
  win = avg loss; FAIL on the bar, the only consistent lead: pooled 5,370 trades +0.151% (t 2.48), 7 of 9 months up,
  random entries +0.02%; it is in the small coins (+0.48%, t 2.3) not BTC/ETH/SOL/XRP (+0.03%), longs +0.20% /
  shorts +0.10%, and only at 4-8 h (30 min +0.00%, 60 min +0.01%, 2 h +0.05%, 8 h +0.20%) -- slow give-back again,
  not a quick trade; after two taker fees +0.03%. F2 LAST vs FAIR PRICE (mark candles in `out/marks/`, 9 variants):
  DEV's best (gap 0.4%, trade toward the mark, hold 30 min: +0.30%, t 1.9) is -0.18% on TEST (t -1.6, both halves
  negative); every variant flips sign between the two periods. FAIL. All twelve families done: 0 pass.
- **FIVE MORE FAMILIES (owner "Search more families"; `docs/intraday_families2_prereg.md`,
  `scripts/intraday_families2.py` -> `out/intraday_families2.txt`; same data, split, selection and bar): 0 PASS.**
  H1 OPENING RANGE (first 30/60 min of the UTC day, follow or fade): TEST -0.01% (t 0.0). H2 FIRST 8-12 h PREDICT THE
  NEXT 8 h: +0.67% on TEST but t 0.8 and halves -0.60 / +1.64% -- noise. H4 MARKET BREADTH (80-90% of coins one way
  in an hour -> BTC/ETH/SOL): +0.008% (t 0.3). H5 SLOW LEAD-LAG (BTC 1-2% in an hour -> alts): chosen mirror +0.48%
  (t 1.5), first half negative. H3 PAIRS among BTC/ETH/SOL/XRP (ratio 2-3 sd from its 24 h mean, sell rich / buy
  cheap) is the one STATISTICALLY REAL effect: positive in all six variants in BOTH periods (z 2 / 60 min: DEV
  +0.014% t 4.0, TEST +0.010% t 1.85; z 3 / 4 h: +0.044% t 2.2 / +0.054% t 2.1; z 3 / 12 h: +0.042 / +0.089%), win
  55%, wins = losses -- and an order of magnitude below the cost of trading two legs (0.118% per leg taker):
  after fees -0.11%. Twelve families on this data: no pass; two real effects (pairs reversion, fading quiet-range
  breakouts), both slow and smaller than costs.
- **TIMED TRAIL INSTEAD OF THE BREAK-EVEN LOCK (owner 2026-11-16: "ready to run 20% capital ... I would change lock
  to trailing SL; since most trades flip the trailing SL can be timed instead of price or % fall";
  `scripts/careful_timed_trail.py` -> `out/careful/timed_trail.txt`; Spike Sell exactly as agreed otherwise; two
  implementations equal on every trade for all 28 exits at 10% and 20% margin; a third per-trade check agrees).**
  From $3,184, full history, STRICT .. LENIENT, 10% margin: exit C as agreed $1,801 .. $3,890 (drop 67% .. 53%, 11 ..
  22 of 22 starts above $3,184). PLAIN 5% TRAIL FROM THE FILL, NO LOCK: **$6,074 .. $8,386, drop 35% .. 30%, 22 .. 22
  starts above**, 94 trades, win 37%, avg win +10.4% of the balance vs avg loss -4.5%. THE CLOCK HARDLY MATTERS: 5%
  trail started after 15 / 30 / 60 min $6,041 / $4,722 / $5,626 strict; after 2-8 h ~$4,050; a timed break-even
  lock is worse ($1,063 .. $2,609 strict for 15 min-4 h; 8 h $4,987). TIGHT TRAILS ARE WORSE THAN EXIT C: 2.25% and
  3% trails $993 .. $3,075 strict. 20% MARGIN: plain 5% trail $6,588 .. $12,660 but drop 62% .. 54% (exit C at 20%
  ends at the floor): strict fills gain almost nothing over 10% for twice the drawdown. WHAT THE RESULT RESTS ON:
  (a) per trade over ALL 617 fills the plain trail is no better than exit C (-0.15% strict / +0.20% lenient of the
  position; exit C -0.11 / +0.22%); (b) the account gain is the MONTH-OFF rule: without it the same exit ends at the
  $1,000 floor in 2025; (c) the exact rule matters -- month off after 1 / 2 / 3 / 4 / 5 full stops: $2,773 / $6,074 /
  $3,191 / $2,683 / $2,144 strict (lenient $3,146 / $8,386 / $5,203 / $6,087 / $6,765), exit C at the same settings
  $2,936 / $1,801 / $979 / $982 / $996; (d) five winners carry it: total +$2,890 strict, without them -$1,803; 12 of
  23 months up. Reading: dropping the early lock for a wide trail is not worse than exit C at any month-off setting
  and usually better with smaller drops; the $6k figure is the lucky centre of that range. Not built into the bot.
  **OWNER DECISION 2026-11-16: MONTH-OFF RULE REMOVED** ("We will [not] halt post 2 stop, remove that setting"; asked
  once with the numbers -- keep 2 / loosen to 3 / remove -- answered "Remove it entirely"). Working tree only, NOT
  committed, NOT deployed: `live/spike_live.py` `MONTH_STOP_LIMIT = None` (a number restores the rule; full stops
  still counted by `month_stops`), `tests/live/test_spike_live.py` (the old test now asserts the third day's order
  RESTS; a new test pins the rule when a limit is set), `docs/fade_live_spike_prereg.md` rule 9 rewritten with the
  tested consequence (exit C without it $955 .. $4,304, drop 70% .. 59%; plain 5% trail without it the $1,000 floor
  in 2025). The $1,000 floor and HALT rules are untouched.
  **OWNER 2026-11-16, next message: "1 approved / 2 show me number / 3 wait"** = build the plain 5% trail; show the
  margin numbers; do NOT commit yet. BUILT IN THE WORKING TREE (uncommitted, undeployed): `live/spike_live.py`
  `BREAKEVEN_LOCK = False` (`_exits` goes straight to `_trail`, which starts from `stop_price`; `month_stops` and the
  replay ledger treat a NULL lock_price AND trail_price as "the original stop"; STOP_FILL logs `trailed`); the
  existing tests keep exit C through an autouse fixture, 4 new `test_plain_trail_*` tests; `scripts/replay_bot.py`
  follows `S.BREAKEVEN_LOCK` / `S.MONTH_STOP_LIMIT`. VERIFIED: tests/live + tests/live_exec 2,915 passed; replay of
  the REAL bot code vs the research rule: full history 93 / 131 trades identical, wallets $948.02 / $994.07, state
  STOPPED (the floor); from 2026-01 216 of 217 identical ($5,033.75 vs $5,046.19) -- the one difference is SIZE, not
  the exit: PIEVERSEUSD 2026-11-10 above its $10,000 leverage tier, bot 19x whole-number leverage $10,039 vs the
  research formula's $10,507. MARGIN NUMBERS for these rules (plain 5% trail, no month-off, floor $1,000), from
  $3,184, strict .. lenient: 5% $979 .. $4,708 (floor 2025-10 / never; 5 .. 0 of 22 starts under $1,000); 10% floor
  $948 .. $994 (2025-06 .. 08; 2026 only $5,046 .. $17,595; 10 .. 7 starts under); 15% floor by 2025-04/05 (16 .. 12
  under); 20% floor by 2025-04 (2026 only also the floor; 19 .. 16 under; worst month -$2,150).
  Per trade at a fixed $3,184 (owner asked): 5% margin win 29-30%, avg win +$179 .. +$176, avg loss -$76 .. -$69, a
  liquidation -$240 (extra margin makes the loss larger than the 5%), average -$1.90 .. +$5.63 a trade, 2025 lost /
  2026 won both ways, longest losing run 11; 20% margin: same win rate, avg win +$717 .. +$706, avg loss -$305 ..
  -$277, liquidation -$962, average -$7.60 .. +$22.55.
  **OWNER 2026-11-16: "We'll use 20% margin."** BUILT (working tree, uncommitted, undeployed): `TRADE_MARGIN = 0.20`,
  `EXTRA_MARGIN = TRADE_MARGIN * 25 / 49` (the tested ratio, 10.2% of the balance), docstring and prereg updated with
  the consequence, tests updated (incl. PIEVERSE: the run's $637 is above its $10,000 tier -> under 20x). tests/live +
  tests/live_exec 2,915 passed; replay of the real bot = research rule on every trade at 20%: full history 51 / 60
  trades ($984.63 / $950.63, STOPPED at the floor), from 2026-01 65 / 81 ($961.38 / $987.13, STOPPED).
  Then "Commit push and merge then deploy all approved": committed (f733116 research, a212f75 live), #168 merged
  (e393c80), deploy-prod 43935278199 approved and completed 19:30 UTC; probe PASS (see SPIKE SELL -- MERGED below).
- LOOSENING THE OWNER'S FOUR WISHES (owner 11-16 "try to loosen my criteria"; inline on the spike-fade entry, 120
  combinations of stop 3/5/7.5/10% x target 2..10% or none x clock 1 h..24 h, before fees, STRICT). Wishes scored
  separately: MORE WINNERS (win > 55%), BIGGER WINS (avg win > avg loss), LEVERAGE (stop <= 5%, ~10x), QUICK (out
  within 4 h). 47 combinations are positive in both years; NONE meets all four; none meets "more winners AND bigger
  wins" at all; only 2 meet three -- give up MORE WINNERS: stop 5%, no target, 4 h: win 50%, +5.5% vs -5.0%, +0.26%
  (t 0.9), after fees +0.19%. Give up LEVERAGE and BIGGER WINS: stop 10%, 4 h: win 59-60%, +5.3..5.5% vs -6.5..6.6%,
  +0.51..0.53% (t 1.6-1.8), after fees +0.44..0.46%. Give up MORE WINNERS and QUICK: stop 5%, 8 h: win 44%, +7.4%
  vs -5.2%, +0.38% (t 1.15). All three pay the same per unit of risk (R ~ +0.05..0.08): the choice is taste.
  Nothing positive exits inside 2 hours.
- THE NEW FAMILIES UNDER A LOOSER BAR (owner 11-16: "I mean for new family loosen the criteria"; inline, all 75
  variants of the 10 runnable families, direction = DEV's sign; looser bar = same sign on DEV and TEST, 200+ trades
  each, pooled t >= 2 by day; before fees). 37 of 75 have the same sign in both periods; 7 clear the looser bar:
  PAIRS x4 (z 2 / 1 h +0.013% t 4.1; z 3 / 4 h +0.048% t 3.1; z 2 / 4 h +0.024%; z 3 / 12 h +0.062%; win 56-58%,
  wins = losses; after two legs' fees -0.17..-0.22%); FADE THE QUIET-HOUR BREAKOUT, 4 h (+0.151% t 2.5, win 54%,
  1.75 vs 1.72; after fees +0.03%); LONG THE 08:00 UTC HOUR (+0.170% t 2.6, win 50%, avg win +1.18% vs avg loss
  -0.85%, after fees +0.05%) and LONG 21:00 (+0.127% t 2.1). THE HOUR RESULT IS NOT A SETUP: 14 of 24 hours are
  positive long in both periods because the coins drifted up (any hour: DEV +0.017%, TEST +0.068% t 2.6); 08:00 is
  a funding settlement but 00:00 is -0.15% on TEST and settlement hours as a group equal other hours on TEST
  (+0.071 vs +0.067%); two hits out of 24 hours x 2 directions is what chance gives. Nothing in the looser list is
  quick AND bigger than its costs: pairs is real and ~10x too small; the quiet-breakout fade is the same slow
  give-back in small coins.
  EXHAUSTION CHECK (owner: "above -20 is where moves have exhausted and selling starts"; inline on U, 19.6k events a
  side): after the 1m %R closes above -20 price does what a RANDOM bar does -- +0.012% at 5 min, +0.055% at 60 min,
  +0.226% at 4 h vs random long +0.009 / +0.053 / +0.234%; +1% before -1% 33% vs 32%; same for closes below -80 vs
  random shorts; Supertrend side changes nothing. Neither exhaustion nor continuation: the cross carries no
  information, which also says the breakout lead on U is mostly the rising market.
- WHEN THE 1m %R CROSSES ITS BAND, HOW THE 5m BEHAVES (owner 11-16; `scripts/wpr_1m_to_5m.py` ->
  `out/wpr_1m_to_5m.txt`; U and B-liquid agree; second implementation reproduces U). At a 1m close above -20 / below
  -80 (own terms, vs a random bar from the same 5m zone): the 5m %R is already in the band ahead 36-38% (random
  18-20%), between the bands ~60%, still behind 3-5%; the 5m Supertrend is on the event's side 88% (50%). The 5m then
  gets to the band ahead before the band behind 30% vs 20% from -80..-65, 50-52% vs 41-42% from -65..-50, 67-70% vs
  57-59% from -50..-35, 86-88% vs 80% from -35..-20; within 1 h 26-29% of those not yet there. So the 1m event adds
  ~+8..13 points to the 5m's odds -- the owner's "the 5m tries to reach the other side" is real. PRICE over the next
  15 min / 1 h is the same as after a random bar in every zone (+-0.05%): the 5m is LAGGING -- its last closed bar has
  not yet printed the move the 1m just made, so the 5m "following" is mostly that move being recorded, not new move.
- At 20x the trade has NO edge, whatever the stop: 23 entry designs (limit +3..15%, sell-after-the-turn; `careful_entry_grid.py`)
  and 48 exit designs (stop 1..3% x target $500..none x lock; `careful_exit_grid.py`) -- 0 of 71 positive on 2025.
  Lenient loss ~ the trading cost ($30-50/trade): zero edge minus costs.
- WHY (`careful_drift.py`, `out/careful/drift.txt`): the short at the +5% limit drifts 0.0% in 15 min, +0.2% in 1 h, +0.8%
  in 4 h, **+1.4% in 24 h** (wins 64%; 2025 +1.56%, 2026 +1.28%), but goes >2.25% against it in 82% of trades, >5% in
  62%, >10% in 38%. The 20x trade lasts a median 10 minutes -- where the drift is zero.
- WIDE STOP, HOURS-LONG HOLD, risk-sized (`careful_wide_stop.py`): 47 of 54 designs positive on 2025, 46 of those also
  on 2026 (strict). Chosen on 2025: limit +5%, stop +10%, no target, out after 24 h. With Delta funding
  (`careful_wide_account.py`; shorts PAY a mean 0.30%/day on these picks): **+0.055 (strict) .. +0.080 (lenient) R per
  trade**, 617 trades (28/month), t +1.2, bootstrap P(<=0) 10% -- small and NOT yet statistically proven; half-years
  +0.10 / 0.00 / +0.07 / +0.07 R. Kelly 4.2% of balance per trade. Accounts from $3,184 (no month-off -- the 2-stops rule
  kills it; floor $1,000): risk 2% $6,068 .. $7,376 (max drawdown 38%), 3% $7,475 .. $10,050 (53%), 5% $9,134 ..
  $15,109 (73%); 22 of 22 monthly starts end above the start at 2-5%. Risk 10%+ overbets: 23.5% ($750/trade, today's
  stop size) is under $1,000 by Jul 2025. Fixed risk: +1.5 .. +2.2 R a month, worst month -10.6 R, 14 of 23 months up.
- Funding filter re-check (skip D-1 funding < 0, `careful_wide_more.py`): +0.111 .. +0.127 R on the kept half (2025
  +0.16, 2026 +0.08), drawdown roughly halves (risk 3%: 30% vs 53%), but on 2026 alone no better than random skipping.
- So: $2,000/month needs ~1 R = $900-1,300 = 28-40% of $3,184 per trade = ruin; it is a capital question (~$30k at 3%).
- MARK-price stop trigger (Delta's own MARK 1m candles, `careful_dataset_mark.py`, `careful_mark_stop.py`): avoids only
  3 of 190 stops; -$97 vs -$94 per trade strict (paired t -0.5). The stops are real moves, not wicks on Delta's book. CLOSED.
- BUY instead of sell at the +5% fill (owner: "price goes against the short >2.25% in 82% ... can we not buy here?",
  `careful_long_instead.py`): over the 24 h the price is also >= 2.25% BELOW the level in 90% (>= 5%: 75% vs 62% above);
  +2.25% comes first 49%, -2.25% first 51%. 20 long designs (targets 1..10%/none x stops 1..10%): 0 positive on both
  years; -0.3% (lenient) .. -1% (strict) of the position per trade = -$83 .. -$437 at $30,472. CLOSED (again, on Delta data).
- MARKET CONTEXT before the fill (owner: "can you not find a solution to the SL problem with the spike sell setup?";
  `careful_fill_context.py`, Delta volume + MARK premium + BTCUSD 1m via `careful_dataset_btc.py`): 10 readings (5-min
  volume ratio, perp premium, 1 h realised vol / range, run from the day's low, gap to the day's high, pressing under
  the limit, last close, BTC 1 h / day), 100 skip rules: 1 positive per kept trade on 2025, 0 on 2026; stops and non-stops
  have the same medians; a logistic model on all readings: safest quarter +$47 on 2025 -> -$94 .. -$39 on 2026. CLOSED.
- BOOK A SMALL PROFIT on every trade (owner: "before turning against us does the trade go in favour? ... book small
  profit at that point"; `careful_small_target.py`): yes it goes in favour first -- before the 2.25% stop 85% of trades
  fall >= 0.1%, 82% >= 0.3%, 77% >= 0.5%, 69% >= 1%, 57% >= 1.8% (even of later-stopped trades 78% >= 0.3%) -- but 18%
  never do and each costs ~$900: net +$25/50 wins 82% (needs 95-97%), +$100 77% (needs 90%), +$200 72% (needs 82%),
  +$500 57% (needs 64%). 28 designs (net $25..500 x stop 1.5..4.5%): 0 profitable in both years, even with touch fills
  and stops at their price. CLOSED.
- TRAILING STOP from the fill instead of the lock (owner: "how about a trailing stop loss per trade instead of a
  lock?"; `careful_trailing.py`): trail 0.5..3% above the lowest low, starting from the 2.25% stop or as a pure trail,
  with / without the $500 target -- 22 designs, 0 profitable in both years; -$83 .. -$135 strict, -$5 .. -$68 lenient
  per trade on 2026 (as built -$94 .. -$32). A tight trail makes the losses small (-$340) but the wins too (+$270..390)
  and only 33-39% win: the same zero-edge-minus-costs. CLOSED.
- QUICK EXIT by the clock / first profitable close (owner: "booking a quick profit per trade before it flips";
  `careful_quick_exit.py`): buy back 1..60 min after the fill, or at the first 1m close netting >= $0..200 (stop 2.25%):
  0 of 13 profitable in both years, but they LOSE LESS than as built on the same fills: first close >= $50: 2025 -$62 ..
  -$36, 2026 -$35 .. +$5 (as built -$94 .. -$49 / -$94 .. -$32); 77% of trades book ~+$220-280, 23-26% stop at ~-$900.
- QUICK BOOK as a full account (owner: "test what you found"; `careful_quick_book.py`, two implementations agree on
  every trade): first close netting >= $50 x balance/$3,184, stop 2.25%, no target/lock/trail. Every trade at fixed
  $3,184: -$19,321 (strict) .. -$4,829 (lenient) over 459 trades (as built -$40,700 .. -$18,224); 341 booked avg +$246
  (median 2 min after the fill), 118 stops avg -$876 .. -$753; 8 of 23 months up. Bot account from $3,184: full history
  $828 .. $763 (floor by May-Jun 2025, as built $836 .. $854); 2026 $995 .. $810; 20 real picks $7,140 (11 booked, 1
  stop); 22 monthly starts under $1,000 in 20 .. 11 (as built 22 .. 14), above start 1 .. 10. Halves the bleed; still
  loses; still ends at the floor.
- NO STOP-LOSS AT ALL (owner: "how about no SL at all?"; `careful_no_stop.py`, two implementations, liquidation on LTP
  strict / MARK lenient): liquidation becomes the stop and takes margin + extra. Quick book, 25% extra (liq ~+5.1%):
  13% of trades liquidated at $2,356 each (74% of the balance), every trade at fixed size -$45,118 .. -$53,780 (with the
  2.25% stop -$19,321 .. -$4,829); 51% extra (liq ~+7.8%): 10% liquidated at $3,184 (100%), -$38,486 .. -$43,096. $500
  target: 25% / 17% liquidated, -$98k / -$56k. Account from $3,184: the FIRST trade (LDO 2025-01-15) is liquidated ->
  $828; all 22 monthly starts end under $1,000 (median 3-6 trades to the first liquidation); real picks $2,405
  (ESPORTS liquidated, -$4,088). CLOSED: the 2.25% stop is what keeps a loss at ~$900 instead of ~$2,400.
- NO STOP FIRST, STOP / CUT LATER (owner: "people having no SL first and when things go south they apply SL or cut";
  `careful_late_stop.py`, two implementations, quick-book exit): grace 2..30 min then stop 2.25/3.5%; cut on a 1m close
  1..3% against; cut if still losing after 5..60 min (with / without the 2.25% stop) -- 22 versions, 0 profitable in
  both years, all -$23 .. -$108 per trade (stop from the start -$62 .. -$41 / -$46 .. -$22). A grace period books more
  trades (76-85% vs 74%) but liquidations go 1% -> 2-8% and the average bad trade from -$900 to -$1,000..-$1,800.
  Least bad: 2.25% stop + cut if losing after 10 min (-$50 .. -$38 / -$40 .. -$19) -- within noise of the plain stop. CLOSED.
- WHAT QUICK BOOK GIVES UP (`careful_left_on_table.py`): of its booked trades 76% later reach +$500, 54% +$1,000, 35%
  +$2,000 (best point avg +$2,012), but held with the 2.25% stop 76% come back and stop (-$847), 24% are open at 24 h
  (+$3,502): held avg +$195 vs booked +$237 (2025 +$31 vs +$202; 2026 +$377 vs +$276).
- BEST WAY TO TRAIL (owner: "what is best way to trail?"; `careful_trail_ways.py`, 27 methods, two implementations):
  pct from the fill, break-even-then-trail (activation $50/150/300 x trail 1..5%), last-M-minute high, k x 1m range,
  step ratchet. 0 profitable in both years strict; 2 lenient. Best on 2025 = "after the best point nets +$150 move the
  stop to break-even, then trail 5% above the low": 2025 -$52 .. +$12, 2026 -$36 .. +$55 (quick book -$62 .. -$41 /
  -$46 .. -$22; as built -$94 .. -$49 / -$94 .. -$32); 13% of trades win avg +$1,861, the rest avg -$320. Pattern:
  early break-even + a WIDE trail (5%, 60-min high, 20 x 1m range) beats every tight trail; roughly break-even between
  the fill bounds, not a proven profit.
- BREAK-EVEN + 5% TRAIL as a full account (owner: "Run all tests"; `careful_be_trail.py`, two implementations agree
  on every trade; activation scales with the balance; exits after the stop moved do not count for month-off): every
  trade at fixed $3,184: -$17,470 (strict) .. +$16,585 (lenient) over 452 trades -- the whole result is how break-even
  stops fill (losing trail exits avg -$98 strict vs -$40 lenient); 12% winners avg +$1,990, top 10 winners = +$45k;
  months up 5 .. 15 of 23. Bot account from $3,184: full history $774 .. $981 (under the floor by Apr 2025: two full
  stops in Jan 2025 before any big winner); 2026 $992 .. $872; 20 real picks $7,356 .. $8,895; 22 monthly starts under
  $1,000 in 21 .. 16 (as built 22 .. 14, quick book 20 .. 11), above start 1 .. 6. Not better than quick book as an account.
- 10% MARGIN (owner: "Run number with 10% margin"; `careful_margin.py`, two implementations per exit, 49% reproduces
  the earlier results): position ~$6,219 at 20x, stop ~$150-180, every $ level scaled x 10/49, extra margin 5.1%
  (liq ~+5.1%). Bot accounts from $3,184, strict .. lenient, full history: as built $1,843 .. $2,811 (deepest drop
  55% .. 34%), quick book $2,206 .. $2,830 (47% .. 44%), break-even + 5% trail $1,801 .. $3,890 (67% .. 53%); never
  under the $1,000 floor in any of 22 monthly starts. 2026 from Jan 1: $2,182 .. $2,949 / $3,197 .. $3,889 / $3,784 ..
  $6,071. 22 starts ending above $3,184: as built 0 .. 9, quick 9 .. 16, BE-trail 11 .. 22 (median $3,177 .. $5,345).
  20 real picks $3,611 .. $4,303. 20% margin: full history back at the floor in strict for all three. So 10% survives
  and hovers around break-even (a slow bleed on strict fills); it does not make $2,000 a month.
- MARGIN SWEEP 5/10/15/20/30/49% (owner: "testing on different margins"; `careful_margin_sweep.py`,
  `out/careful/margin_sweep.txt`, two implementations agree at every margin). Full history end from $3,184, strict ..
  lenient: as built 5% $2,483 .. $3,050, 10% $1,843 .. $2,811, 15% $1,299 .. $2,492, 20% $962 .. $2,121, 30%+ at the
  floor; quick book 5% $2,719 .. $3,064, 10% $2,206 .. $2,830, 15% $1,697 .. $2,507, 20%+ floor; BE + 5% trail 5%
  $2,554 .. $3,738, 10% $1,801 .. $3,890, 15%+ floor. No margin is profitable on strict fills for any exit; margin only
  scales the result (losses and the real-picks gain alike: real picks +$210..$540 at 5% -> +$2,500..$5,700 at 49%).
  22 starts never under $1,000 at <= 10% (quick book also at 15%); BE-trail starts above $3,184: 13..22 at 5%, 11..22
  at 10%, 9..20 at 15%, 8..16 at 20%, 3..11 at 30%, 1..6 at 49%.
- ONE BIG TRADE (owner: "one for sure trade with 75% margin, let it run till 1000 profit and stop the bot for a
  week"; `careful_big_one.py`, two implementations): top pick only, 75% margin at 20x (~$46.6k), +20% extra, take
  profit at net +$1,000 (a 2.26% fall), stop 2.25%, 7 days off after a win. Per trade: +$1,000 first 48%, stop 50-52%
  (avg -$1,348 strict .. -$1,150 lenient), 10 liquidations strict; -$226 .. -$123 per trade. Account from $3,184:
  full history $499 .. $855 (the first three trades, Jan 15-17 2025, are stops: $681 strict, done); 2026 $627 .. $860;
  22 starts under $1,000 in 21-22; real picks $5,185 (INIT 10-29 and AIOT 11-09, both +$1,001). CLOSED.
- ONE-TIME +$2,000 GOAL (owner: "we just need 2000 as profit"; `careful_goal_2000.py`, from each of 615 pick days,
  run to +$2,000 or the $1,000 floor): 75% / +$1,000 rule reaches +$2,000 first in 204 .. 216 starts (33-35% of all),
  the floor first in 396 .. 385 (63-64%), median 10 days either way. Exit C at 10%: never the floor; +$2,000 in 86
  (14%, strict) .. 338 (55%, lenient) of 615 starts, median 74 .. 215 days; the rest not there by 2026-11-14.
  Early cuts on the 75% trade (`careful_big_one_cut.py`: stop 1/1.5%, cut on a close 0.5-1.5% against, cut if losing
  after 2-30 min): all lose, -$24 .. -$266 per trade. CLOSED.
  50% margin (`careful_big_one_margin.py`): +$1,000 target (3.34% fall) wins 39%, -$150 .. -$76 per trade, +$2,000
  goal first in 30-35% of starts vs the floor 63-68%; +$500 target wins 57%, -$103 .. -$49 per trade, goal first only
  15-21% (four wins needed) vs the floor 68-74%. No better than 75%. CLOSED.
- 1% FALL TARGET (owner: "Can we try 1% fall simulator"; `careful_one_pct.py`, live product terms, two
  implementations): top pick, take profit at a 1% fall. Stop 2.25%: 1% first in 67% of trades, break-even needs 74-77%;
  per trade -$24 .. -$14 (10%), -$121 .. -$73 (50%), -$187 .. -$110 (75%). Stop 1%: 49% first, needs 58-66%. Accounts
  from $3,184 all lose (10%: $1,181 .. $1,846; 50%/75%: at the floor); +$2,000 goal first 26-37% of starts at 50-75%
  (stop 2.25%) vs the floor 59-70%. Real picks $5,188 (50%) / $6,218 (75%). CLOSED.
  With BANKING (owner: "you are not considering banking and compounding"; `careful_one_pct_bank.py`, two account
  implementations; compounding was always on): monthly / after-each-win sweeps above $3,184. Full history 50-75%
  margin, stop 2.25%: $595 .. $938 -> $1,487 .. $2,098 (the early-2025 gains are kept), never above $3,184; the
  +$2,000 goal odds do not improve (banking leaves a smaller wallet: floor first as often or more); real picks lower
  ($6,218 -> $5,570 .. $5,939 at 75%). Banking protects gains already made; it cannot fix a losing average. CLOSED.
- ONE-TIME +$1,000 GOAL (owner: "leave 2000 aside, how about 1000"; `careful_goal_1000.py`, 615 starts, average
  per start counting unfinished runs at their Nov 14 total): with the $1,000 floor -- exit C at 10% reaches +$1,000
  first in 40% (strict) .. 80% (lenient) of starts, never the floor, median 110 .. 211 days, avg +$274 .. +$1,199
  per start (the only positive rule); 75%/50% margin +$1,000 target: 52-58% first (usually the first trade), avg
  -$202 .. -$556; 1% fall targets: -$497 .. -$1,108. With a symmetric quit at -$1,000: exit C -$10 .. +$571, all
  others -$129 .. -$854.
- PRODUCT TERMS CHECK (`careful_limits_check.py`): the saved table held only 12 coins; the careful sims sized the
  other 161 on defaults (20x to $50k). Delta's live list (`out/delta_margin/product_limits_live_all.json`, same units)
  for the 173 picked coins: 141 at 20x to $50k, 15 at 20x to $10k, 2 at 10x, 14 at 50-200x (held to 20x by the rule).
  Re-run with the live terms: 75%/50% per trade within $4, goal counts within 4 of 615; exit C at 10% identical
  ($1,801.05 .. $3,889.84; its ~$6k positions are under every cap). Results stand.
  TALLY for the 20x setup: entries 23, exits 48, stop distance / close-confirmed 11, MARK trigger, earlier stop moves,
  buy instead 20, coin filters 72, context rules 100 + model, re-sell, reloads -- nothing passes out of sample.
NOT built. A candidate for a PAPER account (rule family of the paper LADDER/A accounts, entry at the spike).

**RE-SELL PARKED (owner 2026-11-15: "let's park re-sell").** Not built, not on paper; the research
(`scripts/run4_resell*.py`, `careful_sim_*` variants) stays for later.

**CAREFUL SIM of SPIKE SELL (2026-11-15, latest -- supersedes every earlier run-4 number):** rules `docs/careful_sim_spec.md`;
frozen data = Delta's OWN 1m candles, 48 h per pick, all 1,049 picks (1,029 history + 20 real;
`scripts/careful_dataset.py` + `careful_dataset_prevclose.py` for 28 picks with no trade by 00:02); three
implementations (`careful_sim_a/b/c.py`: tested functions / spec-only / minute-by-minute bot state machine) agree on
EVERY trade, all variants, both fills (`out/careful/compare.txt`); 10 hand-worked scenarios pass in all three
(`careful_scenarios.py`). Results (`out/careful/report.txt`), strict .. lenient: per trade at $3,184 run 4 -$94 ..
-$41 (Binance proxy said -$67 .. -$28: Delta is WORSE); re-sell A -$81 .. -$35 (2025 +$37 .. +$89, 2026 -$210 .. -$168).
Accounts with the $1,000 floor: 20 real picks run 4 $5,702 .. $7,248, + re-sell $14,087 .. $17,497 (ESPORTS re-sell now
held its full 24 h, +$5,648, which also blocks Nov 3); 2026 from Jan 1 run 4 $950 .. $825 (floor by May), + re-sell
$800 .. $994; 22 monthly starts: run 4 ended under $1,000 in 22 .. 14, above $3,184 in 0 .. 2; + re-sell + monthly
banking above $3,184 under $1,000 in 15 .. 12, above $3,184 in 1 .. 5. RELOADS (owner: "if we reloaded the $3,000
post 2 stops", `scripts/careful_reload.py`, `out/careful/reload.txt`, A/B/C agree): full history NET (end - $3,184 -
reloads) run 4 -$2,348 .. -$2,330; reload to $3,184 each month -$11,145 .. -$4,793 ($13,040 .. $6,698 put in);
reload at once after 2 stops and trade on -$50,571 .. -$27,395 ($54,158 .. $32,783 put in); of 22 monthly starts
0 .. 4 made money. Reloading feeds a losing rule. STOPS (owner: "huge stops is the real pain point";
`out/careful/stop_pain.txt`, `stop_grid.txt`): of 459 trades the 138 stops cost -$119.7k .. -$103.9k vs +$79.0k ..
+$85.7k from the rest; 86% of stops are back under the stop within 30 min, BUT sitting through them does not pay: touch
stops +1.5..+4.5% and close-confirmed stops (close > +2.25/3/3.5%, hard +3.5/4.5%), chosen on 2025, all do worse than
the as-built +2.25% on 2026 (-$94 .. -$32 per trade; best-on-train -$116 .. -$75) -- fewer stops but bigger ones and
more liquidations. CLOSED. Earlier history numbers also cut every trade
at 23:59 (1-day Binance files).

**ACCOUNT-SIM CORRECTION (2026-11-15, late):** every run-4 ACCOUNT number before `scripts/run4_account.py` took the
day's picks in list order and let the second pick trade after the first closed; the bot (correctly) trades only the
pick that FILLS FIRST and cancels the other, and places nothing while a trade is open. Per-trade numbers were not
affected. Corrected (two independent account implementations agree to the cent, `out/run4_account.txt`), final rules
from $3,184, strict .. lenient: 20 real picks $5,702 .. $7,248; 2026 to Nov 14 $110 .. $700; full history $43 .. $84;
21 starts under $1,000: 20 .. 15. 75% margin, best-ranked pick only: real $7,088 .. $7,125; 2026 $34 .. $50.
With a +$2,000 monthly profit stop (owner's manual habit, `run4_account.CAP`, `out/run4_cap2000.txt`): real $5,702 ..
$6,630; 2026 $158 .. $782; history $43 .. $84 -- it only binds in Mar 2026 and Nov 2026; the losing months are untouched.
HISTORY DATA: Delta's pick list (1,022 of 1,029 picks have a Binance match) but BINANCE 1m candles as the price path;
on the 20 real picks Binance vs Delta 1m give the same outcome 16/20, totals +$1,631 vs +$1,494 strict
(`out/run4_delta_vs_binance.txt`) -- the proxy is, if anything, slightly kinder than Delta.
Delta's panel has NO picks 2026-01-01..14 (and smaller gaps, e.g. 2025-10-16..24). Trace of Jan 2026
(`scripts/run4_month_trace.py`): XAI +$500, BERA -$1,222, ARC -$833 (stopped the minute after the fill), then off.
Full stops never fall 1% first (by construction); 177 of 618 fills, median 6 min to the stop, 71 never traded below
the entry. Break-even after a 0.75/0.5/0.25% fall loses MORE (cuts targets 153 -> 136/118/101) (`out/run4_stop_anatomy.txt`).
LONG the breakout instead (owner: "why not buy the coin when it moves up?", mirrored rules, two implementations agree,
`out/run4_long_breakout.txt`): +5% buy stop, per trade on $3,184: real -$6,934 .. -$3,975, history -$167k .. -$86k
(4x worse than the short); +7.5%: -$134k .. -$71k. CLOSED: these coins mean-revert after a spike, they don't run.
RE-SELL after a full stop (owner: "when SL hits we sell again?", `scripts/run4_resell.py`, `out/run4_resell.txt`), one
re-entry per pick: +5% above the stop fill / +2.25% / at market. Re-entries lose on the history in all three (-$21.6k ..
-$16.0k, -$17.2k .. -$13.6k, -$38.0k .. -$29.4k per trade on $3,184). R1 looks better on the 20 real picks only
because of one trade (ESPORTS 11-02 re-sell +$521); 2026 account $19 .. $607 vs $110 .. $700 as built. CLOSED.
CATCH THE FALL after a stop (owner: "why not catch that falling spike?", `out/run4_catch_fall.txt`): stopped coins DO
settle (after the stop a further +5.5% median, then a 13.9% median fall from that top before 24:00, 69% end the day
under the stop price) but a market sell Y% off the running high loses on the history for Y = 1/2/3/5% (re-entry P&L
-$47.9k .. -$15.3k per trade on $3,184): the first Y% dip is usually not the top -- it rallies 2.25% and stops again
(70-80 stops vs 28-43 targets of ~177). CLOSED.
MIDWAY (sell 7.5/10/15% off the high, or back below our stop / entry price; `out/run4_catch_midway.txt`): from every
such point the next move is a coin flip -- it falls 1.8% before rising 2.25% only 40-49% of the time (break-even needs
~56% before costs); every variant's re-entries lose on the history. CLOSED.
RE-SELL SEARCH with its OWN stop/target (owner: "when is yours to find out"; `scripts/run4_resell_search.py`, checked by
`run4_resell_search_check.py`): 350 settings (when x stop x target), risk-sized, in R; chosen on 2025 (91 events),
judged on 2026 + real (86). Train-best (5% off the high, stop +2.25%, hold 24 h): +0.53 R on 2025 -> +0.07 .. +0.14 R
on test (43% chance <= 0); by half-year +1.01 / +0.22 / +0.13 / -0.01 R -- fading to nothing. "30 min after, stop 3%
above the high, target 12%" (picked seeing the test, so optimistic) +0.16 R test, last half-year -0.14 R. Not proven;
at most a PAPER candidate. Lesson vs the earlier re-sell tests: run 4's 1.8% target is what killed them.
On the 20 real picks (`out/run4_resell_real.txt`): 2 full stops. A: EVAA 11-01 -1 R (-$748 at run-4 size), ESPORTS
11-02 +9.1 R (+$5,872; held to 24 h, 0.01858 -> 0.01441); B: +1.65 R, +0.65 R. Two events -- no evidence either way.
Run 4 + re-sell A as one account (`scripts/run4_resell_account.py`, `out/run4_resell_account.txt`), from $3,184,
strict .. lenient: 20 real picks $15,498 .. $19,262 (cap on or off: it never fires there; ESPORTS re-sell +$6,073);
2026 cap ON $772 .. $2,985, cap OFF $47 .. $47 (Jan alone -$3,042 strict, 19 trades); history cap ON $43 .. $354,
OFF $40 .. $41. The 2-stops loss cap must stay.
Re-sell as a CUSHION (`out/run4_resell_cushion.txt`): it loses again on 54-80% of stop-losses (A: 69%); A's average
-0.71 R per stop-loss comes from ~1 in 5 huge wins. SKIP FILTERS at full size (`scripts/run4_skip_filter.py`, 72 rules
on fill time / rise speed / prev_change / atrp / wick / turnover, train 2025 test 2026): "improvements" in totals are
just trading less; per KEPT trade the best are -$25 .. +$44 on test, none clearly beats random skipping. CLOSED.
BANK PROFITS monthly above $3,184 (`out/run4_bank_profits.txt`): history total $652 .. $1,163 vs $43 .. $128; 2026
unchanged ($110 .. $700, it never got ahead to bank). It protects gains already made; it cannot cushion losses.

**BOT REPLAY + FOUR CODE REVIEWS (owner 2026-11-15: "run the simulation as well so that all the complicated pieces are
tested", "run code reviews as well"):** `scripts/replay_bot.py` = the REAL bot (`SpikeLive` + PostgreSQL) minute by
minute against a simulated Delta (`ReplayDelta`) on the careful dataset, compared trade by trade with the research
rule; `scripts/replay_battery.py` = the battery (needs DELTABOT_TEST_DSN; one scratch db `replay_N` per worker;
`out/careful/replay_battery.txt`). Final code: 44 clean runs, 4,441 of 4,458 trades identical (full history $1,801.05 ..
$3,889.83 = the research numbers; the 17 others = whole-number leverage on $10k-capped coins); 64 fault runs (~19.8k
faults, ~30.8k restarts) with 0 halts / 0 unprotected passes / never two positions / nothing left open. Four
`/code-review high` passes found real failure-path bugs, all fixed with tests (list: prereg "When Delta or the
network misbehaves"): break-even move marked done though it failed; extra margin forfeited by one failed read; resting
sells not cancelled on HALT; double fill unmanaged; a stop seconds after the fill booked as UNKNOWN_EXIT (entry_time is
now Delta's fill time); `fade-live-capital` built run 3's class (now `fade_live.account_class()`); base
`_reconcile`'s SENDING branch is now the overridable `_sent_unknown`. Left as is (review, low risk while the account
starts flat): run ownership is read from `detail->>'run'` (no run_version column on positions). tests/live +
live_exec: 2,910 passed.

**SPIKE SELL RULE C -- LIVE 2026-11-17 (#170, merge 1c704fb; owner "approved on C").** After the first live trade
(CROSS 2026-11-17: filled 0.154 at 05:02:51, stopped at the 2.25% stop 27 s later in the same spike, then fell below
the entry; -$291 + ~$9 fees, wallet $3,195.85 -> $2,895.60): `STOP_ABOVE` 0.10, `MAX_LEVERAGE` 5 (20% margin = ~1x
the balance, a full stop ~10% of it; liquidation ~+27% after the unchanged extra margin), `TRAIL` 0.10 with
`TRAIL_ONLY_PAST_ENTRY` (the stop moves only to a level at or below the entry). Entry, picks, 24 h, no month-off, floor
unchanged; RUN_VERSION stays 4. Evidence (`scripts/spike_verify.py`, `out/careful/spike_verify.txt`, two
implementations, 617 trades, before fees): win 59%, +8.1% / -9.2%, +1.07%/trade strict (+1.50% lenient), all four
half-years positive, random-minute control -0.09%; compounding low $897 (worst fall 89%); chosen from ~70 variants.
Same file: the live 2.25%-stop rules -0.31%/trade, positive in 1 of 4 half-years; quick 20x exits (1-2% target,
15-60 min) all negative strict. Replay of the real bot (`scripts/replay_bot.py`, now honouring the position's
leverage): 2026-08..11 68/68 identical; full history first 321 trades identical, diverging from 2026-09-21 at a ~$96k
simulated balance (coin size limits). deploy-prod run 44051329938 approved by the owner, completed ~10:00 UTC; prod
image 1c704fb verified: constants 5 / 0.10 / 0.10 / True, $2,895.60 sizes to 2,878 notional at 5x, RUNNING, nothing open.
**SUPERSEDED SAME DAY by rule C, TRAIL FROM THE FILL (#172, merge fc09838; owner "use 10% from the fill"):** only
`TRAIL_ONLY_PAST_ENTRY` False -- the 10% trail tightens from the fill, so the stop can come inside the entry before
the trade is in profit. Evidence (same file, cell "C from the fill"): win 49%, +8.25% / -6.39%, +0.77%/trade strict
(+1.26% lenient), t 1.99, half-years +0.60 / +0.53 / +1.28 / +0.69%, control -0.10%, compounding low $1,046. Real
picks 10-29..11-17 as the bot trades them (`scripts/real_picks_rule_c.py`): 13 trades, 8 wins, +$238 / -$179, +$78
per trade before fees vs +$53 for C. Replay 2026-08..11 71/71 identical. deploy-prod run 44064424605 approved by the
owner, completed 10:46 UTC; prod image fc09838 verified: constants 5 / 0.10 / 0.10 / False, RUNNING, $2,895.60,
nothing open. Judge after ~30 live trades: stop if avg win < avg loss or win rate < ~40%.
**THEN 10% MARGIN + FINISHED-MINUTE TRAIL (owner 2026-11-17 "approved on both" / "let's do it now"):**
`TRADE_MARGIN` 0.20 -> 0.10 (position ~0.5x the balance, a full 10% stop ~5% of it; extra margin still 25/49 ->
liquidation ~+27%), and `_lows_since_fill` uses FINISHED 1m bars only (the external review -- GPT, Gemini, Kimi via
OpenRouter, out/review_pack/2026-11-17-spike -- found the live trail also used the forming minute; the backtest and
the replay never did). Why 10%: `scripts/spike_high_margin_check.py` (the bot's day rules, 5,000 orderings): 20%
worst fall 61%, under $1,000 in 2%; the review's sizing said 7-10%. Replay 2026-08..11 at 10%: 71/71 identical.
Same day, tested and NOT adopted: $40/$45 take-profits, 30-60% margin, tight stops with a close target, QUICK with
$40 / max leverage / stops before liquidation, QUICK at 10-20% margin (scripts/quick_*.py, out/careful/quick_*.txt);
QUICK at 10% x 5x is ~+$5/trade after fees over history (t 1.7) and ~-$2/trade on the paper trades 10-29..11-17 once
the picks the $250 paper account skipped are included.
deploy-prod run 44167611114 (image 952e1ec) approved by the owner, completed 11:02 UTC 2026-11-18 (all jobs green);
prod verified read-only 11:58 UTC: image 952e1ec, TRADE_MARGIN 0.1, RUNNING, $2,792.50, nothing open.
**THEN THE TAKE-PROFIT LADDER -- LIVE 2026-11-18 (#190, merge 2bac016; owner "we will adopt it"):** deploy-prod run
44330469980 approved by the owner, completed 15:16 UTC; prod verified read-only: image 2bac016 running, MARGIN 0.1 /
STOP 0.1 / TRAIL 0.1 / 5x / LADDER ((0.05, 0.5), (0.1, 0.25)), `tp_legs` column present, RUNNING, $2,792.50, nothing
open. First ladder trade: the 2026-11-19 entry (no probe of the step order was run). after
MMT 2026-11-18 (+~$178 at best, closed by the trail at -$99.6) the owner asked for quick profit booking. Tested and NOT
adopted that day (all below the rule as it runs, history and 2026 alike): $20-40 take-profits at 10-50% margin
(`scripts/spike_small_usd.py`, `spike_cut_30_40.py`), a 1 h / 30 m / 2 h time cut (`spike_cut_1h.py`), split / break-even
(`spike_split_be.py`, `spike_split_breakdown.py`), a stop / time-cut search for a $40-50 target chosen on 2025 and
tested on 2026 (`cut_search.py`: no cut works; tighter is worse). ADOPTED: `LADDER = ((0.05, 0.50), (0.10, 0.25))` --
reduce-only post-only limit buys for 50% of the contracts 5% under the entry and 25% 10% under, placed right after the
fill (market if the price is already through), the rest keeps stop / trail / 24 h; left-overs cancelled at the close.
Evidence (`scripts/spike_ladder.py`, row L6, two implementations, before fees, 10% margin): history +$12.2/trade strict
(+$16.7 lenient) vs +$19.8 (+$25.2) without; win 66% vs 50%; worst run -$973 vs -$1,227; real picks 10-29..11-18
+$28.8 vs +$21.7. The owner was told it makes LESS over the history. Earlier steps (`scripts/spike_ladder_3_5.py`, owner "why not 3% 5%", 2026-11-18): 3% 50 / 5% 25 history +$8.7 strict (+$12.5 lenient), real +$22.9; 3% 25 / 5% 25 +$12.7 / real +$23.6; 3% 33 / 5% 33 +$10.5 / real +$24.2 -- none beats 5/10, kept. Code: `_ladder` (place / read the steps,
`tp_legs` JSONB), `_ladder_close` (the closed P&L counts every step), `explained` (a step's fill moves the wallet while
the position is open -- without it the capital check HALTs; a test proves it). Replay of the real bot vs the reference
with the ladder, 2026-08..11: 71/71 identical, STRICT $3,722.49 / LENIENT $4,355.32 (without the ladder $4,063.87 /
$4,890.32). The battery's sanity now checks the replay's reference against `spike_ladder.loop` (the old exit-C
research function no longer ships): 1,229 of 1,234 identical (5: a close above a just-moved trail, which the bot buys
back at that close). Replay battery (`REPLAY_QUICK=1 REPLAY_SEEDS=3`, `out/careful/replay_battery_ladder.txt`): clean runs
2,498/2,498 identical; every fault group (read timeouts, lost / refused writes incl. the new `place_tp` faults,
restarts, lookup misses, double fills) ALL SAFE, no halts. Its first run found three ladder bugs, fixed with tests:
a step sent twice when the by-id lookup missed (now `_find_leg` asks three ways, and resting steps never exceed the
ladder's share); a P&L left wrong when a read failed at the close (now a closed ladder is UNSETTLED until read --
retried each pass, `explained` waits, no new entry meanwhile); a step left resting on a flat coin when the close's
cancel failed, which then filled against the next position (now cancelled before the ladder is settled). Without the
ladder the same fault run (D2 seed 1) never halted. Real tick / contract sizes (B): whole contracts make "50%" of a few
SOL / LTC / TAO contracts differ from the backtest's exact halves (2 such differences exist without the ladder too).
New order shape on real money: a probe of the step order is advisable before the first live trade.
Also tested 2026-11-17 and NOT adopted: QUICK with other exits (`scripts/quick_exits.py`: as-is +0.024R, t 1.95, the
best; tighter stops worse) and QUICK at 50% margin + break-even lock + 1% target (`scripts/quick_margin50.py`: 5-20x
bust by liquidation, 2-3x survive with 66-83% falls).

**SPIKE SELL -- MERGED 2026-11-16 (#168, merge e393c80; owner "Commit push and merge then deploy all approved").
Rules as merged: 20% margin (+ 25/49 of it as extra margin), +2.25% stop, plain 5% trail from the fill (no
break-even step), no take-profit, 24 h exit, NO month-off rule, $1,000 floor. deploy-prod run 43935278199 dispatched
19:03 UTC, approved by the owner, COMPLETED 19:30 UTC: the bot host runs image e393c80 (healthy); run 4 NEW_RUN
19:29:49 UTC, RUNNING, wallet $3,195.85, no open positions. **LIVE ON REAL MONEY from the next 00:00 UTC entry.**
PROBE PASS 2026-11-16 ~19:45 UTC (owner "Yes"; run as `docker exec -e DELTABOT_EQUITY=3184 deltabot python -m live
fade-live-probe XRPUSD` because the probe's wallet check reads the host's DELTABOT_EQUITY=1500 and would refuse
$3,196 -- the bot's own run-start check uses RUN_START_USD=3184): resting post-only sell accepted and cancelled;
1 XRPUSD sold at 0.9566 with a LAST-TRADED stop leg; stop moved 1.148 -> 1.1958 and read back; $1 isolated margin
added (liquidation 1.43 -> 2.43; isolated: YES); bought back at 0.9567, nothing left open; wallet $3,195.85; state
still RUNNING afterwards. Open item: the probe should take max(DELTABOT_EQUITY, RUN_START_USD) like the bot. Backtest of these exact rules: the $1,000 floor
in April 2025 (see the bullets above and the prereg).** The paragraph below is the 2026-11-15 version (10%, exit C,
month-off 2), kept for the record.

**(SUPERSEDED 2026-11-16) SPIKE SELL AS IT WILL RUN (owner 2026-11-15: "I suggest we deploy with 10% margin", exit "C approved") -- BUILT, NOT
DEPLOYED:** `live/spike_live.py`: TRADE_MARGIN 0.10, EXTRA_MARGIN 0.051, NO take-profit order (target_price NULL),
stop +2.25% LTP; once a 1m low nets +$30.61 x balance/$3,184 (`BREAKEVEN_AFTER_USD` = 150 x 10/49, ~0.6% fall) the
stop moves to break-even (`LOCK_USD` 0; lock_price column / LOCK_* events keep their names), then `TRAIL` 5% above the
lowest low; 24 h exit; month off after 2 full stops (not after the move); $1,000 floor. Tested numbers and the
replaced first version: `docs/fade_live_spike_prereg.md`. (PG tests need
DELTABOT_TEST_DSN -> the local `deltabt-testpg` container on network dtnet). Go-live is the owner's: merge PR #168,
deploy, then the probe (owner's yes) must PASS. The text below describes the FIRST version (49%, $500 target).

**SPIKE SELL (run 4) FIRST VERSION, BUILT 2026-11-15, REPLACED BEFORE ANY ORDER:** (name: owner 2026-11-15, "Spike Sell"; run number stays 4) `live/spike_live.py` (rules `docs/fade_live_spike_prereg.md`) replaces rule A for the real account via `fade_live.from_env`; paper accounts unchanged. Owner-approved additions: last-traded stop trigger (`bracket_stop_trigger_method` now also sent), 25% extra isolated margin after the fill, STOP_FILL logging. New client calls `edit_stop_price` (PUT /v2/orders) and `add_position_margin` (POST /v2/positions/change_margin, never resent). `RUN_START_USD = 3184` (host DELTABOT_EQUITY still 1500). Option A: a 2.5 s watcher tops up the margin right after a fill (shared lock with the 30 s pass; once per position). As built: history -$71.5 (strict) .. -$33.8 (lenient) per trade, 3/604 liquidations strict; 20 real picks +$1,494 .. +$2,196 (`out/run4_final_numbers.txt`). Target/lock now SCALE with the balance (owner: "Scale can be kept, cap is no"); 2026 from $3,184: $847 .. $2,125; 82 trades of 482 picks (342 skipped by the hard stop). Trail 0.8% after the lock added (bot-moved, not Delta's native trail); 20 real picks with the final rules as an account: $3,184 -> $2,589 (strict) .. $3,307 (lenient), Nov stopped after EVAA + ESPORTS (`out/run4_final_rules_real_trades.txt`). Go-live: merge, deploy, then the probe (`fade-live-probe`, owner's yes) must PASS. The real account is HALTED (manual SOPH trade 2026-11-14, -$609.26, wallet $3,184.13 after a deposit); run 4's first pass begins a new run from the wallet.

**20x SPIKE VERSION — CHOSEN BY THE OWNER 2026-11-15 FOR THE NEXT REAL BOT; numbers verified twice.** Rule: rule A's
two picks; a resting limit sell 5% above the 00:03 close (valid to 24:00, fills when a 1m high trades 0.05% through);
50% of the balance as isolated margin at 20x (Delta per-coin limits: 20x only up to max_leverage_notional, $50k on most,
$10k ESPORTS/PIEVERSE; contract limits; `out/delta_margin/product_limits_2026-11-15.json`); take-profit netting +$500;
stop +2.25% (20x liquidation = entry x (1 + IM - MM) = +2.5%, matches Delta-reported liquidation prices); when a 1m low
first reaches -1% the stop moves to the price netting +$200 (if the close is already above it the stop fires at once,
at the close); 24 h; off for the month after 2 full stops; one position at a time. **Fill model (all final numbers):**
entry maker 0.02% + 0.05% slip; exit taker 0.05% (a real SOPH stop paid 0.053% = 0.045% + 18% GST); a triggered stop
fills at the worse of its price and the minute's close +0.1% (STRICT) -- the LENIENT bound (stop price +0.1%) is the
optimistic edge; a stop fill past liquidation loses the margin. Code: `scripts/clean_compare.py` (`short`, `account`,
`checks()` asserts the known facts) and `scripts/verify_independent.py`, an independent re-implementation: **604/604
history fills and 14/14 real fills identical** (`out/verify_independent.txt`). Results, start $3,184: 20 real picks
10-29..11-14 +$1,463 (strict) .. +$2,172 (lenient) per-trade sum, account with the hard stop $2,452..$3,013; full Delta
history 2025-01..2026-10 **-$127 (strict) .. -$37 (lenient) per trade**, account $31..$262; 57 of 604 fills are
liquidations under STRICT (a 1m jump past +2.5%). The owner decided to build it knowing this. Earlier 20x tables in
this session (before `clean_compare`) used a stop fill at the stop price, a backwards lock condition and a 2.62%
liquidation -- all superseded. IDEAS LOGGED, not adopted (all on the same model, `out/*`): extra isolated margin after
the fill removes liquidations (25% extra: -$75..-$37/trade; `avoid_liquidation.txt`, `fill_bounds.txt`); tighter stops
0.5-1.5% and wider 3-5% (+extra margin) both worse (`tight_sl_grid.txt`, `tight_sl_real.txt`, `wider_stop.txt`); 75%
margin worse at every target (`margin_target_grid.txt`); lock fixes A +$100 lock / B move after 1.5% / C resting 40%
take at -1% then breakeven (C best: -$113..-$27; `lock_fix.txt`); fixed-$ targets $200-1,000, weekly/monthly
win-streak and loss-streak hard stops, entry at 01:00-08:00, sell-the-spike 1-8% (`quick_20x_*.txt`). The steady
version (filter + 3x + 10% margin + take 10%) on the same model: +$11/trade, 19/22 green months, $3,184 -> $8,336
(`clean_compare.txt`).

**OWNER PACKAGE (2026-11-13, owner "you have pushed back alot, without finding solution to my needs").**
`scripts/owner_package.py` -> `out/owner_package.txt`, Delta picks, real fills, liquidation >= 1.2x stop away. Funding
>= 0.05% filter + hold 24 h at 10% risk: +$9.94/trade, $1,640 -> $4,859, worst drop 42% (all picks today at 5%:
+$0.73, $2,384, drop 78%). With 2/3 at +0.2R: 65% win, +$6.19/trade, $3,646, drop 44%. 324 trades / 15 a month.
Caveat: threshold chosen after the Binance/Delta looks, t 1.45 on Delta.
Owner "if we reach more than 1R we cut the trade" (`--target1r` -> `out/owner_target1r.txt`): with the funding filter,
close at +1R: +0.041R, 56% win, 10% risk +$4.32/trade, $3,039, drop 41% (only 16% of trades reach +1R, ~19% fall in
24 h). +0.5R target did best in dollars: +0.053R, 63% win, 10% risk +$12.73/trade, $5,766, drop 46%; hold 24 h $4,859.
Owner "max leverage and 50% capital per trade" (`--maxlev` -> `out/owner_maxlev.txt`, isolated margin 50% of balance,
Delta max leverage ~20x, funding filter, +0.5R): 256 of 324 trades liquidated, $0 by 2025-03-20. 10x: $0 by 2025-04;
5x: $0-907; 3x: drop 98%. Liquidation sits ~2.5% away at 20x; these coins move that within hours.
Later 2026-11-13 owner questions (results in `out/`, made by inline runs on the same Delta picks, funding filter,
real fills, from $1,640): **Option B with the bot's real leverage rule** (whole leverage, mm 2.5%, from the worst fill)
15% margin x max safe lev: ~3x, $5,342, drop 41% (`option_b_live_rule.txt`). **30% margin x max leverage keeping
today's liq >= 2x stop** (~2x): $5,603, drop 47%; liq >= 1.2x (~3x): $7,774, drop 74% (`today_maxlev_filter.txt`).
**10x** at 5-30% margin: $0-806 (`lev10_filter.txt`); 10x stop 6% / take 3-10%: best break-even $1,645
(`lev10_take_grid.txt`). **Cut +0.5-3% / take 1-3% at 10x/20x**: all $0-31 (`scalp_filter_lev.txt`). **Dollar exits**
(take +$800-1,000, cut -$200): all lose (`dollar_exits.txt`). Filtered coins' 24 h moves: median fall 8.9%, median
rise 7.9%, rise >= 2.5% before the low on 64% of days (`filtered_moves.txt`). **Monthly cap** $2-3k never triggers
(best month +$1,394); +20-50% caps end $6.5-6.7k, likely luck (`monthly_cap.txt`). Owner's hand-trade record (commit
e09f637): 165 clean trades +$2,297, 44 liquidations -$9,172. Owner decision: **keep the real bot running as is**;
Option B (filter + 0.5R + 30% margin x max safe lev, liq >= 2x stop) is the upgrade on offer, not built.

**LADDER WIN RATE vs MONEY, PARTIAL EXITS (2026-11-13, owner "need to improve ladders win rate, we should win mostly").**
`fade_profit_ladder.py --winmap`, `out/fade_win_rate_map.txt`. Ladder 10/5/3, real-bot fills, Delta picks. Close X% at
+0.2R: 0% win 55% $2,610 drop 66%; 25% 59% $2,250; 50% 63% $2,875 drop 57%; 66% 66% $2,734 drop 52%; 75% 68% $2,486
drop 49%; 100% (QUICK) 76% $1,737. At +0.3R: 58-68%, $2,007-2,334. Each step up in win rate lowers R per trade
(+0.021 -> +0.010); the dollar ends move with path luck. 66-75% at +0.2R: win 66-68%, drop ~50%, under $1,000 only in
Sept 2025.

**SKIP-NEGATIVE-FUNDING FILTER, RE-CHECKED ON LADDER SIZING + REAL TRADES — NOT ADOPTED (2026-11-13).**
`scripts/fade_funding_filter.py`, `out/fade_funding_filter.txt`, real/paper trades in `out/fade_real_trades_2026-11-13.json`.
Backtest (1,022 Delta picks, real-bot fills): keeps 46%; rule A +0.021 -> +0.038R (t 1.16) but it SWAPS years: 2025
−0.017 -> +0.050, 2026 +0.067 -> +0.027 (the skipped picks made +0.114R in 2026). Ladder $2,610 -> $2,159, drop 66% ->
48%, never under $1,000; fixed 5% $3,035 (drop 41%). QUICK: no gain (+0.010 -> +0.011R). Live 10-29..11-13 (2026-like):
paper A kept +0.028R vs skipped +0.227R; QUICK kept +0.035 vs skipped +0.128; real kept −$26.44 vs skipped +$40.79
(ARIA 11-12 at −0.018% funding). Smoother because it trades half as often, not because it picks better.

**QUICK AT A FIXED $300 RISK (2026-11-13, owner).** `fade_optimal_size.py --qusd`, `out/fade_quick_usd.txt`: +$2.94 a
trade, win 76% (avg +$54), losses avg −$158, 84 full stops; from $1,640 broke 2025-04-12; unlimited money +$3,003 with a
$3,223 fall first (2025 −$123, 2026 +$3,127); needs ~$4,000+.

**FIXED $300 RISK / $600 TARGET (2026-11-13, owner).** `fade_optimal_size.py --usd / --usdcap`, `out/fade_fixed_usd.txt`.
Delta picks, real-bot fills, from $1,640, no compounding. 2x-ATR stop: target hit 14 of 1,022 (1%), stops 159, 24 h
exits 849; +$4.74 a trade; broke 2025-04-11. 1x ATR: −$15.04 a trade, broke 2025-01-17; 0.5x ATR (3.8x the wallet in
notional): −$47.95, broke 2025-03-22. With unlimited money the 2x-ATR plan ends +$4,848 but falls $7,838 first
(2025 −$3,220, 2026 +$8,069): needs ~$8,000+ to survive at $300 a trade.

**MORE RISK + MORE LEVERAGE + 1:2 ON THE LADDER (2026-11-13, owner).** `fade_optimal_size.py --r12`,
`out/fade_more_risk_12.txt`. Delta picks, real-bot fills, 50% margin a day, risk 5-20%, up to 3x / 5x. 1:2 on the 2x-ATR
stop (+0.016R): 5% $1,887 (3x) / $1,205 (5x, 124 liquidated); 10% $525 / $226; 15% $17 / $5; 20% $0. 1:2 on 1x ATR
(−0.050R): $23 at 5%, $0 from 10%. Every cell under $1,000 in 2025.

**BANK BIG OPEN PROFITS — WORSE (2026-11-13, owner "by holding 24h we lost 200$ profit and closed at 40$": real ARIA
11-12, open +$194 at 14:00 UTC, closed +$39.55 / +0.27R).** `fade_profit_ladder.py --bank`, `out/fade_bank_big.txt`. Same
1,022 Delta picks, real-bot fills: hold +0.021R (ladder $2,610); close all at +0.5R +0.014R ($1,810), +0.75R +0.004R
($1,035), +1R +0.012R ($1,645, reached by ~10%), +1.5R +0.013R ($1,559); trail from +1R 0.5R/0.3R behind +0.009/+0.005R
($1,433 / $1,029). Trades that reach +1R usually keep falling; capping them costs more than days like ARIA give back.

**LADDER AT 5x AND 15% RISK (2026-11-12, owner).** `fade_ladder_5x.py --r15`, `out/fade_ladder_5x_r15.txt`. Delta picks,
$1,640: fixed 15% -> $181 (drop 99%, worst month −83%, under $1,000 on 2025-01-21, 135 liquidations); ladder
15/7.5/4.5% -> $1,904 (drop 77%, 2025 end $631). 3.4x full Kelly (4.4%).

**OPTIMAL RISK PER TRADE BY EXIT STYLE (2026-11-12, owner "ready to take more risk, get me optimal R&R, position size").**
`scripts/fade_optimal_size.py`, `out/fade_optimal_size.txt`. Delta picks 2025-01..2026-10, real-bot fills, risk 2-15%
(liquidation >= 2x the stop, <= 3x, 25% margin a pick). Kelly (mean/var of R): rule A 4.4% (2025 negative, 2026 11%),
QUICK 7.5%, half-at-0.2R 7.0%. Growth peaks: A at 4% $2,479 (drop 69%), QUICK at 8% $2,477 (61%), HALF at 8% $2,774
(79%); beyond ~8% every style earns LESS. QUICK at 4% $2,171, drop 36%, never under $1,000 (5%: $2,243, 44%).
Today's 10% ladder on rule A is past full Kelly (4.4%).

**LADDER AND QUICK UNCHANGED AT 50% MARGIN, 1-5x (2026-11-12, owner "5% with 50% capital ... as they are today", read as
5x).** `fade_profit_ladder.py --today`, `out/fade_profit_ladder_today.txt`. Rule A: 5x $0 (2025-09-07, 12% liquidated),
3x $61, 2x $1,043, 1x $2,784 (drop 74%). QUICK: 5x $0 (2026-03-11), 3x $366, 2x $1,333, 1x $2,083 (drop 56%). All
cells go under $1,000.

**SAME AT 30% MARGIN (2026-11-12, owner "20% at 30% capital", read as 20x).** `fade_profit_ladder.py --lev30`,
`out/fade_profit_ladder_lev30.txt`: 20x 78% liquidated, $0 2025-03-23; 10x $0 2025-05-30; 5x $67; 3x $2,236 (drop 84%);
2x $2,769 (68%); 1x $2,524, drop 41%, never under $1,000 (the only cell that is).

**HALF AT +0.2R + 50% OF CAPITAL AS MARGIN + FULL LEVERAGE — RUIN (2026-11-12).** `fade_profit_ladder.py --lev`,
`out/fade_profit_ladder_lev.txt`. Same 1,022 Delta picks: full leverage liquidates 81% of trades, $0 by 2025-02-15;
10x: 46% liquidated, $0 2025-03-23; 5x: 12%, $0 2026-01-22; 3x: $428 (drop 99%); 2x: $1,795 (87%); 1x: $2,790 (60%,
under $1,000 2025-09-17). 1x at 50% margin ~ 10% risk on a 20% stop: already as large as the ladder.

**PROFIT-SIDE LADDERS AND TRAILING STOPS ON THE LADDER'S PICKS (2026-11-12, owner "why not add ladder to profits as well?",
"or trailing SL?").** `scripts/fade_profit_ladder.py`, `out/fade_profit_ladder.txt`. Same 1,022 Delta picks, real-bot
fills; today +0.021R, ladder $1,640 -> $2,610 (drop 66%). Raising risk when ahead (+12.5/15%, 3->7.5%, 5->10%): $2,050-
2,289, none better, none avoids the 2025 floor. Scale-out: 1/2 at 0.2R +0.015R but ladder $2,875 drop 57% (smoother,
not more per trade; 3%: $2,345 drop 42%); other plans $1,677-2,632. Trailing (0.25-1R behind, from 0-1R): −0.001..+0.011R,
ladder $1,152-1,433 (all below today). Win rate rises to 60-69%, average win falls. Nothing avoids the 2025 floor.

**1:2 ON THE LADDER'S OWN PICKS, OUTCOME COUNTS (2026-11-12).** `scripts/fade_rr12.py`, `out/fade_rr12.txt`. Delta picks
2025-01..2026-10, 1,022 trades, real-bot fills. Today (2x ATR, no target): stop 16%, 24 h in profit 55% (+0.47R), 24 h
at a loss 30% (−0.23R); +0.021R; ladder $1,640 -> $2,610, floor hit 2025-04-12. 1:2 with the same stop: target hit 1%,
+0.016R, ladder −2%. 1:2 at 1x ATR: stops 36%, targets 8%, −0.050R, −90%. 1:2 at 0.5x ATR: stops 58%, targets 21%,
−0.160R, −99%.

**DECISION 2026-11-12 (owner): "we will stick to quick and ladder".** The exit / leverage / R:R / new-family research
of 2026-11-12 (entries below) is closed; no strategy change. Real account stays rule A ladder 10/5/3 (run 3); QUICK
stays as it is. Open option the owner has not taken: real account to QUICK at 3% fixed (fade_sizing_options).

**R:R >= 2 SEARCH, SIX FAMILIES ON 20 LARGE COINS — NONE PASSES (2026-11-12, owner "more ideas with better R&R, less risk,
good win rate and good returns").** `scripts/rr_search.py`, `out/rr_search.txt`. Picked on 2021-23, judged on 2024-26
(pass: mean > 0, t >= 2, every test year > 0). Dead everywhere: trend pullback (−0.014..−0.040R), dip-buying below the
lower band (−0.12..−0.23R, t to −30), Asia-range breakout (−0.07R), NR7 (−0.08R). Trend-following lives but fades:
4h breakout in BTC's direction 4R +0.162R t 4.34 all years (TEST +0.055 t 1.07, 2025 −0.009); DAILY 20-day breakout,
2x ATR, 4R +0.177R t 4.00, win 39% (TEST +0.101 t 1.65, 2025 −0.054) — account 0.5% risk, max 5 open: $1,640 -> $2,729,
worst drop 13%, but 2024 +10%, 2025 −0%, 2026 +1%. Fresh coins (ICP HBAR VET ALGO SAND MANA AXS EGLD THETA XTZ,
downloaded) stay unused: nothing passed the bar.

**TREND BREAKOUTS ON LARGE COINS, R:R 4-5 (2026-11-12, owner "option 2") — FIRST OUT-OF-SAMPLE PASS, BUT FADING AND
DEEP DRAWDOWNS.** `scripts/trend_data.py` (Binance 1h 2021-01..2026-10, out/trend, not committed), `scripts/trend_rr.py`
(main / --robust / --holdout / --money), `out/trend_rr*.txt`. 18 cells on BTC ETH SOL BNB XRP DOGE ADA AVAX LINK LTC
(next-1h-open entries, stop-first in an hour, 0.05% fee + 0.02% slip a side, longs pay 0.03%/day): the pre-set rule
(best DEV cell per family, judged 2024-26) fails all 3 families. Seen AFTER the run: 4h breakout, 2x-ATR stop, fixed
4R/5R target positive in 5-6 of 6 years (N55/5R +0.151R t 2.84, 6/6; N20/4R +0.112R t 2.89, 5/6; both sides, most
coins, survives double slippage). Fixed before a HOLD-OUT run on DOT TRX ATOM NEAR UNI FIL BCH ETC AAVE XLM: N55/5R
+0.072R t 1.40 (fail); N20/4R +0.087R t 2.24, 5/6 years, 9/10 coins (PASS). All 20 coins N20/4R: +0.099R t 3.63, 945
trades a year, win 25%; $1,640 at 0.25% risk -> $6,014 but drop 42% and 2025 −8%, 2026 −2%; at 1% -> drop 89%.
Breakouts cluster across coins, so losing streaks are long (42-63 trades). Best years 2021-24; recent two flat/negative.

**REWARD:RISK 4:1 / 5:1 — ALL NEGATIVE (2026-11-12, owner "risk 10 to make 40 or 50").** `fade_reward_risk.py --wide`,
`out/fade_reward_risk_wide.txt`. Stops 2/3/5/8% and 0.5x/1x ATR, targets 4x/5x, out at 24/48/72 h, realistic fills: 36
rows, every one negative over 2021-26 (−0.04..−0.34R). A 2% stop is hit first on 80-84% of picks (target 10-17%);
wide stops reach a 4-5x target on 0-10%. Pumped coins swing several % against the short before any 4-5x fall.

**ONE PICK A DAY INSTEAD OF TWO — WORSE (2026-11-12, owner "2 trades per day with quick? or 1").** `fade_sizing_options.py
--top1`, `out/fade_sizing_options_top1.txt`: 599 trades. QUICK real fills −0.003R (two picks: +0.009R); QUICK 3% fixed
$1,490 real / $2,193 ideal vs $2,045 / $3,707 with two. The second pick carries as much of the result as the first.

**SIZING OPTIONS FOR THE REAL ACCOUNT, RULE A vs QUICK, DELTA PICKS 2025-01..2026-10 (2026-11-12, owner "major problems
around risk reward and position sizing").** `scripts/fade_sizing_options.py`, `out/fade_sizing_options.txt`. 1,022
trades, live sizing (lever_plan), from $1,640. Per trade: A +0.033R ideal / +0.020R real fills (00:03 entry, 0.1% through,
0.1% spread); QUICK +0.029R / +0.009R (win 76-78%, avg win +0.19R, loss −0.52R). Live ladder 10/5/3 on A: $4,889 ideal /
$1,683 real, both under the $1,000 floor in 2025 (07-13 / 04-12). QUICK fixed 3%: $3,707 / $2,045, floor never, drop 25%
/ 29%, green 16 / 13 of 22 months; QUICK 5%: $5,993 / $2,214, drop 39% / 45%. The real account's 7 matched trades
(+0.003R vs paper, entry slippage in its favour) suggest real fills sit between the two columns.

**FILTERS FOR A TIGHT STOP + 2:1 TARGET — NONE SURVIVES OUT OF SAMPLE (2026-11-12, owner "filters where we trade less
... keep the win rate and R:R").** `scripts/fade_rr_filters.py`, `out/fade_rr_filters.txt`. Stops 1x ATR / 5% / 8%,
target 2x, 24 h, realistic fills; 11 pre-entry features (pump, wick, 3-day change, volume surge, 7-day breakout, ATR,
BTC, funding, OI change, long/short accounts), thirds and the choice from 2021-23 only, one look at 2024-26. Best on
DEV: long/short account ratio high third (+0.131R, 1x ATR) -> TEST +0.025R t 0.57, 2024 negative; its pair with BTC
mid third (8% stop) DEV +0.192 -> TEST +0.065R t 0.82, 2024 −0.007. 27 rows, 0 pass; most DEV winners turn
negative on TEST (pump mid third +0.017 -> −0.098R).

**2:1 / 3:1 VIA ENTRY TIMING (watchlist + trigger + structure stop) — FAIL (2026-11-12, owner "ok with taking less
trades ... atleast 1:2").** `scripts/fade_rr_entries.py`, `out/fade_rr_entries.txt`. Rule A picks as a watchlist;
short only on a 15m trigger (breakdown of the first 1/2/4 h low; failed push +3/5/10% back below the open; lower
high), stop just above the day's high (1-15%), target 2x/3x, 24 h, realistic fills. 14 rows: 12 negative (breakdowns
−0.06..−0.12R, t −2.5..−5.2). Best: failed push +10% (43 trades a year, stop 12.7%): +0.022/+0.026R, t 0.4, 3/6
years — noise. Lower high 3:1 −0.019R, 4/6 years. Waiting for weakness enters late: the day's fade is mostly done.

**REWARD:RISK 1:1 / 2:1 / 3:1 — ALL NEGATIVE (2026-11-12, owner "we risk more than we make").**
`scripts/fade_reward_risk.py`, `out/fade_reward_risk.txt`. 3,608 Binance picks, realistic fills, stops 2x/1x ATR and
fixed 5/10%, targets 1/2/3x the stop, out at 24/48/72 h (48 rows): every row's mean R is negative over 2021-26; none
positive in more than 3 of 6 years. Rule A's own realised ratio (no target, 24 h): avg win +0.47R, avg loss −0.58R,
win 55%. A 2:1/3:1 target on the 2x-ATR stop is a 39-58% move: hit by 0-4% of trades. Tighter stops (5%) are hit 50-74%
of the time. Wins exceed the break-even rate only where the payoff is < 1:1.

**"SENSE THE FLIP" EXITS — ALL FAIL (2026-11-12, owner "instead of hard SL, cut with some logic if we sense the
position is going to flip").** `scripts/fade_flip_exits.py`, `out/fade_flip_exits.txt`. 3,608 Binance picks; signals
on 15m closes, exit next minute + 0.05%: new 1/2/4 h high above entry, 15m close above the 4 h mean by 0.25/0.5 ATR,
three rising 1 h lows, BTC +1% with the coin above entry, adverse at 1 h. On top of the 2x-ATR stop: all 8 worse
(−0.002..−0.015R, z −0.5..−2.0); instead of it (3x-ATR disaster stop): all 8 worse. Why: 90% of trades trade above
entry within 4 h and 87% of WINNERS do; 48% of winners rise 0.25 ATR against us first. Only a >= 1 ATR rise separates
(24% end winners) and the 2x stop is already near it.

**1:3 (TARGET = STOP / 3), ATR STOP, MAX SAFE LEVERAGE, 30% MARGIN PER PICK — WIN RATE BELOW BREAK-EVEN EVERYWHERE
(2026-11-12, owner "if win rate is more we make money right").** `scripts/fade_tight_stops.py --r13`,
`out/fade_ratio13.txt`. Stop = c x ATR14% (c 0.05..2), leverage up to 20x with liquidation >= 2x the stop. Realistic
fills: every cell -> $0; win 62-74% vs break-even 78-90% (costs ~0.2% a round trip eat a 0.3-0.8% target). Ideal
fills: best c 0.5 (stop 4.9%, target 1.6%, 8x) win 78% vs 77% break-even, still $1. A 1:3 needs > 75% wins plus costs.

**LADDER AT A CONSTANT 5x (2026-11-12, owner).** `scripts/fade_ladder_5x.py`, `out/fade_ladder_5x.txt`. Delta rule A
picks 2025-01-15..2026-10-27 (1,022 trades, +0.033R after funding), ladder risk 10/5/3%, from $1,500. 5x liquidates at
17.5% above entry: 13% of trades get there before exiting (89% of those would have lost anyway). NOW (live sizing,
1-3x, 60% notional cap): $4,471, 2025 −40% then 2026 +400%, drop 67%, below the $1,000 floor 2025-04-12. 5x, same risk
(cap 150% notional): $2,330, drop 63%, 135 liquidations. 5x with 30% margin every pick (150% notional): $0 by
2025-09. Constant 5x is worse than the live sizing in both versions.

**$300-400 TARGET VIA LEVERAGE ("more contracts, target reaches quickly") — RUIN (2026-11-12).** $1,640 wallet, 25%
margin, leverage 5/10/20x, stop = widest keeping liquidation 2x away ((1/L - mm)/2: 8.75/3.75/1.25%), target = the move
that earns $100-400 (4.9-19.5% at 5x, 1.2-4.9% at 20x). Six years (`fade_tight_stops.py --usd`,
`out/fade_dollar_target.txt`): all 24 cells -> $0, ideal fills too. Paper's 30 picks on Delta (`fade_quick_full_replay.py
--usd`, `out/fade_dollar_target_replay.txt`): 20x loses $1,819-2,477 in two weeks (stopped 16-22 of 30); 5x/10x mixed
(−$929..+$777). Leverage shrinks the allowed stop faster than the target move.

**TIGHT FIXED STOPS + SAFE LEVERAGE (liquidation >= 2x the stop), 25% PER PICK — PAPER PICKS LOOK OK, SIX YEARS
RUIN (2026-11-12, owner "we can use tight SL? avoid liquidations").** Paper QUICK's 30 picks on Delta 1m + MARK
(`fade_quick_full_replay.py --tight`, `out/fade_quick_tight_replay.txt`, entry at the 00:00 open): stop 1% $858,
2% $1,058-1,211, 4% $1,254-1,620, 8% at 5x $1,779-1,999. Six years (`fade_tight_stops.py --safe25`,
`out/fade_tight_safe25.txt`): realistic fills -> $0 in all 8 cells (mean per trade negative in every year but one
cell-year); ideal fills: only 8% stop / 1.6% target survives ($4,818, drop 97%). Even with the 2x buffer, 0.2-4.9%
of picks gap past liquidation inside one minute (Binance trade highs; Delta's mark is smoother: 1-2 of 30 on paper).

**QUICK AS IS, FULL LEVERAGE, 25% OF CAPITAL PER PICK — RUIN, ON PAPER'S OWN TRADES TOO (2026-11-12).**
`scripts/fade_quick_full_replay.py` (paper QUICK's 30 picks 10-29..11-12, taken + skipped, replayed on Delta's own
1m trade + MARK candles; `out/fade_quick_full_replay.txt`) and `scripts/fade_quick_full25.py` (3,608 Binance picks
2021-26; `out/fade_quick_full25.txt`). Replay at Delta's max (20x mostly; liquidation 2.5% vs stops 16-55%):
$1,500 -> $2,973 after two days, then 16 of the next 26 picks liquidated -> $87 (−94%), although paper won 19 of 23.
Same picks at the leverage the bot allows (liquidation >= 2x the stop): $1,719 (+15%), worst drop 9% (skips 7 picks).
Six years: full/10x/5x reach $0 in 2021 (liquidated 61/31/10% of picks); 1x ideal $1,088, realistic $59.

**FLEXIBLE RISK + SAFE FULL LEVERAGE + TARGET % SWEEP — NOTHING POSITIVE (2026-11-12, owner "you would avoid
liquidations right? ... flexible risk per trade ... full leverage and 50% capital", "test diffrent target %").**
`scripts/fade_flex_risk.py` (`--targets`), `out/fade_flex_risk.txt`, `out/fade_flex_targets.txt`. Realistic fills
(00:03 entry, 0.1% trade-through, 0.1% spread, 0.05% fee); leverage = the most whose liquidation sits >= 2x the stop
away (as lever_plan), 50% of capital as margin -> 0 liquidations with the 2x-ATR stop (a few 1m gaps past
liquidation with 2-4% stops). Rule A itself is −0.006R over 2021-26 under these fills (2025 +0.011, 2026 +0.058).
Risk rules fixed 2/5/10%, ladder, up-after-win, up-after-loss, half-Kelly from the last 200 trades: every cell below
$1,500 (best $817, adaptive, 2x-ATR, no target). Targets 1/2/3/5/8/10/15/20/30% x stops 2x/1x ATR and fixed
4/8/12%: 50 cells, mean R negative over 6 years in all 50; best 2% target on 2x ATR (−0.006R, 86% win, $911 at 2%).
Sizing cannot turn a ~0 or negative trade positive; the win rate climbs to 92% while R per trade stays negative.

**TIGHT 2%/4% STOPS AT FULL LEVERAGE — EVERY CELL GOES TO ~$0 (2026-11-12, owner "full leverage and big position
and stop at 2%, 4%").** `scripts/fade_tight_stops.py`, `out/fade_tight_stops.txt`. Same picks; half the balance as
margin per pick; targets 0.2/0.5/1/2x the stop; 2x/5x/10x/full. Realistic fills (00:03 entry, 0.1% through, 0.1%
spread): all 32 cells end at $0, mean per trade negative in every one; even ideal fills: best $501 (4% stop, 0.8%
target, 2x). A 2% stop is hit before a 2% target on 52% of picks (pumped coins are noisy), costs are ~10% of a 2%
move, and at 20x liquidation (2.5%) sits INSIDE a 4% stop, so 29-75% of trades lose the whole margin.

**QUICK AT FULL LEVERAGE + COMBINED STOPS — DEAD; THE ONE BIG CELL IS A FIRST-MINUTES ARTIFACT (2026-11-12).**
`scripts/fade_quick_leverage.py` (`--robust`), `out/fade_quick_leverage*.txt`. Same 3,608 Binance picks; half the
balance as margin per pick; per-coin max leverage from Delta's /v2/products (`out/delta_margin/`): 20x for 92% of
picks, liquidation 2.5% away vs a median 19.5% stop. 72 exit combos (stop 2x/1x ATR/none x target QUICK/2%/5%/none x
stuck 4h-3% on/off) x 1/2/3/5/10x/full: at 5x and above every cell ends at $0; at 3x 0 of 24 end above $1,500.
Standout: 2% target, 2x-ATR stop, 1x: $1,500 -> $66,820 (5/6 years) — entering at the 00:03 close instead of the
00:00 open: $573; + target must trade 0.2% through: $29; + 0.1% spread: $5. Same for targets 1-4% at 1x/2x. The
edge is the first minutes' wick below the open, which the real bot (enters ~00:03 at the bid) cannot get. Lesson:
any small-target result must be re-run from the real entry minute with trade-through fills.

**OWNER'S EXIT + SIZE IDEAS — ALL FAIL (2026-11-12, "more leverage", "close it if coin moves certain % in favour",
"cut the position if the price is stuck ... in a band").** `scripts/fade_owner_exits.py`, `out/fade_owner_exits.txt`.
Binance rule A picks 2021-26 (3,608), paired vs the 24 h exit (+0.011R). Pass = better in >= 5/6 years and paired
z >= 2. Take profit at 2/3/5/8/10/15/20% or 0.2/0.5/1.0x the stop: best +0.006R (2%), z <= 0.6, all fail. Stuck in a
2/3/5% band for 2/3/4/6 h, always / only in profit / only in loss (36 cells): best +0.006R z 1.48 (4 h, 3%, in
profit, 5/6 years) — fails z, and is the best of 36. SIZE: the same trades compounded with no notional cap:
3% risk $1,500 -> $2,146 (drop 81%), 5% -> $1,068 (95%), 10% -> $7, 15%+ -> $0; QUICK 10% -> $364 (99%). More
leverage only magnifies a near-zero edge; at 10% the six-year path is ruin (run 3's $1,000 floor would stop it first).

**FUNDING BACKTEST TIMING FIX + REVIEWERS' IDEAS 1/3/5 — ALL FAIL (2026-11-11).** `scripts/funding_ideas.py`,
`out/funding_ideas.txt`. Delta daily candles are stamped at their open, so `C.loc[t]` is the price at t+1 00:00 (the
entry), but `backtest_survivors.portfolio()` booked funding for days t..t2-1 — one day early, and that day also sat in
the score's 3-day window. Fixed to t+1..t2. Every 3 days, fifths: +0.639% → +0.551%/period, t 2.48 → 2.12 (2025
t 1.02, 2026 t 1.88), $4,868 → $4,058. Rule A's funding (`f_day`, `f_prev`) was already timed right. Ideas vs the
fixed baseline (pass = better in 2025 AND 2026 and paired t ≥ 2): squeeze filter 25/35/50% all worse both years;
median score worse; inverse-vol weights +0.03% 2025 / −0.18% 2026; keep-while-in-35% −0.16/−0.53 (t −2.7: funding
rank goes stale fast, rotation is the edge); + skip-if-edge<2×cost still worse. Older exploratory scripts
(explore_delta_basis.py etc.) may carry the same one-day shift — re-check before quoting them.

**ONE BACKTEST OF EVERY SURVIVOR (owner 2026-11-11 "run backtest on all of these").** `scripts/backtest_survivors.py`,
`out/backtest_survivors.txt`. Delta prices, Delta funding, fee + live spread, no-look-ahead cut-offs, $1,500 accounts,
2025-26 (survivor-only coin list). RULE A on Delta picks after funding: +0.045R (t 1.8; 2025 0.000, 2026 +0.085);
$1,500 → $5,627 at 5% (worst drop 68%), $7,399 on the ladder (62%). Variants positive BOTH years: skip negative
funding +0.057R (drop 36%), largest third +0.059, most-traded third +0.052, funding>=0 and (big or liquid) +0.072R
(drop 42%) — all t < 1.7. Binance filter −0.001R. DELTA PORTFOLIOS: five-part score −0.79%/wk and 7-day low-volume
reversal −1.36%/wk (both NEGATIVE on Delta); quiet coins +0.36%/wk (2025 negative). FUNDING COLLECTION is the only
portfolio positive in both years on every version: every 3 days, fifths +0.555%/period (t 2.13, $1,500 → $4,094,
worst drop 25%; timing-fixed); daily thirds +0.085%/day (t 1.40, drop 32%). Binance reference 2021-26 (1x gross, costs): quiet coins
+0.61%/wk (t 3.0, 6/6), 7-day low-volume reversal +0.85%/wk (t 2.1, 6/6), five-part +0.27%/wk (t 1.7).

**HOLD LONGER THAN 24 H — WORSE (owner 2026-11-11 "maybe 48h?").** `scripts/fade_hold_length.py`,
`out/gainer_fade/hold_length.txt`. Binance 2021-26 (3,608 trades): 24 h +0.011R, 36 h +0.001, 48 h −0.003, 72 h
−0.003 (stopped 19% → 31%). Delta's picks 2025-26 after Delta funding: 24 h +0.033R, 36 h +0.031, 48 h +0.023,
72 h +0.018 — each day held adds stops and funding paid. 24 h stays.

**AFTER THE STOP: BUY IT, OR SHORT IT AGAIN — BOTH NOTHING (owner 2026-11-11, after ARIAUSD).**
`scripts/fade_stop_reverse.py` / `fade_stop_reshort.py`, `out/gainer_fade/stop_{reverse,reshort}.txt`. Binance 681
stopped rule A trades 2021-26 + Delta's 156 stopped picks 2025-26. BUY at the stop (16 exit/stop variants): −0.10R to
+0.02R, best t 0.4, adds +0.004R per rule A trade; on Delta's picks +0.03R (t 0.3; 2025 −0.13). SHORT AGAIN (22
variants: at the stop, after 1 h / 4 h, once back under the old stop, 0.5 ATR off the high, next day): −0.07R to
+0.04R, best t 1.05, positive 2021-24 but negative 2025-26; on Delta's picks −0.10R to 0.00R. After a stop the price
is a coin flip either way.

**DELTA-NATIVE READINGS ON DELTA'S OWN PICKS (owner 2026-11-11 "how to improve edge then?").**
`scripts/fade_delta_picks.py`, `out/gainer_fade/delta_picks.txt`: 1,022 picks 2025-26, Binance 1m prices, Delta's
real funding. Rule A +0.046R before funding, funding −0.013R (shorts on gainers mostly PAY), +0.033R after (2025
−0.010, 2026 +0.085). Readings consistent in BOTH years (exploratory, small): skip when D−1 Delta funding < 0
(+0.050 / +0.043, keeps 46%); largest third of pumps (+0.086 / +0.087, z 1.8); highest third of D−1 Delta turnover
(+0.055 / +0.057). None significant; candidates for the forward record, not rules.
**FUNDING-SETTLEMENT CAPTURE — FAILS (owner "focus on futures").** `scripts/explore_settle_data.py` +
`explore_settle.py`, `out/explore_settle.txt`: 5,173 Delta settlements with |rate| >= 0.25% an hour before (2024-26;
80% negative funding = longs paid). Holding the paid side across the stamp: funding +0.45% but price moves −0.21% in
the settlement minute itself and spreads cost ~0.14%: net −0.08% to −0.17% for every entry/exit tested (t −2 to −7);
longs −0.19%, shorts +0.05% (t 0.3). The market prices the payment in at the stamp.

**RULE A SKIP FILTER — DO NOT DEPLOY (owner 2026-11-11 "how to improve its edge", "run tests / backtests with the
rule").** `scripts/fade_filter_tests.py`, `out/gainer_fade/filter_tests.txt`. Filter: skip a pick if BTC rose
> +0.87% on D−1 or the coin's D−1 Binance taker-buy share > 0.5095. On BINANCE's picks it is strong: kept −
skipped +0.107R over 2021-26, positive in all six years; walk-forward cut-offs (earlier picks only) kept +0.071R vs
skipped −0.024R (z 3.97); smooth across cut-offs; account from $1,500 better in every year (e.g. 2023 ladder $426 →
$8,923). On DELTA'S OWN picks 2025-26 (1,022 trades on the same coins' Binance 1m prices): REVERSED — kept +0.008R,
skipped +0.101R (z −2.09; 2025 −0.110, 2026 −0.071); a random subset beats the kept set 98% of the time. Delta's
top gainers are a different set (Delta-only rankings, its own volumes). Live rule unchanged.

**FAST CHECKS ON DELTA ITSELF (owner 2026-11-11 "I don't have couple of weeks, we need to prove working strategy
soon").** `scripts/explore_delta_costed.py`, `explore_basis.py`, `explore_delta_basis.py`; outputs `out/explore_*.txt`,
live spread snapshot `out/delta_spreads_2026-11-11.json`. (1) SPREADS ARE NOT THE BLOCKER: 250 Delta perps at 11:48
UTC, median spread 0.03-0.04% for coins over $250k/day, 0.12% under $50k/day, worst 0.56%. (2) On the coins Delta
HAD LISTED AT THE TIME (~115, 2025-26; Binance and Delta weekly returns correlate 0.998): quiet-coins +0.04%/wk
(t 0.07) — NOT there; the earlier "+1.54% on Delta-listed coins" counted coins before Delta listed them. Five-part
score +1.05%/wk (t 1.3; 2025 +0.44, 2026 +1.78) — unproven. (3) BASIS/FUNDING: high-funding coins out-run
negative-funding coins by +3.1%/wk in PRICE (t 2.5) but REAL Delta funding takes −3.4%/wk (t −15): net ~0. The
carry side (short high funding, long negative funding) nets +0.2 to +3.4%/wk across variants, positive in both years
in most, but t <= 1.5 and drawdowns 60-80%: unproven. Nothing found today can be PROVEN on 88-97 weeks of Delta data.

**FROM PUBLISHED RESEARCH (owner 2026-11-11 "read external sources, run tests in different ways").**
`scripts/explore_lit.py`, `explore_composite.py`; `out/explore_lit.txt` (sources in its header),
`out/explore_composite.txt`. Open exploration, Binance perps 2021-26. REPLICATES: (1) reversal lives in low-volume
coins (Bianchi et al. 2022): 7-day reversal inside the least-traded third +1.40%/wk (t 3.9, 6/6), ~0 in the middle
third; (2) weekly regression with seven characteristics together: log volume −0.30%/wk per SD (t −3.9, 6/6), 30-day
volatility −0.58 (t −5.0, 6/6), 1-day volume spike −0.48 (t −5.1), 30-day return +0.38, 7-day return −0.26;
(3) turn-of-the-candle in BTC (t 6.7) but gone since 2024 and sub-basis-point; (4) BTC 21:00-23:00 UTC +0.05%,
under costs. COMPOSITE of the five (equal weights, nothing fitted), long top fifth / short bottom fifth weekly:
+1.48%/wk gross (t 3.8, Sharpe 1.6, 6/6 years, max drawdown 32%, worst week −27%); walk-forward weights (earlier
weeks only) +1.03%/wk (t 3.4, 6/6); Delta-listed coins only +1.54%/wk (t 2.2, 2024 negative). COST IS THE WHOLE
QUESTION: turnover 53%/wk -> fees alone leave +1.31%/wk, but a 1% round-trip spread leaves +0.26% (t 0.7). The
low-volume sort alone turns over 16%/wk and keeps ~+0.9%/wk at a 1% spread. Still a LEAD: which five was chosen on
the full sample; not seen on Delta's own data; spreads on Delta's thin coins unmeasured. Basis (premium index) data
fetched by `scripts/explore_basis_data.py`, test pending.

**WIDE EXPLORATION, ANY FAMILY — ONE LEAD (owner 2026-11-11 "not just fade ones, try any family, unpopular ones,
invent some").** `scripts/explore_wide.py`, `out/explore_wide.txt`: ~20 cross-sectional rankings (low vol, lottery,
skew, beta, reversal, residual reversal, momentum, near-high, illiquidity, volume surge, close location, price
level, age, range, wicks, streaks) at 1- and 7-day holds, plus calendar / market-wide ideas and BTC hour-of-day, on
all Binance perps 2021-26 (median 194 liquid coins). ONE flag: LONG the least-traded fifth, SHORT the most-traded
fifth (30-day volume, floor $5M/day), weekly: +1.30%/wk gross (t 3.24), positive all six years, both legs, floors
$1M-$20M, one day late, without BTC/ETH; capped at ±25%/wk it is +1.06% (t 5.27); turnover 23%/8% a week. On coins
Delta lists, ranked by BINANCE volume: +1.54%/wk (t 2.25, 6/6; capped +0.70%, t 2.69). On Delta's OWN volumes and
universe (2025-26, 88 weeks, survivors only): NOT seen (+0.2%/wk, t 0.2; 2025 negative). A lead, not a result: open
sweep, no hold-out, thin long leg (spread is the real cost), needs ~12-40 positions a side. Also noted, too small
after fees: 1-day residual reversal (+0.09%/day, 6/6 years, costs 0.30%); new listings lag the market on days 2-7
(−0.57%/day, t −2.2); alts beat BTC the day after BTC fell > 3% (+0.54%, t 2.4). Nothing in: low vol, lottery,
beta, momentum, near-high, calendar, trend-following, BTC hours.

**OPEN EXPLORATION — ONE LEAD, NOT A RESULT (owner 2026-11-11 "look for unpopular ideas", "run them in different
ways instead of hard by the book").** `scripts/fade_explore_open.py`, `out/gainer_fade/explore_open.txt`: no
pre-registration, all six Binance years at once. LEAD: take rule A only when BTC was NOT strong on D−1 (change <=
+0.87%) AND the pump was NOT buyer-driven (D−1 taker-buy share <= 0.5095); cut-offs from 2021-23. On 2024-26: kept
49% of trades, +0.062R (z 2.36; +0.029 / +0.075 / +0.109) vs skipped +0.001R; kept − skipped +0.061R (z 1.59); a
random half does as well 5% of the time. Suggestive, not proven. Weaker leads: gainers fall 08:00-09:00 UTC (+0.20%
of price, 6/6 years, before fees) and 13:00-14:00; a first-hour spike that gives half back (+0.10R, 5/6). Nothing in:
re-shorting after a stop, repeat picks, day of week. To confirm the lead the only clean data is FORWARD: tag each
paper pick with the two readings and compare kept vs skipped (not built).

**SECOND IDEA SEARCH — 13 FAMILIES — FAIL (owner 2026-11-11 "search all").** Pre-registered
(`docs/fade_families_prereg.md`; `scripts/fade_families_data.py` + `fade_families_search.py`;
`out/gainer_fade/families_{dev,holdout}.txt`). Binance 2021-23, ~45 variants: crowding (funding, taker-buy share,
open interest, long/short ratios), spot vs futures, coin age, BTC hedge, long the losers (−0.025R, z −2.73), the
pair, long the gainers, weekly gainers (−0.040R, z −3.16), BTC's D−1 move, volume-surge picks, scaling in, time-of-day
exits. Liquidations: NOT TESTABLE (no archive). ONE qualifier under the rule: rule A only when the all-account
long/short ratio is in its highest third (dev +0.102R, z 4.27; lowest third −0.117R). HOLD-OUT 2024-26: +0.033R,
z 1.17 vs rule A's +0.031R — FAIL (bar z 2.70 and above rule A). Dev-only patterns, NOT confirmed: fading works less
after BTC rose (highest third −0.087R) and when taker-buy share is high (−0.083R). The hold-out has now judged three
candidates; further searches on it are worth little. Live rule unchanged.

**IDEA SEARCH — entry timing / condition / hold / pump shape — FAIL (owner 2026-11-11 "I would challenge on no
edge").** Pre-registered (`docs/fade_idea_search_prereg.md`, `scripts/fade_idea_paths.py` + `fade_idea_search.py`,
`out/gainer_fade/idea_search_{dev,holdout}.txt`; 1m simulator matches fade_binance_test to 0.00000R). ~40 variants on
Binance 2021-23: "enter later only if the coin is already UP" was positive in all 3 years, "only if already DOWN"
negative; holding 48/72 h, skipping a day and the largest pumps worse. Frozen: +2 h if up >= 0.25 stop (dev +0.182R,
z 4.05) and +1 h if up >= 0.10 stop (dev +0.124R, z 3.71). HOLD-OUT 2024-26: +0.045R z 0.73 (2025 −0.068) and −0.003R
z −0.06 — both FAIL (bar z >= 2.24). The dev pattern was selection on ~40 tries. Live rule unchanged.

**%R / STOP-DISTANCE FILTERS — FAIL; STOPS THAT "COME BACK" (owner 2026-11-11).** Pre-registered
(`docs/fade_wpr_stop_filter_prereg.md`, `scripts/fade_wpr_filter.py`, `out/gainer_fade/wpr_stop_filter.txt`; harness
reproduces rule A's judged −0.002R). Binance 2021-24, taken trades: W (daily %R(14) of D−1 >= −20) −0.025R (z −1.64);
S (stop <= 20%) +0.016R (z +1.00); both −0.004R — all FAIL; live rule unchanged. Stopped trades: 15% of trades
(2021-24), 30% (2025-26); 65% / 59% of them closed D back under the stop, 18% / 27% under the entry; yet no stop at
all is +0.018R vs −0.002R (2021-24) and +0.022R vs +0.046R (2025-26): no consistent gain, and an unlimited tail.
Our paper stops: AINUSD 10-31 closed above its stop (stop helped), INITUSD 11-10 back under it (hurt).

**FUNDING-COLLECTION PAPER ACCOUNT (owner 2026-11-11 "approved"; PR #161).** `live/funding_paper.py`, run from the
fade runner's loop as a BACKGROUND task with its own Delta client (never delays the real account). Every third UTC
day ((day // 86400) % 3 == 0; first 2026-11-12) at 00:20: coins with 30-day mean turnover >= $250k and >= 35 days
of history (the runner's 40-day `_day_cache`; the backtest asked 60), 3-day mean daily Delta funding, LONG the lowest
fifth, SHORT the highest fifth, half the marked equity each side, whole contracts at the real best ask/bid + 0.0531%
fee; coins in both books kept untouched. Funding settled at Delta's interval from FUNDING history; equity marked at
each rebalance. $1,500 paper. Tables `funding_positions`, `funding_periods`; report section "Funding collection
(paper)" (db_probe.collect_funding); attention if MISSED or none for > 4 days. NO ORDER PATH. Backtest it comes from
(PR #160, scripts/compare_ladder_funding.py; timing-fixed 2026-11-11): $1,500 -> $4,094 2025-02..2026-10, worst drop
25%, best of several variants. Dry run on live data 2026-11-11: 69 liquid coins, 13 a side, median spread 0.105%, INITUSD −8.8%/day.

**RUN 3 FIRST STOP + HALT (2026-11-11).** ARIAUSD (entry 0.21834, stop 0.2722 = 24.7%, 2×ATR 12.3%) rose to mark
0.2809; the exchange market stop fired 09:47:45 UTC, 241 bought back at 0.26884 (mark led last), −$122.33, −0.94R;
wallet $1,761.76 → $1,639.71 (high-water $1,761.98, ladder still 10%). The bot could not RECORD it: `last_close`
compared epoch seconds with the DB's datetime → TypeError every pass → HALTED after 10 (first exchange-side close
of run 3; the fake venue stamped no times). Fixed in #155 (deployed 10:24 UTC), the close recorded as STOP_LOSS,
`fade-live-resume` run 10:25 UTC → RUNNING. The coin kept rising (0.30 by 10:20): the stop saved ~$77.

**FADE REAL-MONEY RUN 3 — RULE A LADDER, $1,500 (owner 2026-11-09: "Let's run rule a ladder in paper and in
real", "approved on 1-6"; rules `docs/fade_live_ladder_prereg.md`, branch `feat/fade-ladder`).** Risk 10/5/3 by
drawdown from the high-water mark (steps at 20%/35%); AMENDMENT 1 (leverage, `feat/fade-leverage`): notional ≤ 60%
of the balance on isolated margin ≤ 30%, 1x-3x per position with liquidation ≥ 2× the stop away
(`live.fade.lever_plan`; risk taken ~6% average, 2x on ~40% of trades; the probe must show isolated margin),
MARKET stop, no take-profit. PROBE PASS 2026-11-09 21:31 UTC at 2x (XRPUSD: margin_mode isolated, auto_topup False, margin = notional/2, liquidation +49.5%, market stop size 1); `dry_run "0"` PR follows. Wallet at the switch $1,661.84. FIRST DEPLOY 2026-11-09 20:42 UTC (#149+#150, exits-only): the margin check read Delta's single-product /v2/positions (no margin fields), bought back run 1's 1-contract AIOTUSD (−$0.16) and HALTED; fixed to read /v2/positions/margined and to check leveraged (>1x) positions only. Earlier: PR #149 merged but NOT deployed: a GitHub Actions outage blocked the
infrastructure apply and deploy-prod; the old run-1 bot halts at its next entry check on the topped-up wallet. Under $1,000 at an entry check it STOPS until
the owner refills to ≥ $1,400 and runs `python -m live fade-live-capital` (mark reset, ladder back at 10%);
refills ≤ $3,000 for the run. With prod keys but `dry_run` ≠ "0" the account is EXITS-ONLY (reconciles, buys back,
never sells), so deploys/restarts never strand a position; the wallet band is checked only when a run begins.
Halts also on 10 failed passes in a row and on a wallet move no trade explains (deposit/withdrawal → record it
with `fade-live-capital`). Paper twin: account `LADDER` ($1,500, refills at once, `fade_paper_capital`).
Validation: `scripts/fade_live_sim.py` (26/26 checks, `out/gainer_fade/live_sim.txt`) runs the real module through
every day of Delta 2025/2026 + a budget-exhausting stress year against a rules-text reference: day-end balances
within 0.021%, identical trades/steps/refills, same with daily restarts + 3% failed reads. Two independent reviews
found 3 demonstrated bugs + smaller ones, all fixed with tests (the fault sim itself found a mid-exit blip that
would have halted trading). Sizing in `live.tf`: 1500 / 0.10 / dry_run "1" (exits-only) until the market-stop
probe passes; a second PR sets "0". Review 2026-12-10 on pooled per-trade R, not balance. Quick (version 2,
`feat/fade-live-quick`) was never run.

**(VERSION 1, SUPERSEDED 2026-11-09) FADE REAL-MONEY RUN — SWITCHED ON BY THE SECOND PR (dry_run "0", equity 176, risk 0.03); live from the deploy that follows it (owner 2026-11-02: "worth starting fade in live right now?" → told the
two-year evidence is ~break-even → "approved on tiny run, I have 176$"; rule A, 3% risk, stop the run below
$140, own sub-account).** Rules fixed first in `docs/fade_live_prereg.md`; code `live/fade_live.py`, called from
the fade runner's loop. Same picks as paper A: sells each with a reduce-only-safe order shape — IOC limit 1%
under the bid, exchange-held bracket stop (mark-triggered, 1.5R cap) at 2×ATR% — leverage set to **1x** and
read back (Delta defaults these coins to 20x), size = 3% of the wallet balance (≤45% notional each), a stop
that is not seen resting within 30 s → bought back + HALTED; buys back reduce-only at 24 h, cancels leftovers
only once flat; reconciles every pass (unknown position → HALTED, untouched; SENDING row → looked up by client
order id, never resent). STOPPED (final) below $140; `python -m live fade-live-resume` clears a HALT. Tables
`fade_live_positions` / `_state` / `_events`; report puts a REAL row first and a real-vs-paper journal.
**OFF unless** sizing `dry_run` is exactly "0", `DELTA_ENV` prod, a key is set and the wallet is $140–$220.
2026-11-02: owner made the sub-account ($176.56 USD) and a Trading key (read + trade, whitelisted to
13.207.73.230) in `deltabt-paper/live/bot/venue-credentials`; **probe PASS 11:06 UTC** (1 XRPUSD sold at 1.2500 vs
bid 1.2501 with the bracket, stop seen resting, bought back 1.2503, Delta auto-cancelled the bracket, cost ~$0.002).
Fade mode has NO daily restart: the sizing is read only at container start (a deploy, or a crash restart).
TRAP 2026-11-02: the owner deleted `deltabt-paper/pg/bot` (the DB password) by mistake with the metals key;
run_live.sh exits 90 "pg data exists, its secret is empty" and the rollback fails the same way — the bot was
down 12 min until `aws secretsmanager restore-secret --secret-id deltabt-paper/pg/bot` (owner ran it).

**GAINER-FADE PAPER RUNNER (owner 2026-10-28: "get variant A running as paper; scrap crypto and metals bot";
"both, side by side"; "2%"; "keep a backup").** `live/fade.py`, run mode `DELTABOT_UNIVERSE=fade` (live_sizing
`universe`, an SSM parameter — switching it is NOT a host replacement; the variant string stays). Once a UTC day
(00:01:30) it shorts the two previous-UTC-day top gainers (research definitions; parity test vs the lab) on two
simulated $250 accounts at 2% risk / 5x cap: **A** (stop 2×ATR14%, exit at the day's last 1m close) and **QUICK**
(same + take-profit 0.2× the stop distance). Entry = close of the 00:00 minute (the lab used the daily open).
Every 30 s it walks closed 1m candles (stop first, `ltp_close` fill), settles fees + real funding; tables
`fade_positions` / `fade_days` / `fade_heartbeat`; own FastAPI `/healthz` `/readyz` in the brief-report shape; an
"alive" log line every 5 min for the CloudWatch silence alarm. `db_probe.collect` returns ONLY `{"fade": …}` in
fade mode (the inherited signal-bot tables are stale); `brief_report` renders "Paper trade journal" (owner 10-29: simple, like the old trade journal): attention list, one account table (A, Quick, options), the fade journal (open, then closed newest first; newest 120 trades) and the strangle journal (newest 60).
`_roll.yml` treats `fade` as unbound. Smoke-tested locally on live Delta data against a throwaway Postgres
(10-28: AIN + EVAA; QUICK hit both targets, A stopped EVAA). NO ORDER PATH. **Backups before scrapping:**
pg_dump files on each pg volume + snapshots `snap-01e227faede28a963` (bot) and `snap-02cb8add4ffb7daa3` (metals),
tag `Role=keep-before-fade` (the host's retention only deletes `Role=pgdata-backup`). **LIVE since 2026-10-28 12:40 UTC** on the bot host (deploy run 40686903238, image dde1ef9; #143 set
`universe` "fade"). 10-28 itself is MISSED (started past the 3 h window); first entries 10-29 00:01:30 UTC. Health is
on port **8000**. **Metals scrapped 10-28:** `deltabt.service` stopped + disabled 12:46 UTC, then the stack removed
from `live.tf` + `monitor.yml` (infra/destroy-metals). Its pg volume is KEPT, still managed, detached: `db_host.tf`
`retired_pg_volumes` (OpenTofu `removed` cannot target one for_each key). DESTROYED 10-28 13:25 UTC
(#144 + `infrastructure` dispatch with `allow_replace`, run 40695776673 — this repo's apply has NO approval gate, the
dispatch applies at once): instance terminated, EIP released, GitHub vars deleted, owner removed 13.205.134.107 from
the key's allowlist. Left: secret `deltabt-paper/live/metals/venue-credentials` (not Terraform's; delete by hand).
The host keeps Name tag `deltabt-paper-bot` (owner: "keep it as is") — workflows find hosts by that tag.
**Fade learning (owner 10-28 "how will model training work here?" → "approved"; `live/fade_learning.py`, rules
fixed first in `docs/fade_model_switch_on_rule.md`).** After the morning entries the runner records every eligible
gainer (top 20 by D−1 change, traded or not) in `fade_candidates` with readings known at entry — the research daily
ones plus, new, the order book (spread, ±1% USD depth, imbalance) and the last 50 trades (aggressive-buyer share) —
labels each a day later as if shorted under both exits (size-free R before funding). The GRADE is a separate process,
`live/fade_grade.py` via `python -m live grade-model` (Mondays in the monitor document; it grades every complete week
not yet graded) — never imported by the runner (`test_model_never_trades` covers it): the model's own daily top 2 vs
the rule's top 2 vs random 2. It never chooses a trade; eligibility needs 4 pooled
qualifying weeks beating both at p ≤ 0.05, and the owner decides. Expect a FAIL on the daily readings alone (764
filters, rank corr −0.23); the book/flow readings are the only new information (~2–3 months to a fair verdict).
TRAPS found while building it: `DeltaClient._get` returns the whole envelope (`{"success","result"}`); a thin coin
can have NO trade in the 00:00 minute (REST serves no candle for leading no-trade minutes) — the runner now enters
at the first traded minute within 15 min and skips a coin with none (it would otherwise have waited for a candle
that never comes and MISSED the day); a thin coin's last minutes may have no candle, so the 24 h exit closes at the
last traded minute once the day is over; the weekly grade drops all-empty readings (same trap as #141).

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

**RULE AUDIT 2026-10-20 (owner: "were the crypto bot's trades as per rule?").** All 2,051 bars the live bot judged
under flip_st_short (20 coins, 10-19 14:31 → 10-20 04:25 UTC) were re-judged with `evaluate_spec` on Delta's REST 1m
candles: 2,046 agree (23 signals fire on both, 19 suppressed on both). VVV short (00:50 UTC) = per rule. **ARB short
(04:20 UTC bar, opened 9:55 AM IST) = NOT per rule:** on Delta's candles the 5m Supertrend stayed bullish (line 0.20006
vs close 0.20012); the bot saw bearish (line 0.20250). The real signal came one bar later (04:25). Other
differences: VVV 03:25 fires on Delta's candles but not on the bot's; 2 bars the bot judged that Delta's candles suppress. Cause
= the socket's candle (see "Live and venue traps": candlestick_1m open). Stop/target/size on both trades matched the rule.
FIX (owner approved 10-20; LIVE on both stacks since #135/#138/#139 + this): at each decision-bar close the bot
refetches its socket-built minutes from REST and swaps them in (`TradingBot._reconcile_socket_bars`,
`SymbolCandleBuilder.reconcile`), rows replaced in `market_candles` (source `rest`). REST can serve the just-closed
minute short of trades (BTC 5,941 at +0.3 s, 6,125 at +1 s), so a REST bar with LESS volume is asked again at +1 s /
+2 s while the minute is < 3 s old — then taken as final (live: the socket sometimes counts a trade REST puts
elsewhere, TIA 7,539 vs 6,539, unchanged minutes later; REST is the archive). Only a traded minute REST does not
serve keeps the socket bar (`SOCKET_BARS_KEPT`). No-trade socket minutes (flat, volume 0 — already the archive's
bar; a REST window starting on one returns nothing) are skipped (#138; before that ~2,000 "kept" events a day). A
failed request (`RECONCILE_FAILED`) leaves the minutes for the next decision. /healthz: `reconciled_minutes` /
`reconcile_kept`. Tests are aligned to the real clock: count decision boundaries, never assume one (#139).

**1m %R CHECK 2026-10-20 — BUILT, HELD (owner: "hold and close 136").** The owner saw VVV/ARB confirmed with 1m %R
already oversold (the 1m gate checked only the 1m Supertrend). Family `flip_st_short_1m_wpr` = flip_st_short + 1m
%R still above −80 (`wpr_rule="not_exhausted"`, hash `401e71db9612…`, in the code since #135; history in
`out/signal_lab_flip_short_1m_wpr/`). 9 months, every signal: −0.054R → −0.035R (blocked 427 at −0.092R, 235 fire
later at +0.034R), inside the noise. Variants tested and rejected: setup on 1m + 5m ST confirm −0.196R (0.9% stops);
5m ST only + 1m flip/cross −0.033R but Aug–Oct −0.161R. Replay of the bot's own week (its daily lists, 10-13 13:22
UTC → 10-20): flip_st_short +3.13R closed (8) + ARB open ≈ +2.6R vs the new rule −2.13R (6) — it skipped INJ,
SIREN, ARB (all 3R). PR #136 (switch the bot) CLOSED; the bot stays on flip_st_short. To grade the check later:
the 1m %R is in every signal's `indicators->'confirmation'`. Replay recipe: `TG_PICKS` = the bot's lists,
`TG_FROM` = 1791897720; the engine stamps `entry_time` = the signal bar's OPEN (entry 5 min later) and drops
positions still open at the end of the data from `trades_*.csv`.

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

## What is CLOSED, and why

**OPTION-SELLING PAPER ACCOUNT (owner 10-28 "let's start options now" → "approved"): `live/options_paper.py`**, in
the fade runner's loop on the bot host. 12:00 UTC daily: one BTC + one ETH 1% strangle, 1 contract/leg, sold at the
REAL best bid (mark/bid/ask/size stored), settled at Delta's `settlement_price` of the expired option (= payout per
unit; Delta settles on its index's 30-min average — 10-28: 81,400.67 vs 30-min index avg 81,397, perp 1h close
81,288). $250 simulated; tables `option_legs` / `option_days`; report section "Option strangles" (one row per strangle). Read
per `docs/options_paper_read.md` (~120 strangles ≈ 60 days before the mean is 2 SE from zero; 2026 SD 134 bps).
Live snapshot 10-28 21:00 IST: bids 99–100% of mark, 7k–13k contracts at the best bid.

**RETRACTED 2026-11-16 — THE OPTION "PASS" WAS A SETTLEMENT BUG; NO EDGE.** `option_selling_lab.table()` settled
each leg at `p.get(d - HOUR)` = the perp at 00:00 of the expiry day, 12 h after the 12:00 sale and 12 h BEFORE the
12:00 expiry, so it counted half the price move the option was priced for. `scripts/option_recheck.py` (two P&L
implementations agree on every leg; `out/options/recheck/report.txt`) re-fetched all 2,680 1% legs 2025-01-15 ..
2026-11-15 and reproduces the lab exactly with the 00:00 price (TEST +24.35 bps, n 600, 76%). With Delta's own
`settlement_price` (all 2,680 expired legs; perp-12:00 stand-in matches it on 98.8% of legs): at 0.9 x mark BUILD
-16.1 bps (t -1.6), TEST -14.0 (t -1.6), win 64%; at 1.0 x mark (paper's real bids are 0.99 x mark, median
spread 1.8%) BUILD -2.8, TEST -3.4 (t -0.4) -- zero edge, the options are fairly priced. Paper account
10-29..11-15 agrees: BTC 17 strangles -32 bps, ETH 7 -124 bps. Everything below in this entry is superseded.
**(SUPERSEDED) Selling daily BTC/ETH options — BUILD done, TEST PASSED (owner 10-28 "option 2"; pre-registered
`docs/option_selling_prereg.md` before any option price was fetched; `scripts/option_selling_lab.py`,
`out/options/build.txt`).** Delta still serves `MARK:` 1h history for expired daily options (12:00 UTC expiry; BTC
0.001, ETH 0.01 contracts; fee min(0.01% notional, 3.5% premium)). 13,020 entry marks fetched (2025-01-15 → 2026-10-27);
95% found — BTC strikes 4–5% out are often NOT listed on calm days (coverage 1–3%: ≥ 98%; 4%: ~85%; 5%: ~66%), so
results there over-weight volatile days. Short strangle 24 h before expiry, sold at 0.9 × mark, held to expiry.
BUILD 2025 (chooses m only): m 1/2/3/4/5% → win 74/80/86/89/92%, mean +13.8/+7.6/+3.1/+0.7/+0.9 bps of notional per
strangle (p 0.052/0.146/0.302/0.567/0.480); avg loss ≈ 3× avg win at 1%, ≈ 12× at 5%; worst −989 … −771 bps. **m = 1%
chosen and frozen.** At 1%: BTC +10.8 bps (+$40/yr for one 0.001-BTC strangle a day), ETH +16.8 bps (+$17/yr).
**TEST 2026 (opened once, `out/options/test.txt`): PASS.** 600 strangles / 300 days, **win 76%**, **+24.4 bps** of
notional per strangle (one-sided p 0.000), avg win +74.6 / avg loss −137.6 bps, worst −1,577 bps; BTC +21.3 bps (78%
win, +$47 for one 0.001-BTC strangle a day), ETH +27.4 bps (75%, +$18); puts and calls both positive. $250 selling one
BTC + one ETH strangle a day → $315, low $244, worst day −$7.01, worst week −$13.85. NOT modelled and must be checked
before any paper/real use: real bid fills (0.9 × mark assumed), Delta's short-option margin and intraday
liquidation (positions held to expiry here), settlement on Delta's index average (perp 11:59 close used), and the
tail — losses are uncapped; the worst strangle in two years lost 15.8% of notional, and a crash day bigger than any
in 2025–26 would cost more.

**Gainer fade, no stop — DEV FAIL (2026-10-28, pre-registered `docs/gainer_fade_prereg.md` before any result,
`scripts/gainer_fade_lab.py`, `out/gainer_fade/dev.txt`).** Owner's "different idea". Short the K previous-UTC-day
top gainers (crypto perps, D−1 change > 0, turnover ≥ $250k) at the UTC open, cover at the close. Picks reproduce
`out/top_gainers/picks.csv` exactly (2,730/2,730 coin-days). Dev (Jan–Jul), K = 2: net **−0.30%**/trade (424), excess
vs the same-day liquid market −0.50% (t −0.50, p 0.69), random pairs beat it 85% → FAIL on all three. Shape: median
**+1.90%**, win 57% — most gainers do drift down — but squeezes (worst −162%, a coin that more than doubled again)
outweigh it. K = 1/5/10 and by gain size: nothing. **Hold-out (Aug → 10-27) NOT opened** — kept sealed for the
follow-up the dev shape suggested (the same fade WITH a stop), which, being dev-inspired, only the hold-out can judge.
**Gainer fade WITH A STOP (pre-registered `docs/gainer_fade_stop_prereg.md`, `scripts/gainer_fade_stop_lab.py`).**
Stop k × 14-day daily ATR% above the 00:00 UTC entry (1m `ltp_close` fill), else exit at the UTC close; A = top 2
gainers, B = top 2 among defi/layer/turnover < $712,048 (owner: "why not selected coins?" — by-name persistence was
checked first on dev: rank correlation −0.05, last half's best coins −0.86% next half, so names were not used).
DEV (Jan–Jul, chooses k only, not a judgement; 400 trades, first ~2 weeks lack a 14-day ATR): A k=1/1.5/2 →
+0.050/+0.068/+0.079R vs random coins under the same stop +0.025/+0.020/+0.015R; B +0.032/+0.030/+0.032R, no
better than its random (+0.037/+0.025/+0.019R). **k = 2.0 frozen.**
**HOLD-OUT (opened once, pick days 08-01 → 10-27, `out/gainer_fade/stop_holdout.txt`): A PASSES, B FAILS.**
A: 176 trades / 88 days, **+0.069R ±0.065** (median +0.08R, win 56%, stopped 22%, worst −1.23R) vs random coins
under the same stop **−0.028R**, 0 of 2,000 random draws as good (bar 0.025); $250 → $310, max drawdown 19%.
B: −0.010R vs random −0.040R (p 0.084), $250 → $238 — the coin-group selection adds nothing (dev agreed).
Strength, stated plainly: "the top gainers fall MORE than other coins over the next UTC day" now holds in both
windows against random picks; the PROFIT per trade is small and only ~1 SE above zero on the hold-out alone
(dev +0.079R ±0.033 was used to pick the idea and k). Not included: funding (shorts on pumped coins usually
RECEIVE it — likely a tailwind, unmeasured), survivorship (delisted collapses missing — likely understates shorts),
entry exactly 00:00 UTC. Both positions are open ~24 h a day, every day. Per the pre-registration the owner decides
whether it goes to PAPER; never straight to money.
**Ladder on A (owner 10-28, "can ladder help?") — NO EFFECT.** The repo's standard rungs, untuned
((0.5,0), (1,0.5), (1.5,1), (2,1.5); exits before rungs; 1m ltp), paired on A's 576 trades: −0.012R ±0.015 (dev),
+0.023R ±0.031 (hold-out), **−0.002R ±0.014 both** — changes only 88 trades (42 better, 46 worse); smaller average
loss (−0.40 vs −0.53R) bought with fewer and smaller wins. Exploratory (both windows already seen), but paired and
untuned. Same lesson as every exit test in this repo: the exit is not the lever.
**Trailing stop on A (owner 10-28) — NO HELP.** Paired on the same 576 trades: the old trail bot's 0.5R behind the
best price from +0.5R → −0.011R ±0.016 (dev −0.017, hold-out +0.003; win rate 57 → 61% but average win 0.54 →
0.41R); a wider 1R from +1R → −0.008R ±0.006 (touches 16 trades). Rule A keeps its plain stop + 24 h exit.
**2025 REPLICATION — FAIL; all four squeeze filters REJECTED (2026-10-28, pre-registered
`docs/gainer_fade_2025_prereg.md` before any 2025 data was fetched; `scripts/gainer_fade_2025_lab.py`,
`out/gainer_fade/judge_2025.txt`).** The new code reproduces A's 572 trades of 2026 exactly (max R diff 4e-16).
2025 (pick days 01-15 → 12-31, 150 coins listed today): A **+0.009R ±0.022** (689 trades), win 57%, avg win +0.35R
vs avg loss −0.43R, PF 1.05; random coins under the same stop +0.001R, share ≥ A 0.21 → FAIL; $250 → $268 with a
33% drawdown. 2026 for comparison: +0.076R, random beats it 0 times. **Funding is a slight COST, not the
tailwind assumed:** 2025 −0.011R, 2026 −0.007R per trade. Filters (vs A, 2025 / 2026): a closed-near-high −0.013 /
−0.029; b 04:00 confirmation −0.001 / −0.068; c skip negative funding +0.022 (p 0.095) / −0.047; d skip two-day runs
−0.001 / −0.074 — none adopted. Reading: "top gainers fall more than other coins next day" was a 2026 effect; across
two years the fade is about break-even. Per the pre-registration it does NOT go to paper unless the owner
re-decides.
**Survivorship challenge (owner 10-28: "I would challenge it") — NOT the reason.** Delta still lists 26 expired perps
(`/v2/products?states=expired`); 23 serve 2025/2026 history (`scripts/gainer_fade_delisted_check.py`,
`out/gainer_fade/delisted_check.txt`; FTM no 1m, MATIC/RNDR ended 2024; PEPE/FLOKI/BONK/SHIB moved to 1000× contracts,
old-name days dropped where the new one traded). TRAP: the REST pager anchors on `end` and stops at an empty page, so a
delisted coin fetched to "today" returns no 1m at all, and its daily cache carries flat filler after the delisting —
fetch to the settlement time and cut the daily rows there. Added 2,053 liquid coin-days to 2025: rule A **+0.006R**
(693 trades) vs random +0.002R, share 0.344 → still FAIL; the delisted coins were picked 99 times at −0.035R (not
good shorts on their pump days). 2026 unchanged (+0.078R, PASS). Why 2025 failed (descriptive): winners smaller
(+0.35R vs +0.54R) at the same 57% win rate; 2025 picks were smaller gainers (median +15% vs +22%; 31% under +10%);
no gain size works in both years (>40%: +0.21R in 2025, −0.02R in 2026); a 3-month losing run each summer
(2025 Jul–Sep −15.7R, 2026 Jun–Aug −12R).
**"Can the win rate be raised on 2026?" (owner 10-28) — yes on 2026, and it does not carry.** 764 filters (single +
pairs of entry-time readings: D−1 change/turnover/ATR/wick, D−2 change, funding at 00:00, weekday), each keeping ≥ 300
trades. Best 2026 win rate 64% (+0.089R) → same filter on 2025 55%, −0.024R; best 2026 profit +0.109R → 2025 −0.012R;
the 50 most profitable on 2026 average −0.000R on 2025 (no filter +0.006R). Rank correlation of a filter's 2026 vs
2025 profit **−0.23**: the better it looked on 2026, the worse on 2025. TRAP caught in this search: "price below the
open at 04:00" used as a filter on a 00:00 entry is LOOK-AHEAD (picks trades already winning; showed 70–76% win
rates). The pre-registered filter b was correct (it moves the entry to 04:00).
**Four filters from EXTERNAL sources — all REJECTED (owner 10-28: "use external refs";
`docs/gainer_fade_external_prereg.md` before computing them; `scripts/gainer_fade_external_lab.py`,
`out/gainer_fade/external_judge.txt`; Delta `OI:<SYM>` 1h history fetched for 216 coins, none empty).** Judged on
BOTH years (adopt only if better than A in each year AND pooled day-block p ≤ 0.0125). Vs A, 2025 / 2026:
H1 illiquid half (Zaremba 2021, Kozlowski 2020) +0.000 / −0.080R; H2 low abnormal volume (Llorente 2002; large-move
volume study) −0.004 / −0.060R; H3 funding ≥ day median, crowded longs (practitioner) +0.023 / −0.051R; H4 OI rose
during D−1 (practitioner) +0.008 / −0.009R (OI rises on almost every pump — barely filters). None better in both
years; pooled p 0.48–0.96. Same shape as every filter before it: what helps one year hurts the other.
**The look-ahead "70–76% win" turned into a tradable EXIT (owner 10-28: "are you sure it failed?") — worse in both
years.** Short at 00:00 as A; at a check time, close if not in profit, else hold (stop as A). Mean R 2025 / 2026:
A +0.006 / +0.078; check 1 h −0.007 / +0.047; 2 h −0.019 / +0.054; 4 h (9:30 IST) −0.015 / +0.047; 8 h +0.002 /
+0.067. Win rate falls to 36–47% (cut trades are small losses) and the trades losing at the check that would have
recovered are thrown away. Same lesson as the ladder: early exits cost more than they save.
**WIN-RATE SCAN (owner 10-28: "park variant A; find 70–80% win rate in both years, open to anything").** A is PARKED.
`scripts/winrate_scan.py` (`out/gainer_fade/winrate_scan.csv`): stop {1,2,3}×ATR14% × take-profit {0.1…1.0}× stop,
24 h max, on fade / random short / momentum (long top-2 gainers) / random long, both years. Small targets reach 70–89%
wins on ANY entry including random — win rate alone means nothing; random longs lose in every cell. Only cell meeting
win 70–80% + net R > 0 + above random, in BOTH years: **fade, stop 2×ATR (~20–22% above), take-profit 0.2× stop
(~4–4.5% drop)** — 2025: 72% win, +0.010R ±0.013 vs random +0.001 (p 0.18, NOT distinguishable), $250 → $281, worst
drop 15%; 2026: 79% win, +0.032R ±0.015 (p 0.024), $250 → $356, worst drop 20%. One loss ≈ 2.3–2.9 wins. 1 of 60
cells, both years already seen; it is the same fade effect reshaped, not a new edge. Forward data is the only judge. — do not re-test without new information

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

- **The socket's 1m candle is not the archive's 1m candle (found 2026-10-20).** Delta's `candlestick_1m`
  stream opens each minute at the PREVIOUS minute's close, and its high/low include that price; REST
  `/v2/history/candles` opens at the minute's first trade. Close and volume agree. ARB, 7 days: 816 of 1,079
  socket-built minutes differ, all of them minutes whose first trade ≠ the previous close; REST-built minutes
  (startup backfill, gap repair) are exact. `candle_builder` stores the stream's open as given. Backtests,
  the model's history and the labeller all use REST, so the live bot judged slightly different bars, and the
  share of socket minutes grows the longer the bot runs without a restart. It flipped the ARB 10-20 decision.
  Fixed by REST-reconciling before each decision (see RULE AUDIT 2026-10-20); the socket still drives timing.

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

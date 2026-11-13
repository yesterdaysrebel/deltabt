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

**Selling daily BTC/ETH options — BUILD done, TEST PASSED (owner 10-28 "option 2"; pre-registered
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

# Prod dry run — pre-registration

Written 2026-10-02, before the `dryrun` stack exists. Owner's decisions; this file records them so
the read cannot be redesigned after the data arrive. Template: `docs/v5_stopping_rule.md`.

## What runs

- **Stack `dryrun`** (`infra/terraform/live.tf`): the live code path on **Delta India PROD** with a
  **read-only** API key and **no order path** (`live/dry_run.py`; `live_sizing.dryrun.dry_run = "1"`).
- **Entries:** `SPEC:manual_scalp_both_t3@5` (strategy fingerprint `41e764beceaf`), universe
  BEATUSD / AKEUSD / BANKUSD.
- **Sizing as the real pilot would size:** equity $250, risk 2% ($5/R), AKEUSD may take one
  contract if that risks ≤ 5% of equity (`min_contract_risk_cap`).
- **Gates as the real pilot would run them:** 20% drawdown latch (terminal, no resume), 10% daily
  loss, 8 consecutive losses, 72 h time stop.
- **Every entry** passes the live broker's checks against the real prod book (kill switch,
  entry-side deviation ≤ 0.25R, spread ≤ 0.5R, mark not beyond the stop, leverage leaving
  liquidation ≥ 3 stop distances away), and is then recorded as `DRY_RUN_ORDER` or `DRY_RUN_REFUSED`.
- **Positions** are filled and exited by the paper fill model on prod ticks. Every position is
  shadowed under **baseline** (hold to 3R), **ladder** (0.5→0, 1.0→+0.5, 1.5→+1.0, 2.0→+1.5) and
  **trail** (0.5R behind the peak from +0.5R), one row per position per rule in `shadow_exits`.
- **Experiment** id prefix `DRY-`; execution profile **paper**.

## Stopping point (frozen)

Read once, at the first of: **100 closed positions**; **21 days** after registration; the
**20% latch**; a **defect** below. No interim reads change anything.

## What this can conclude — counts and per-trade quantities

| # | question | source |
|---|---|---|
| D1 | How often would the live checks refuse an approved entry on prod books, by reason and by symbol (deviation / spread / mark / leverage / zero contracts)? | `system_events` `DRY_RUN_*`, `risk_events` |
| D2 | AKEUSD under the floor rule: share sized at one contract vs skipped. | `strategy_signals.detail`, `risk_events` |
| D3 | Spread and touch distance at signal time, in R, per symbol (median, p90): the slippage a market entry would meet. | `DRY_RUN_ORDER.book` |
| D4 | The leverage chosen and the liquidation buffer, per symbol. | `DRY_RUN_ORDER.leverage` |
| D5 | Exit shape under each rule on identical entries: mean and median R, win rate, 3R hits, max drawdown in R, longest non-positive run. Read against the backtest (`scripts/paired_exits_lab.py`: baseline +0.037, ladder −0.018, trail +0.006 R/trade; drawdown 42.7 / 25.1 / 17.2 R). | `shadow_exits` |
| D6 | Trail mechanics: when the bracket edit would be sent (time to +0.5R) and the `bracket_trail_amount` values, per symbol. | `shadow_exits.armed_at`, `trail_amount` |
| D7 | Self-check: the baseline shadow agrees with the real (paper) position on every trade. Any disagreement is a defect. | `shadow_exits` vs `positions` |
| D8 | Gates: how often each would fire at $250 / 2%, and whether the latch fires. | `risk_events`, `/api/risk` |

## What this cannot conclude

**Whether any exit rule has an edge.** At ~100 trades the standard error on mean R is ~0.17R (baseline)
and ~0.09R (trail); the differences measured in nine months of backtest are 0.02–0.06R. **No P&L
figure from this run is evidence of edge or of its absence.** Nor can it say how Delta fills, attaches
brackets or runs its own trailing stop: no order is sent. Those need the testnet probe and, if
chosen, the real-money stage.

## Decisions it feeds

- **Whether to run real money at all**, and with which exit, decided by the owner from D1–D8 with the
  limits above stated.
- **Defects** (any one stops the run, is fixed, and restarts under a new experiment id): a venue write
  attempted; D7 disagreement; journal rows missing for any position; the bot trading a universe other
  than BEAT/AKE/BANK; sizing from equity other than $250.

## Amendment rule

Nothing above the line below changes once the experiment is registered. Additions go underneath,
dated: the experiment id, image SHA, hashes, registration time, the host's EIP (never the key).

<!-- FROZEN ABOVE THIS LINE -->

## Addendum 2026-10-02 — registration record

Recorded after the stack came up; nothing above changed.

| field | value |
|---|---|
| experiment | `DRY-MANUAL_SCALP_BOTH_T3-5-20261002-ed31cba` |
| registered | 2026-10-02 10:39:17 UTC (4:09 PM IST) |
| image | `ed31cba` (#101 on master) |
| strategy hash | `41e764beceaf787f4b54ec25106b4c366e375478f4dbf3660d1a3b20c686f88d` |
| risk hash | `15f132654e7f4374` |
| execution hash | `07cf8612d9ade35c` (paper profile) |
| host | `i-0a38139027a767607`, EIP `15.207.211.127` (the read-only key is allowlisted to it) |
| sizing at start | equity 250, risk 0.02, `min_contract_risk_cap` 0.05; gates 0.20 / 0.10 / 8 |
| database | `deltabt_dryrun`, IAM-token login |
| venue writes | 0 at registration; all three shadow rules present in the running image |

The three symbols bound at start; BANKUSD began inside a stale-data halt (the flat-bar rule) and
resumes on its own when real bars arrive, as designed.

**Daily report** (#102): one screen, 7:00 AM IST, emailed to the alarm topic. Its self-check line is
D7 above: a closed position without a baseline shadow row is reported as needing attention.

## Addendum 2026-10-02 — defect in the first experiment; restart under a new id

**Defect (found by the first daily report, 11:08 UTC):** the paper broker could not fill an order the
minimum-contract floor had sized at one contract. Three AKEUSD entries (10:45, 10:55, 11:05 UTC) were
approved at 1 contract and passed every live check (`DRY_RUN_ORDER`), and the simulator opened none:
"fill at 0.0323 leaves no room inside the $10.88 risk budget". The order's risk amount was exactly
one contract's risk at the reference, so the broker's own 2 bps adverse slip cut the fill to zero
contracts every time. The run therefore held nothing a real bot would have held on AKEUSD, and D2
(AKEUSD under the floor rule) could never be measured. A real order would have gone through.

**Fix:** the order carries the floor's cap (5% of equity) and the fill-time resize measures against
it, only for floor-sized orders; every other order is unchanged. The daily report now flags any
symbol where the simulator opened fewer positions than the live checks passed.

**Per the defect rule:** `DRY-MANUAL_SCALP_BOTH_T3-5-20261002-ed31cba` is retired with **0 closed
positions**, so no data is lost. The run restarts on the fixed image under a new experiment id,
recorded below when it registers. The frozen section is unchanged, and the 21 days count from the
new registration.

## Addendum 2026-10-02 — the restarted experiment

| field | value |
|---|---|
| experiment | `DRY-MANUAL_SCALP_BOTH_T3-5-20261002-c4b719a` |
| registered | 2026-10-02 11:52:38 UTC (5:22 PM IST); the 21 days run to 2026-10-23 |
| image | `c4b719a` (#103 on master) |
| strategy hash | `41e764beceaf787f4b54ec25106b4c366e375478f4dbf3660d1a3b20c686f88d` (unchanged) |
| composite config hash | `3641376f54877289` (as logged at binding; risk and execution hashes are in `forward_test`) |
| host | unchanged: `i-0a38139027a767607`, EIP `15.207.211.127` |
| sizing and gates | unchanged: equity 250, risk 0.02, floor cap 0.05; 0.20 / 0.10 / 8 |

**A position carried over from the first experiment — counted (owner decision, 2026-10-02).** One
AKEUSD short opened at 11:30 UTC under `…-ed31cba` (the broken fill check let it through because the
price had moved in its favour by the fill) and was still open at the restart. The new process
restored it and protects it to its own stop or target. **It counts in this run:** the strategy,
sizing and gates are identical, and the fixed code would have opened it the same way (one contract,
risk $10.87 inside the $12.50 cap, fill within 0.25R). "Closed positions" in the stopping rule is read
as positions closed during this experiment, whenever they opened; the daily report counts them that
way and says how many were carried over. Its shadow rows land under this experiment's id. Its ladder
and trail legs were restarted at 11:52 UTC without the 11:30–11:52 path, and the running image
(`c4b719a`) predates the adoption fix below, so its rows say "observed from entry" although they
were not. Its baseline result does not depend on the path. While it is open it blocks new AKEUSD
entries, and its P&L moves the simulated $250 account, as it would a real account.

**Restored positions in general.** Until #104 the three-exit recorder marked a position restored
after a restart as observed from entry, though its path before the restart was never seen. From
#104 such rows carry `observed_from_entry = false`, and the daily report calls their ladder and
trail results approximate. The baseline result does not depend on the path.

## Amendment 2026-10-03 — three changes, by owner decision, after the first night

**Why.** The first night closed four trades: one BEATUSD win and three AKEUSD losses. Each AKEUSD
loss was ~$11.9 (one contract under the 5% floor, 4.4–4.5% of equity) against $5 for a normal
trade; the three together took 14% of the account, and AKEUSD produced 8 of the first 10 approved
entries. At $250 and 2% the pre-registered 20% latch is 10R of room; the paired backtest's
hold-to-3R drawdown was 42.7R with an 18-trade run of non-positive exits, so the latch was likely
to end the run inside the 21 days with ~10 trades recorded — too few for D5. The owner chose to
keep the run alive and record all three exits for the full window.

**The three changes** (one PR; host replaced; new experiment id recorded below when it registers):

| # | change | before | after |
|---|---|---|---|
| 1 | AKEUSD one-contract floor (`min_contract_risk_cap`) | 5% of equity ($12.50) | **3%** ($7.50): a contract is taken only when AKEUSD's stop is under ~2.3% of price; every AKEUSD entry so far would have been skipped |
| 2 | the exit the simulated account runs | hold to 3R (`manual_scalp_both_t3`) | **trail** (`manual_scalp_both_t3_trail`, 0.5R behind the peak from +0.5R; same entry rule). Paired backtest: shallowest drawdown of the three (17.2R vs 42.7R), same ~zero mean |
| 3 | drawdown latch (`max_drawdown_pct`) | 20%, terminal | **50%** for the dry run only. The daily report states the day the plan's 20% WOULD have fired. Real money returns to 20% before any trading key exists |

**What this changes in the questions above.**
- **D5** is unchanged in content: every position is still shadowed under baseline, ladder and trail.
  Two mechanics change. (a) The entry stream is the trail's: the trail exits earlier than
  hold-to-3R, so the per-symbol slot frees sooner and more entries are taken; the comparison stays
  paired (identical entries for all three rules) but the entries are not the ones a hold-to-3R bot
  would have taken. (b) When the real (trail) position closes by its own stop or target, the
  hold-to-3R and ladder shadows keep running on ticks until each exits by its own rule, with the
  72 h time stop applied; they live only in the running process, so a restart while they are open
  loses them and the report shows "not recorded" for that trade. A time stop, flatten or halt of the
  real position still closes all three together.
- **D7** self-check: the **trail** shadow must agree with the real position on every trade.
- **D8**: the 20% latch is read as a date ("would have fired on …") instead of ending the run; the
  10% daily loss, 8 consecutive losses and 72 h time stop are unchanged.
- **D2**: AKEUSD under the 3% floor — expected to be skipped on most days at current volatility.
- **Stopping point**: 100 closed positions, 21 days from the new registration, the 50% latch, or a
  defect. Positions closed during the run count whenever they opened (addendum above).

**What it does not change.** No exit rule is claimed to have an edge; the trail is chosen for the
shape of its drawdown, not its mean. Nothing above the FROZEN line is edited.

## Addendum 2026-10-03 — the amended experiment

| field | value |
|---|---|
| experiment | `DRY-MANUAL_SCALP_BOTH_T3_TRAIL-5-20261003-5683929` |
| registered | 2026-10-03 11:55:35 UTC (5:25 PM IST); the 21 days run to 2026-10-24 |
| image | `5683929` (#108 on master; carries #107) |
| strategy | `manual_scalp_both_t3_trail@5m`, hash `cf9917a73c61c14dd0435d1aa56d4e15fa29d5ddaa7e6bc3cde39b7cccf96018` |
| risk hash | `c530af7d32125746` |
| recorded risk | equity 250, risk 0.02, `min_contract_risk_cap` 0.03, `max_drawdown_pct` 0.50, daily loss 0.10, 8 consecutive losses, 72 h max hold |
| host | `i-0b59a7984b76f2154` (replaced by the amendment), EIP `15.207.211.127` (unchanged; the read-only key's allowlist) |
| retired | `DRY-MANUAL_SCALP_BOTH_T3-5-20261002-c4b719a`, by the deploy, through the database (#108) |

The bot was down from 11:23 to 11:55 UTC while the host was replaced and the deploy fixed (#108); ticks
in that window were not seen. Two positions opened under the previous experiment (a BEATUSD long and an
AKEUSD long) were restored and count in this run (same entry rule; addendum 2026-10-02). They were
opened under hold-to-3R, are now exited by the trail, and their shadow rows are marked observed from
entry = false (restored after a restart). The simulated account restarts at $250, as every new
experiment does; the previous experiment ended at $229.38.

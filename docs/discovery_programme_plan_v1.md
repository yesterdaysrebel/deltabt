# Discovery programme — draft plan v1 (2026-10-13)

**Status: DRAFT for the owner's review. Nothing here is approved or pre-registered.** When agreed, each
stage gets its own pre-registration file before it runs, as every test since 9 Oct has.

## 1. What this is, in one paragraph

Thirty-five tests since 5 Oct, and thirteen before that, have looked for an edge on Delta India by
testing setups people had already written down. All failed on data they had not seen, except two
breadth effects the owner does not want to trade. This programme does the opposite: it lets the data
show where this venue has structure (its clocks, its cross-venue gap, its listings, its option
surface), lets a model search that structure freely, and only calls something an edge after it has
survived two sets of data the search never touched and an outside review panel. Odds are low and are
stated at every stage. If it comes back empty, the programme ends and that is the answer.

## 2. Constraints this plan is built around

| constraint | consequence |
|---|---|
| selective trading, one (or few) positions at a time, capital idle most of the time | every candidate is judged in its one-position-at-a-time form; breadth-only effects are reported, not pursued |
| $250–500 account on Delta India, 0.158 % taker round trip, 0.0236 % maker | horizons of hours to days only; cost per R stated for every candidate |
| ₹0 compute by default; heavy runs on a rented box, never here | CPU, gradient boosting and clustering; a sequence model only if earned, on AWS for a day |
| nothing goes near a bot without the owner's yes | the live dry run is untouched throughout; a survivor gets its own stack |
| no results read before rules are written | pre-registration committed before each run; step-0 counts before outcomes |

## 3. Scope — two searches, one discipline

### 3a. Perps (first; data on disk)
Structure pass (measure, no trades): Delta-vs-Binance lead/lag per coin at 1 minute; behaviour around
the 12:00 UTC options settlement; around 8h funding settlements; a listing's first hours; the
Delta–Binance price gap and its reversion. Output: a findings note with effect sizes.
Pattern pass (unsupervised): minute-level paths of the 2 days before every move > 2× the coin's normal
range, clustered; recurring shapes written down as candidate rules.
Model pass (supervised): predict each coin's 3 / 5 / 10-day move at every day from raw and venue inputs
(path shape, settlement clocks, cross-venue gap, OI and positioning, flow, funding, listing age, own
recent results). Candidates = the model's most confident picks as real trades.

### 3b. Options (second; needs two fetches)
Delta-vs-Deribit implied-vol relative value per strike and expiry; the settlement clock (232 cached
expiries with max-pain and OI, never studied; near-expiry behaviour); skew and term-structure
inconsistencies within Delta's own surface. Direction-dependent option trades are excluded (direction is
the thing already shown unpredictable).

## 4. Data

| data | have | need | size | source |
|---|---|---|---|---|
| Delta 1m LTP, mark, funding, 32 coins, Jun 2024–Sep 2026 | yes | — | 1.5 GB | public API |
| Binance 1m volume, taker flow, 5m OI and positioning | yes | — | 1.1 GB | data.binance.vision |
| Binance 1m **prices** | no | refetch (20 min) | 1.5 GB | data.binance.vision |
| Delta listings, first 45 days + later block, 177 perps | yes | — | 1.2 GB | public API |
| Delta daily ATM straddles 2024–26, pin sample, term structure | archive branch | copy | small | repo |
| Delta option surface history (ATM ± n strikes, daily expiries) | no | fetch (owner lifts the old scope rule) | few GB | public API |
| Deribit option trades / IV for matching expiries | no | fetch | ~1 GB | Deribit public API |
| Binance daily prices 2019–2024, majors (final hold-out) | no | fetch (minutes) | MB | data.binance.vision |

Every fetch writes a manifest (time, rows, hashes) that is committed; raw data stays gitignored and is
rebuildable from committed scripts.

## 5. Models

- **Search:** LightGBM (tabular, NaN-tolerant, fast on CPU, finds unspecified interactions; pipeline
  already validated with planted signals). Threads pinned (`n_jobs=8`) — two OpenMP runtimes in one
  process made fits 200× slower on 12 Oct.
- **Discovery:** clustering of normalised paths; every cluster must be expressible as a readable rule
  before it is scored.
- **Conditional:** a small sequence model on minute paths only if the trees find signal; rented box.
- **Panel (via OpenRouter):** three or four strong models from different families, roles split —
  leakage/statistics reviewer, code reviewer, devil's advocate, cheap breadth brainstormer. They see
  text only (pre-registrations, code, summary tables, committed trade tables); never raw data or keys.

## 6. Stages and gates

| stage | what | gate to pass | artifact | approver |
|---|---|---|---|---|
| S0 | fetch Binance prices; copy archive samples; panel brainstorm of what to measure | manifests committed; panel notes filed | `fetch_manifest.json`, `panel/S0.md` | owner (fetch) |
| S1 | structure pass on the DEV window | findings note with effect sizes and noise estimates; panel reads it | `docs/discovery_findings_v1.md` | — |
| S2 | pattern pass + model pass on DEV; up to two candidates written as rules | each candidate has a mechanism, a rule, costs, a control, a pass bar | `docs/<candidate>_prereg.md` | — |
| S3 | panel review of each pre-registration | holes fixed or candidate dropped | `panel/<candidate>.md` | — |
| S4 | run candidate on DEV (in-sample) | planted ≥ 0.52 and shuffled random; net > 0 after costs; beats random control p ≤ 0.05; day-clustered t ≥ 2 | result file + trade tables | — |
| S5 | confirm on OLD, rules frozen | same bars, untouched window | result file | — |
| S6 | panel verification of S4–S5 from committed tables and code; hand recomputation of sample trades | no unexplained discrepancy | `panel/<candidate>_verify.md` | — |
| S7 | final hold-out (Binance 2019–2024 for perp candidates on majors; forward months for options) | same bars | result file | — |
| S8 | dry run on its own stack, stopping rule fixed in advance; parity self-check daily | 100 trades or 21 days; matches backtest on the same signals | prereg amendment, daily report | **owner** (spend, deploy) |
| S9 | options search (3b), same S1–S7 | — | — | owner (fetches) |

A candidate that fails any gate is closed and logged; it is not tuned and re-run.

## 7. Verification matrix (what is checked, how, by whom)

| point | check | evidence |
|---|---|---|
| data provenance | manifests with hashes; rebuild scripts | committed |
| data integrity | gaps, duplicates, UTC boundaries, cross-series agreement, filler bars dropped, listing dates, Delta–Binance sanity band | step-0 prints |
| inputs | computed only from data closed before the decision instant; feature table committed | `features_*.csv.gz` |
| leakage | shuffled labels must be random; pre-reg reviewer reads code | result file, panel note |
| sensitivity | planted signal must be recovered | result file |
| out-of-sample | monthly walk-forward; never scored on training months | code |
| judged as trades | real costs, stops, funding, slippage (2× stress reported); random control with the same trade structure | result file |
| stability | 5 seeds averaged; by-month and by-coin tables | result file |
| replication | OLD untouched until S5; final hold-out at S7 | result files |
| reproducibility | rerun matches byte for byte; versions pinned | `versions.json` |
| hand check | sample trades recomputed from prices | pre-flight log |
| multiple comparisons | one register of every candidate, pass or fail | `docs/discovery_register.md` |
| readiness | sizing at $250/$500 vs contract minimums; liquidity floor; one-at-a-time form | result file |
| reliability | dry run with parity self-check; decay watch monthly; two months outside range → review, not tweak | daily report |

## 8. Pre-registration template (every candidate)

Mechanism (why it should exist on this venue) · data and windows · exact rule (entry, stop, exit,
costs, position limit) · control · pass bar · power statement · what is reported but not judged ·
conduct (one run; bug → fix, log, rerun, report both).

## 9. Hosting

Research: this container (CPU, 50 GB RAM, ~12 GB disk free), data gitignored, scripts and results
committed. Heavy step (sequence model, full option surface): one AWS instance for a day, owner's yes.
Panel: OpenRouter, key in an environment variable, never in the repo. Survivor: its own dry-run stack
on AWS beside the current one.

## 10. Time and money

| item | time | cost |
|---|---|---|
| S0–S2 perps | ~4 days | ₹0 |
| S3, S6 panel rounds | hours | ₹200–1,000 per round, 3–4 rounds |
| S4–S7 | ~2 days | ₹0 |
| S9 options | ~4 days + fetches | ₹0 (+ a rented day if the surface is large) |
| S8 dry run | 21 days | ~₹250–300/month for the stack |

## 11. Stop rules

- S1 finds no structure above noise → stop after S1, report, end.
- No candidate clears S4 on DEV → stop.
- A candidate clears DEV and fails OLD → closed; if a second candidate also does, stop the perp search.
- The options search runs only if the owner lifts the archived scope rule and approves the fetches.
- The programme ends when both searches are closed or one survivor reaches S8.

## 12. Record

Everything in git on the research branch: this plan, each pre-registration, panel notes, result
tables, manifests, the register of candidates, CONTEXT.md updates. Memory notes carry the state across
container rebuilds.

## 13. Decisions needed from the owner

1. Approve S0 (Binance price refetch, archive copies).
2. Provide an OpenRouter key as an environment variable, and a per-round spending cap.
3. Lift the archived options scope rule and approve the option-surface and Deribit fetches (for S9).
4. Confirm the account size the readiness checks should use ($250 or $500).
5. Confirm the constraint set in §2 is complete and correct.

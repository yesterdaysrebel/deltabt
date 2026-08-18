# Paper runner — readiness

Per §16, the long-running runner was **not** started by this task.

---

## 1. Principal finding

**The paper-trading runner already exists, is tested, and is currently running.**

It is `app/`, deployed on AWS since 2026-08-15 as experiment
`H-WPR-1-PAPER-AWS-V3-20260815-2`, day 3 of a planned 30. Yesterday's automated
report: container healthy 22h, 0 restarts, 0 alarms, 0 application errors,
1,399 evaluations, 43 orders, 27 fills, 11 closed trades, worst feed silence
3.8s against a 120s escalation bar.

No second runner was built. Building one would create a parallel system able to
diverge from the one holding live state.

## 2. Strategy identified

    name             H-WPR-1-VariantA-WideStop  (variant V3_WIDE_STOP)
    module           app/strategy/rules.py
    configuration    app/config/variants.py
    strategy hash    11461f2a11a96f8a
    config hash      2b4b19598a47d1e9   (strategy + risk + execution + symbols)
    app version      1.0.0-paper

The strategy hash matches the value the deployment workflow already pins, so
this checkout and the running experiment are the same strategy.

## 3. Paper execution model

    broker        app/execution/paper_broker.py
    assumption    TAKER — the assumption under which this strategy was evaluated
    stop trigger  MARK price (Delta's default), fills at LTP + slippage + taker fee
    target        resting limit, fills at LTP, maker fee, no slippage
    slippage      2.0 bps of notional, taker legs only
    fees          per-symbol from the exchange catalog, x1.18 GST
    funding       snapshot-based, per-symbol cadence

The recent maker research was **not** applied. §6 requires the existing
evaluated assumption, and that is taker.

## 4. Live-order safety audit

| check | result |
|---|---|
| order-placement code anywhere in repo | **none** |
| trading credentials in the process | **none** |
| signing libraries (`hmac`, `ecdsa`, `nacl`) | **none** |
| mutating HTTP verbs on the exchange adapter | **none** |
| exchange adapter capability | market data only |
| paper broker isolation | separate `PaperBroker` interface |
| enforcement | AST scan over every shipped `*.py` |

`tests/live/test_no_live_trading.py` — **915 passed, 2 skipped.**

The boundary is the *absence of the capability*, not a runtime toggle. See the
blocker below.

## 5. Test results

    full suite                2,116 passed, 57 skipped
    live subset               1,765 passed, 57 skipped

    test_no_live_trading        915 passed, 2 skipped
    test_deployment_safety      127 passed
    test_paper_execution         50 passed
    test_recovery                41 passed
    test_experiment_identity     39 passed, 3 skipped
    test_order_lifecycle         35 passed, 2 skipped

No existing test was weakened, deleted or skipped.

## 6. Requirement coverage

| § | requirement | status |
|---|---|---|
| 4 | paper-only architecture | already implemented |
| 5A/5B | `PAPER_MODE` flag | **BLOCKED — see §7** |
| 5C–5G | no live-order path, separate broker, no credentials, tests | satisfied |
| 6 | deterministic documented fill model, taker | satisfied |
| 7 | signal timing, no look-ahead, warm-up, parity tests | satisfied |
| 8 | position engine, P&L, equity, drawdown | satisfied |
| 9 | append-only event journal | satisfied (Postgres repository) |
| 10 | restart safety, hash verification, fail closed | satisfied (`ConfigurationDrift`) |
| 11 | observability, stale data, disconnect, gaps | satisfied |
| 13 | status report | `forward-test report` + `scripts/daily_report.py` |
| 3 | strategy manifest | **created** — `config/paper_strategy_manifest.json` |
| 2 | strategy audit | **created** — `reports/paper_runner_strategy_audit.md` |
| 16 | readiness report | **this document** |

## 7. Blocker requiring an explicit decision

§5A and §5B ask for a `PAPER_MODE` flag defaulting to true, with the runner
refusing to start when it is false.

**`PAPER_MODE` is in the existing `FORBIDDEN_FLAGS` set in `app/safety.py`.**
Adding it would fail `tests/live/test_no_live_trading.py`, which §14 forbids
weakening — and would *reduce* the guarantee, because a flag implies a live mode
exists and is being suppressed, where today there is no order-placement code to
suppress.

I did not add it, and I did not weaken the test to accommodate it. The intent of
§5A/§5B is already met by a stronger mechanism. **This needs your decision
before anything changes.**

## 8. Known limitations of the execution model

Carried from the existing implementation, not introduced here:

1. **Fills are simulated against observed market data.** No real order has ever
   been placed, so no production fill rate exists.
2. **Replay path resolves same-bar stop/target pessimistically** (stop first),
   because bar data cannot order the high and the low. The live tick path
   observes the true order.
3. **Passive entries carry a same-bar look-ahead guard** — a target cannot be
   booked on the bar a passive order filled on. Added after a measured 356:1
   asymmetry.
4. **No time stop.** Only stop and target close a position, so an unreachable
   target is held indefinitely. This is strategy behaviour, unchanged.
5. **Position lock discards most setups.** Yesterday: 108 of 125 rejections were
   "already holding an open position". Observation only; not modified.
6. **Contract rounding is down**, so realised risk is at or below budget. At
   small account sizes this quantisation becomes material.

## 9. Exact command to start paper trading

The runner is already running under systemd on the deployed instance. Nothing
needs starting for the current experiment to continue.

To run locally against live market data:

    # 1. gate — changes nothing
    PYTHONPATH=. python -m app.cli forward-test preflight

    # 2. register an experiment (only for a NEW run)
    PYTHONPATH=. python -m app.cli forward-test start

    # 3. run the bot
    PYTHONPATH=. python -m app.cli run

    # status / reports
    PYTHONPATH=. python -m app.cli forward-test status
    PYTHONPATH=. python -m app.cli forward-test report --day YYYY-MM-DD

Requires `DATABASE_URL` (Postgres). Variant selection is `DELTABOT_VARIANT`;
the deployed run uses `V3_WIDE_STOP`.

**Do not run `forward-test start` against the live database** — it would
register a second experiment alongside the one currently running.

## 10. Statement required by §17

Successful implementation is **not** evidence that the strategy has an edge. The
running experiment is at −2.08R over 11 closed trades and its own report
declares **INSUFFICIENT SAMPLE**. What is validated is execution correctness,
risk enforcement, persistence and restart safety — not profitability.

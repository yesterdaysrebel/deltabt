# H-WPR-1-PAPER-FROZEN-1M-20260818 — experiment journal

Separate journal for the frozen-semantics paper arm. **Not merged with V3's.**

    experiment_id     H-WPR-1-PAPER-FROZEN-1M-20260818
    strategy          H-WPR-1-FROZEN-1M  (Arm A, 1m decides / 5m regime)
    strategy_hash     e63d00ad683ec9c8
    risk_hash         89f939adcd0a8567
    execution_hash    f39439e8918b96c7
    composite         f8500fef82ef6494
    symbols           BTCUSD ETHUSD SOLUSD BEATUSD BANKUSD AKEUSD
    max_stop_pct      0.05   (frozen research value)
    target            2R of the 1m structural stop
    max_hold          86400s (24h)
    mode              PAPER ONLY

## Entries

**2026-08-18 — created, NOT started.**
Evaluator built and parity-verified against `deltabt/research/hwpr.py`
(450/450 signal bars, 450/450 quiet bars, bit-identical stops, 3 symbols).
Identity recorded. Paper safety verified. **No process launched**; the 1m
runner wiring does not exist yet — see §11.1 of
`reports/hwpr1_frozen_paper_readiness.md`.

No signals. No orders. No fills. No positions. No P&L.

Nothing below this line until an operator authorises a start.

**2026-08-18 — runner wiring built. Still NOT started.**
1m decision path wired (`on_closed_1m_frozen`), `_process_explanation`
extracted from V3's 5m callback verbatim so the two arms share one pipeline.
Variant `FROZEN_1M` resolves to this arm; `ALL` untouched, V3 hash still
`11461f2a11a96f8a`. 24h time exit enabled for this arm only. 17 end-to-end
tests added; full suite 1712 passed, 57 skipped; safety 635 passed.

**No process launched. No orders. No fills. No positions. No P&L.**

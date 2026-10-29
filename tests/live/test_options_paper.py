"""The option-selling paper account (live/options_paper.py) against the lab's definitions."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np

from live import options_paper as O

ROOT = Path(__file__).resolve().parents[2]


def test_strikes_are_the_labs_grid_order():
    sys.path.insert(0, str(ROOT / "scripts"))
    import option_selling_lab as L
    assert O.UNDERLYINGS["BTC"]["grid"] == L.UNDERLYINGS["BTC"]["grid"] and O.M in L.MS
    # 2026-10-28 live snapshot: BTC 80,540.4 -> put 79,800, call 81,400; ETH 2,331.9 -> 2,310 / 2,360
    assert O.strike_order(80540.4, "P", 200)[:3] == [79800, 80000, 79600]
    assert O.strike_order(80540.4, "C", 200)[0] == 81400
    assert O.strike_order(2331.9, "P", 10)[0] == 2310 and O.strike_order(2331.9, "C", 10)[0] == 2360
    assert len(O.strike_order(1.0, "P", 1)) == 9


def test_the_sale_is_for_tomorrows_noon_expiry():
    now = dt.datetime(2026, 10, 29, 12, 0, 40, tzinfo=dt.timezone.utc).timestamp()
    e = O.sale_expiry(now)
    assert dt.datetime.fromtimestamp(e, dt.timezone.utc) == dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc)
    assert O.expiry_tag(e) == "301026"


def test_leg_pnl_is_the_labs_formula_with_the_real_fill_and_settlement():
    sys.path.insert(0, str(ROOT / "scripts"))
    import option_selling_lab as L
    spot, cv, mark, strike, settle = 80540.4, 0.001, 269.66, 79800.0, 79000.0
    intrinsic = max(strike - settle, 0)
    prem = mark * L.HAIRCUT * cv
    notional = spot * cv
    lab = (prem - intrinsic * cv - np.minimum(L.FEE_NOTIONAL * notional, L.FEE_PREMIUM_CAP * prem)
           - np.minimum(L.FEE_NOTIONAL * notional, L.FEE_PREMIUM_CAP * intrinsic * cv))
    got = O.leg_pnl(mark * L.HAIRCUT, intrinsic, spot, cv)
    assert abs(got["pnl"] - lab) < 1e-12
    win = O.leg_pnl(268.0, 0.0, spot, cv)                 # expired worthless: keep the premium less one fee
    assert abs(win["pnl"] - (0.268 - min(0.0001 * 80.5404, 0.035 * 0.268))) < 1e-12 and win["fee_out"] == 0


def _opts(**kw):
    o = {"strangles": 4, "wins": 3, "pnl": 1.23, "equity": 251.23, "mean_bps": 21.0, "worst": -0.9,
         "legs_sold": 8, "bid_over_mark": 0.992, "spread_over_mark": 0.015, "skipped": 0,
         "journal_fields": ["expiry", "underlying", "sold", "spot", "put", "call", "premium", "paid", "fees",
                            "pnl", "status"],
         "journal": [[1793448000, "BTC", 1793361600, 80600.0, 79800.0, 81400.0, 0.52, 0.0, 0.0081, 0.5119, "SETTLED"],
                     [1793534400, "BTC", 1793448060, 81000.0, 80200.0, 81800.0, 0.49, None, 0.0081, None, "OPEN"]],
         "journal_omitted": 0,
         "open": [{"symbol": "P-BTC-79800-011126", "bid": 268.0, "expiry": "2026-11-01T12:00:00+00:00"}],
         "days": [{"expiry": "2026-11-01", "status": "ENTERED"}]}
    o.update(kw)
    return o


def test_the_report_shows_the_account_and_flags_a_missed_sale_or_a_stuck_leg():
    sys.path.insert(0, str(ROOT / "scripts"))
    import brief_report as B
    now = dt.datetime(2026, 11, 1, 1, 30, tzinfo=dt.timezone.utc)
    text = "\n".join(B.options_lines(_opts()))
    assert "99% of mark" in text and "79800" in text and "+0.51" in text and "settled" in text
    assert text.index("open") < text.index("settled"), "newest strangle first"
    acct = "\n".join(B.accounts_lines({"accounts": {}}, _opts()))
    assert "$251.23" in acct and "75%" in acct
    assert B.options_problems(_opts(), now) == []
    assert "MISSED" in B.options_problems(_opts(days=[{"expiry": "2026-11-01", "status": "MISSED"}]), now)[0]
    late = now + dt.timedelta(hours=14)
    assert "not settled" in B.options_problems(_opts(), late)[0]
    assert B.options_problems(None, now) == [] and "Not started" in "\n".join(B.options_lines(None))

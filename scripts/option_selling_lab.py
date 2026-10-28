"""Selling daily BTC/ETH strangles -- docs/option_selling_prereg.md (committed before this file
and before any option price was fetched).

    PYTHONPATH=. .venv/bin/python scripts/option_selling_lab.py fetch   # perp 1h + option marks (resumable)
    PYTHONPATH=. .venv/bin/python scripts/option_selling_lab.py build   # 2025: chooses m
    PYTHONPATH=. .venv/bin/python scripts/option_selling_lab.py test    # 2026, ONCE, after build is in CONTEXT.md
"""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OUT = Path("out/options")
H = "https://api.india.delta.exchange/v2/history/candles"
TS = lambda s: int(pd.Timestamp(s, tz="UTC").timestamp())  # noqa: E731
DAY, HOUR = 86400, 3600
FIRST, BUILD_END, LAST = TS("2025-01-15"), TS("2026-01-01"), TS("2026-10-27")
UNDERLYINGS = {"BTC": dict(perp="BTCUSD", cv=0.001, grid=200), "ETH": dict(perp="ETHUSD", cv=0.01, grid=10)}
MS = (0.01, 0.02, 0.03, 0.04, 0.05)
HAIRCUT, FEE_NOTIONAL, FEE_PREMIUM_CAP = 0.90, 0.0001, 0.035
_lock = threading.Lock()
_last = [0.0]


def _get(params, tries=4):
    for k in range(tries):
        with _lock:                                   # ~6 requests/s across threads
            wait = _last[0] + 0.17 - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            r = requests.get(H, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (k + 1))
                continue
            return r.json().get("result", []) or []
        except Exception:                             # noqa: BLE001
            time.sleep(2 * (k + 1))
    return None


def perp_1h(sym: str) -> pd.DataFrame:
    p = OUT / f"{sym}_1h.parquet"
    if p.exists():
        return pd.read_parquet(p)
    rows, end = [], LAST + DAY
    while end > FIRST - 3 * DAY:
        start = max(FIRST - 3 * DAY, end - 3999 * HOUR)
        got = _get({"symbol": sym, "resolution": "1h", "start": start, "end": end}) or []
        rows += got
        end = start - HOUR
    d = pd.DataFrame(rows).drop_duplicates("time").astype({"time": "int64", "close": float}).sort_values("time")
    d[["time", "open", "high", "low", "close"]].to_parquet(p, index=False)
    return d


def tag(day: int) -> str:
    return pd.to_datetime(day, unit="s", utc=True).strftime("%d%m%y")


def mark_at(sym: str, t: int):
    """MARK close of the 1h bar that CLOSES at t (bar open t - 1h); None if no history."""
    got = _get({"symbol": "MARK:" + sym, "resolution": "1h", "start": t - 2 * HOUR, "end": t})
    if got is None:
        return "error"
    for x in got:
        if int(x["time"]) == t - HOUR and x.get("close") is not None:
            return float(x["close"])
    return None


def _job(args):
    und, day, m, kind, spot = args
    g = UNDERLYINGS[und]["grid"]
    target = spot * (1 - m) if kind == "P" else spot * (1 + m)
    k0 = int(round(target / g) * g)
    entry = day - DAY + 12 * HOUR
    for n in (0, 1, -1, 2, -2, 3, -3, 4, -4):
        k = k0 + n * g
        sym = f"{kind}-{und}-{k}-{tag(day)}"
        px = mark_at(sym, entry)
        if px == "error":
            return dict(und=und, day=day, m=m, kind=kind, error=True)
        if px is not None:
            return dict(und=und, day=day, m=m, kind=kind, strike=k, symbol=sym, mark=px, spot=spot)
    return dict(und=und, day=day, m=m, kind=kind, strike=None)


def cmd_fetch() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    store = OUT / "marks.jsonl"
    done = set()
    if store.exists():
        for line in store.read_text().splitlines():
            x = json.loads(line)
            if not x.get("error"):
                done.add((x["und"], x["day"], x["m"], x["kind"]))
    jobs = []
    for und, u in UNDERLYINGS.items():
        p = perp_1h(u["perp"]).set_index("time").close
        for day in range(FIRST, LAST + DAY, DAY):
            t = day - DAY + 11 * HOUR                       # 1h bar closing at 12:00 the day before
            if t not in p.index:
                continue
            for m in MS:
                for kind in ("P", "C"):
                    if (und, day, m, kind) not in done:
                        jobs.append((und, day, m, kind, float(p.loc[t])))
    print(f"{len(done)} cached, {len(jobs)} to fetch", flush=True)
    with ThreadPoolExecutor(4) as ex, store.open("a") as f:
        for k, res in enumerate(ex.map(_job, jobs), 1):
            f.write(json.dumps(res) + "\n")
            if k % 500 == 0:
                f.flush()
                print(f"  {k}/{len(jobs)}", flush=True)


def table() -> pd.DataFrame:
    x = pd.DataFrame([json.loads(l) for l in (OUT / "marks.jsonl").read_text().splitlines()])
    x = x[x.get("error", pd.Series(False, index=x.index)).fillna(False) == False]  # noqa: E712
    x = x.drop_duplicates(["und", "day", "m", "kind"], keep="last")
    rows = []
    for und, u in UNDERLYINGS.items():
        p = perp_1h(u["perp"]).set_index("time").close
        sub = x[x.und == und].copy()
        sub["settle"] = sub.day.map(lambda d: p.get(d - HOUR + 0, np.nan) if (d - HOUR) in p.index else np.nan)
        sub["cv"] = u["cv"]
        rows.append(sub)
    t = pd.concat(rows, ignore_index=True)
    t = t[t.strike.notna() & t.settle.notna()].copy()
    intrinsic = np.where(t.kind == "P", np.maximum(t.strike - t.settle, 0), np.maximum(t.settle - t.strike, 0))
    prem = t.mark * HAIRCUT * t.cv
    notional = t.spot * t.cv
    fee_in = np.minimum(FEE_NOTIONAL * notional, FEE_PREMIUM_CAP * prem)
    settle_val = intrinsic * t.cv
    fee_out = np.minimum(FEE_NOTIONAL * notional, FEE_PREMIUM_CAP * settle_val)
    t["pnl"] = prem - settle_val - fee_in - fee_out
    t["bps"] = 1e4 * t.pnl / notional
    t["notional"] = notional
    return t


def strangles(t: pd.DataFrame, m: float) -> pd.DataFrame:
    s = t[t.m == m].pivot_table(index=["und", "day"], columns="kind", values=["pnl", "bps", "notional"], aggfunc="first")
    s = s.dropna()
    out = pd.DataFrame({"pnl": s[("pnl", "P")] + s[("pnl", "C")],
                        "bps": 1e4 * (s[("pnl", "P")] + s[("pnl", "C")]) / s[("notional", "P")],
                        "put_bps": s[("bps", "P")], "call_bps": s[("bps", "C")]}).reset_index()
    return out


def describe(s: pd.DataFrame, label: str) -> str:
    by_day = s.groupby("day").bps.mean()
    from scipy import stats
    tt = by_day.mean() / (by_day.std(ddof=1) / np.sqrt(len(by_day)))
    p = float(stats.t.sf(tt, len(by_day) - 1))
    lines = [f"{label}: {len(s)} strangles over {s.day.nunique()} days | win {100*(s.pnl > 0).mean():.0f}% | "
             f"mean {s.bps.mean():+.2f} bps of notional (one-sided p {p:.3f}) | avg win {s[s.bps > 0].bps.mean():+.2f} bps, "
             f"avg loss {s[s.bps <= 0].bps.mean():+.2f} bps, worst {s.bps.min():+.1f} bps"]
    for und in UNDERLYINGS:
        u = s[s.und == und]
        if len(u):
            lines.append(f"    {und}: {len(u)} | win {100*(u.pnl > 0).mean():.0f}% | mean {u.bps.mean():+.2f} bps | "
                         f"${u.pnl.sum():+.2f} total for one strangle a day | puts {u.put_bps.mean():+.2f} calls {u.call_bps.mean():+.2f} bps | worst ${u.pnl.min():+.2f}")
    return "\n".join(lines), p


def cmd_build() -> None:
    t = table()
    b = t[t.day < BUILD_END]
    lines = [f"BUILD 2025: option rows {len(b)} (strike found for {100*b.strike.notna().mean():.0f}%)"]
    best = None
    for m in MS:
        s = strangles(b, m)
        txt, _ = describe(s, f"m = {int(100*m)}%")
        lines.append(txt)
        w = (s.pnl > 0).mean()
        if w >= 0.70 and (best is None or s.bps.mean() > best[1]):
            best = (m, s.bps.mean())
    lines.append(f"CHOSEN m = {best[0] if best else None} (highest mean bps with build win >= 70%)")
    (OUT / "m.txt").write_text(f"{best[0] if best else ''}\n")
    txt = "\n".join(lines)
    print(txt)
    (OUT / "build.txt").write_text(txt + "\n")


def cmd_test() -> None:
    m = float((OUT / "m.txt").read_text().strip())
    t = table()
    s = strangles(t[t.day >= BUILD_END], m)
    txt, p = describe(s, f"TEST 2026 (opened once), m = {int(100*m)}%")
    by_und = s.groupby("und").bps.mean()
    ok = ((s.pnl > 0).mean() >= 0.70, s.bps.mean() > 0 and p <= 0.05, bool((by_und > 0).all()))
    lines = [txt, f"PASS conditions: win >= 70% {ok[0]} | mean > 0 & p <= 0.05 {ok[1]} | BTC and ETH each > 0 {ok[2]} => "
                  f"{'PASS' if all(ok) else 'FAIL'}"]
    # $250 account: one BTC and one ETH strangle a day
    daily = s.groupby("day").pnl.sum().sort_index()
    eq = 250 + daily.cumsum()
    lines.append(f"$250 account, one BTC + one ETH strangle a day: -> ${eq.iloc[-1]:.0f}, lowest ${eq.min():.0f}, "
                 f"worst day ${daily.min():+.2f}, worst week ${daily.rolling(7).sum().min():+.2f}")
    txt = "\n".join(lines)
    print(txt)
    (OUT / "test.txt").write_text(txt + "\n")


if __name__ == "__main__":
    {"fetch": cmd_fetch, "build": cmd_build, "test": cmd_test}[sys.argv[1]]()

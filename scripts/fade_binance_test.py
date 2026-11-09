"""Rule A / QUICK / ladder on Binance USDT-M perpetuals 2021-2024 (docs/fade_binance_prereg.md).

    PYTHONPATH=. .venv/bin/python scripts/fade_binance_test.py fetch   # daily + picked 1m (public archive)
    PYTHONPATH=. .venv/bin/python scripts/fade_binance_test.py judge

Data goes to out/binance/ (parquet, gitignored). Archive: https://data.binance.vision (no key, no cost).
Trade and account rules are copied from scripts/fade_target_compare.py and scripts/fade_ladder_account.py;
nothing is tuned here.
"""
from __future__ import annotations

import io
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_ladder_account as LA  # noqa: E402

OUT = Path("out/binance")
ARCHIVE = "https://data.binance.vision/data/futures/um"
LISTING = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DAY = 86400
TS = lambda s: int(pd.Timestamp(s, tz="UTC").value // 10**9)   # noqa: E731
DAILY_FROM, PICK_FROM, PICK_TO = "2020-12", TS("2021-01-15"), TS("2025-01-01")
MONTHS = pd.period_range("2020-12", "2024-12", freq="M").strftime("%Y-%m").tolist()
EXCLUDE = re.compile(r"_|BTCDOM|DEFI|FOOTBALL|BLUEBIRD")
MIN_TURNOVER, K_STOP, MAX_DIST, FEE, QUICK_TP = 250_000.0, 2.0, 0.325, 0.0005, 0.2
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count",
        "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def get(url: str, tries: int = 4) -> bytes | None:
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.parse.quote(url, safe=":/?&="), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (i + 1))
        except Exception:                                            # noqa: BLE001
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"failed: {url}")


def read_zip(blob: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        raw = z.read(z.namelist()[0]).decode()
    first = raw.split("\n", 1)[0]
    df = pd.read_csv(io.StringIO(raw), header=0 if first.startswith("open_time") else None)
    df.columns = COLS
    return df


def symbols() -> list[str]:
    out, marker = [], ""
    while True:
        x = get(f"{LISTING}?delimiter=/&prefix=data/futures/um/monthly/klines/&marker={marker}").decode()
        got = re.findall(r"<Prefix>data/futures/um/monthly/klines/([^/<]+)/</Prefix>", x)
        out += got
        if "<IsTruncated>true" not in x or not got:
            break
        marker = f"data/futures/um/monthly/klines/{got[-1]}/"
    return sorted(s for s in set(out) if s.endswith("USDT") and not EXCLUDE.search(s))


def daily_one(sym: str) -> pd.DataFrame:
    """One symbol's daily candles; a symbol that cannot be fetched is logged and left out (never fatal)."""
    parts = []
    for m in MONTHS:
        try:
            b = get(f"{ARCHIVE}/monthly/klines/{sym}/1d/{sym}-1d-{m}.zip")
        except Exception as exc:                                     # noqa: BLE001
            print(f"  skipped {sym} {m}: {exc}", flush=True)
            continue
        if b:
            parts.append(read_zip(b))
    if not parts:
        return pd.DataFrame()
    d = pd.concat(parts, ignore_index=True)
    d["symbol"] = sym
    d["time"] = d.open_time.astype("int64") // 1000
    return d[["symbol", "time", "open", "high", "low", "close", "quote_volume"]]


def panel(d: pd.DataFrame) -> pd.DataFrame:
    """Same readings as gainer_fade_2025_lab.panel(): change, ATR14% and turnover of D-1 only."""
    d = d.sort_values(["symbol", "time"]).drop_duplicates(["symbol", "time"]).reset_index(drop=True)
    d["change"] = d.close / d.open - 1
    pc = d.groupby("symbol").close.shift(1)
    tr = np.maximum.reduce([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()])
    d["atrp"] = pd.Series(tr, index=d.index).groupby(d.symbol).transform(lambda s: s.rolling(14).mean()) / d.close
    prev = d[["symbol", "time", "change", "quote_volume", "atrp"]].copy()
    prev["time"] += DAY
    prev.columns = ["symbol", "time", "prev_change", "prev_turnover", "atrp"]
    p = d[["symbol", "time", "open"]].merge(prev, on=["symbol", "time"])
    p = p[(p.time >= PICK_FROM) & (p.time < PICK_TO)]
    p["eligible"] = (p.prev_change > 0) & (p.prev_turnover >= MIN_TURNOVER) & np.isfinite(p.atrp) & (p.atrp > 0)
    picks = (p[p.eligible].sort_values(["time", "prev_change"], ascending=[True, False]).groupby("time").head(2))
    picks = picks.copy()
    picks["rank"] = picks.groupby("time").cumcount() + 1
    picks["dist"] = K_STOP * picks.atrp
    picks["year"] = pd.to_datetime(picks.time, unit="s").dt.year.astype(str)
    return picks.reset_index(drop=True)


def minute_one(x) -> dict | None:
    day = pd.to_datetime(x.time, unit="s").strftime("%Y-%m-%d")
    try:
        b = get(f"{ARCHIVE}/daily/klines/{x.symbol}/1m/{x.symbol}-1m-{day}.zip")
    except Exception as exc:                                         # noqa: BLE001
        print(f"  skipped 1m {x.symbol} {day}: {exc}", flush=True)
        return None
    if not b:
        return None
    m = read_zip(b)
    t = m.open_time.astype("int64").to_numpy() // 1000
    keep = (t >= x.time) & (t < x.time + DAY)
    h, lo, c = (m[k].to_numpy(float)[keep] for k in ("high", "low", "close"))
    if len(c) == 0:
        return None
    e, dist = float(x.open), float(x.dist)
    stop = e * (1 + dist)
    s = np.nonzero(h >= stop)[0]
    si = s[0] if len(s) else 10**9
    rec = {"symbol": x.symbol, "time": int(x.time), "year": x.year, "rank": int(x.rank), "dist": dist, "bars": len(c)}
    for name, tp in (("A", None), ("QUICK", QUICK_TP)):
        ti = 10**9
        if tp is not None:
            tg = e * (1 - tp * dist)
            q = np.nonzero(lo <= tg)[0]
            ti = q[0] if len(q) else 10**9
        if si <= ti and si < 10**9:
            xp, how = max(stop, c[si]), "stop"
        elif ti < 10**9:
            xp, how = tg, "target"
        else:
            xp, how = c[-1], "time"
        rec[name] = (-(xp / e - 1) - FEE - FEE * xp / e) / dist
        rec[name + "_how"] = how
    return rec


def cmd_fetch() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    dp = OUT / "daily.parquet"
    if dp.exists():
        d = pd.read_parquet(dp)
    else:
        syms = symbols()
        print(f"{len(syms)} USDT perpetual symbols in the archive (incl. delisted); daily candles ...", flush=True)
        frames = []
        with ThreadPoolExecutor(12) as ex:
            for k, f in enumerate(ex.map(daily_one, syms), 1):
                if len(f):
                    frames.append(f)
                if k % 50 == 0:
                    print(f"  {k}/{len(syms)}", flush=True)
        d = pd.concat(frames, ignore_index=True)
        for c in ("open", "high", "low", "close", "quote_volume"):
            d[c] = d[c].astype(float)
        d.to_parquet(dp, index=False)
    picks = panel(d)
    picks.to_parquet(OUT / "picks.parquet", index=False)
    todo = picks[picks.dist <= MAX_DIST]
    print(f"{d.symbol.nunique()} symbols with daily data; {len(picks)} picks, {len(todo)} with stop <= 32.5%; 1m ...", flush=True)
    rows = []
    with ThreadPoolExecutor(12) as ex:
        for k, rec in enumerate(ex.map(minute_one, todo.itertuples()), 1):
            if rec:
                rows.append(rec)
            if k % 250 == 0:
                print(f"  {k}/{len(todo)}", flush=True)
    pd.DataFrame(rows).to_parquet(OUT / "trades.parquet", index=False)
    print(f"{len(rows)} trades simulated ({len(todo) - len(rows)} picks without 1m data)")


def account(tr: pd.DataFrame, col: str, mode: str) -> dict:
    days = [(g.dist.to_numpy(float), g[col].to_numpy(float)) for _, g in tr.groupby("time")]
    if mode in LA.SIZINGS:
        return LA.simulate(days, LA.SIZINGS[mode])
    eq = peak = low = LA.START
    worst, lvl, ref, recent, hist = 0.0, 0, None, [], []
    levels = (0.10, 0.05, 0.03)
    by_dd = lambda u: 0 if u < 0.20 else 1 if u < 0.35 else 2      # noqa: E731
    for (dist, r), t in zip(days, sorted(tr.time.unique())):
        if mode == "rolling 90d":
            hi = max([e for tt, e in hist if tt >= t - 90 * DAY] + [eq])
            lvl = by_dd(1 - eq / hi)
        else:                                                        # "20 trades won >= 65%"
            want = by_dd(1 - eq / peak)
            if ref is None:
                lvl = want
            else:
                if eq < ref * 0.90:
                    lvl = min(lvl + 1, 2)
                    ref = eq if lvl < 2 else None
                if want < lvl:
                    lvl, ref = want, (None if want == 0 else ref)
            if lvl > 0 and len(recent) >= 20 and sum(v > 0 for v in recent[-20:]) / 20 >= 0.65:
                lvl, recent, ref = lvl - 1, [], eq
        k = levels[lvl]
        ks = np.minimum(k, LA.POSITION_CAP * dist)
        ks = ks * min(1.0, 1.0 / float(np.sum(ks / dist)))
        eq = max(eq + eq * float(np.sum(ks * r)), 0.0)
        recent += list(r)
        hist.append((t, eq))
        peak, low = max(peak, eq), min(low, eq)
        worst = max(worst, 1 - eq / peak)
    return {"end": eq, "low": low, "worst_drop": worst}


def cmd_judge() -> None:
    tr = pd.read_parquet(OUT / "trades.parquet").sort_values("time")
    tr = tr[tr.dist <= MAX_DIST]
    years = ["2021", "2022", "2023", "2024"]
    print(f"{len(tr)} trades, {tr.symbol.nunique()} coins; per year: "
          + ", ".join(f"{y} {int((tr.year == y).sum())}" for y in years))
    for col in ("A", "QUICK"):
        print(f"\n=== {col}: mean net R per trade ===")
        pos = 0
        for y in years:
            v = tr[tr.year == y][col]
            pos += v.mean() > 0
            print(f"  {y}: {v.mean():+.3f}R ± {v.std() / np.sqrt(len(v)):.3f}  win {100 * (v > 0).mean():.0f}%  "
                  f"stopped {100 * (tr[tr.year == y][col + '_how'] == 'stop').mean():.0f}%  n {len(v)}")
        v = tr[col]
        z = v.mean() / (v.std() / np.sqrt(len(v)))
        print(f"  pooled {v.mean():+.3f}R, z {z:+.2f}; years > 0: {pos}/4")
        print(f"  RULE {'1' if col == 'A' else '2'} ({col} has an edge): {'PASS' if pos >= 3 and z >= 2 else 'FAIL'}")

    print("\n=== accounts: $1,500 at the start of each year, rule A ===")
    modes = ["flat 3%", "flat 5%", "flat 10%", "ladder 10/5/3", "rolling 90d", "20 trades won >= 65%"]
    res = {m: {y: account(tr[tr.year == y], "A", m) for y in years} for m in modes}
    print(f"  {'sizing':22s} " + " | ".join(f"{y}: end / low / drop" for y in years))
    for m in modes:
        print(f"  {m:22s} " + " | ".join(f"${res[m][y]['end']:>8,.0f} ${res[m][y]['low']:>6,.0f} {res[m][y]['worst_drop']:4.0%}"
                                         for y in years))
    lad, f10, f5 = res["ladder 10/5/3"], res["flat 10%"], res["flat 5%"]
    n3 = sum(lad[y]["worst_drop"] < f10[y]["worst_drop"] and lad[y]["end"] >= f5[y]["end"] for y in years)
    print(f"  RULE 3 (ladder kept over flat): {n3}/4 years -> {'PASS' if n3 >= 3 else 'FAIL'}")
    for m in ("rolling 90d", "20 trades won >= 65%"):
        n4 = sum(res[m][y]["end"] > lad[y]["end"] and res[m][y]["worst_drop"] <= lad[y]["worst_drop"] + 0.05 for y in years)
        print(f"  RULE 4 ({m} replaces the ladder): {n4}/4 years -> {'PASS' if n4 >= 3 else 'FAIL'}")
    print("\n=== accounts: QUICK, for comparison ===")
    for m in ("flat 3%", "flat 5%"):
        print(f"  {m:22s} " + " | ".join(
            f"${account(tr[tr.year == y], 'QUICK', m)['end']:>8,.0f} drop {account(tr[tr.year == y], 'QUICK', m)['worst_drop']:4.0%}"
            for y in years))


if __name__ == "__main__":
    {"fetch": cmd_fetch, "judge": cmd_judge}[sys.argv[1]]()

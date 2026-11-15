"""Careful sim, implementation B: written from docs/careful_sim_spec.md alone. No trade logic shared with A or C;
Delta sizing solved by bisection here."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402

DEFAULT = dict(im=5.0, mm=2.5, ims=1e-05, mms=5e-06, max_lev_notional=50000.0, limit=10 ** 12, cv=1.0)


def sizing(sym, margin, price, extra):
    p = D.PRODUCTS.get(sym) or DEFAULT
    im, mm, ims, mms, cap = p["im"] / 100, p["mm"] / 100, p["ims"] / 100, p["mms"] / 100, p["max_lev_notional"]
    lo_, hi_ = 0.0, margin / (im + 0.0012)
    for _ in range(100):
        mid = (lo_ + hi_) / 2
        if mid * (im + ims * max(0.0, mid - cap) + 0.0012) <= margin:
            lo_ = mid
        else:
            hi_ = mid
    n = min(lo_, p["limit"] * p["cv"] * price)
    over = max(0.0, n - cap)
    return n, (im + ims * over) - (mm + mms * over) + extra / n


def first_trade(sym, H, L, Cl, bal, lenient):
    limit = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= limit * 1.0005), None)
    if k is None:
        return None
    e = limit
    margin, extra, sc = 0.49 * bal, 0.25 * bal, bal / D.START
    n, ld = sizing(sym, margin, e, extra)
    floor_ = -(margin + extra)
    pl = lambda x: max(n * (-(x / e - 1) - 0.0002 - 0.0005 - 0.0005 * x / e), floor_)  # noqa: E731
    T = e * (1 - (500 * sc / n + 0.0012))
    S = e * 1.0225
    K = e * (1 - (200 * sc / n + 0.0012))
    Q = e * (1 + ld)
    locked, lowest = False, e
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if H[j] >= S:
            x = max(S, Cl[j]) * 1.001 if not lenient else S * 1.001
            if not locked and x >= Q:
                return dict(how="LIQ", pnl=floor_, k=k, j=j)
            return dict(how="LOCKED" if locked else "STOP", pnl=pl(x), k=k, j=j)
        if L[j] <= T * 0.999:
            return dict(how="TARGET", pnl=n * (-(T / e - 1) - 0.0002 - 0.0005 - 0.0005 * T / e), k=k, j=j)
        lowest = min(lowest, L[j])
        if not locked and L[j] <= 0.99 * e:
            locked = True
            S = min(S, K)
            if Cl[j] >= S:
                return dict(how="LOCKED", pnl=pl(Cl[j] * 1.001), k=k, j=j)
        if locked:
            S = min(S, 1.008 * lowest)
            if Cl[j] >= S:
                return dict(how="LOCKED", pnl=pl(Cl[j] * 1.001), k=k, j=j)
    return dict(how="TIME", pnl=pl(Cl[last] * 1.001), k=k, j=last)


def resell(sym, H, L, Cl, m, bal, lenient):
    top = H[m]
    trig = None
    for j in range(m + 1, 1440):
        if L[j] <= 0.95 * top:
            trig = (j, min(0.95 * top, Cl[j]) * 0.999)
            break
        top = max(top, H[j])
    if trig is None:
        return None
    j, p = trig
    margin, extra = 0.49 * bal, 0.25 * bal
    n, ld = sizing(sym, margin, p, extra)
    S, Q = 1.0225 * p, p * (1 + ld)
    last = min(2880, m + 1440)
    for i in range(j + 1, last + 1):
        if H[i] >= S:
            x = S * 1.001 if lenient else max(S, Cl[i]) * 1.001
            if x >= Q:
                return dict(how="LIQ", pnl=-(margin + extra), k=j, j=i)
            return dict(how="STOP", pnl=max(n * (-(x / p - 1) - 0.0005 - 0.0005 * x / p), -(margin + extra)), k=j, j=i)
    x = Cl[last] * 1.001
    return dict(how="TIME", pnl=max(n * (-(x / p - 1) - 0.0005 - 0.0005 * x / p), -(margin + extra)), k=j, j=last)


def run(days, variant, lenient):
    v = D.VARIANTS[variant]
    balance, bank, free_at, cur, full_stops = D.START, 0.0, -10 ** 9, "", 0
    out = []
    for day, picks in days:
        if day.strftime("%Y-%m") != cur:
            cur, full_stops = day.strftime("%Y-%m"), 0
            if v["bank"] and balance > D.START:
                bank, balance = bank + balance - D.START, D.START
        base = int(day.timestamp()) // 60
        if balance < v["floor"] or full_stops >= 2 or free_at > base + 2:
            continue
        trades = []
        for rank, sym, H, L, Cl in picks:
            t = first_trade(sym, H, L, Cl, balance, lenient)
            if t:
                trades.append((t["k"], rank, sym, H, L, Cl, t))
        if not trades:
            continue
        trades.sort(key=lambda z: (z[0], z[1]))
        _, rank, sym, H, L, Cl, t = trades[0]
        balance = max(balance + t["pnl"], 0.0)
        free_at = base + t["j"]
        full_stops += t["how"] in ("STOP", "LIQ")
        out.append((day.date(), sym, "sell", t["k"], t["j"], "24h" if t["how"] == "TIME" else t["how"], t["pnl"], balance))
        if v["resell"] and t["how"] in ("STOP", "LIQ") and t["j"] <= 1438 and full_stops < 2 and balance >= v["floor"]:
            q = resell(sym, H, L, Cl, t["j"], balance, lenient)
            if q:
                balance = max(balance + q["pnl"], 0.0)
                free_at = base + q["j"]
                full_stops += q["how"] in ("STOP", "LIQ")
                out.append((day.date(), sym, "resell", q["k"], q["j"], q["how"], q["pnl"], balance))
    return out, balance, bank

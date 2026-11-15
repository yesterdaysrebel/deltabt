"""Careful sim, implementation C: a minute-by-minute state machine like the live bot (resting orders -> position ->
re-sell watch -> position), written from docs/careful_sim_spec.md. Delta sizing solved in closed form here."""
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402

FALLBACK = dict(im=5.0, mm=2.5, ims=1e-05, mms=5e-06, max_lev_notional=50000.0, limit=10 ** 12, cv=1.0)


def notional_for(sym, margin, price):
    p = D.PRODUCTS.get(sym) or FALLBACK
    a, cap, r = p["im"] / 100 + 0.0012, p["max_lev_notional"], p["ims"] / 100
    n = margin / a
    if n > cap and r > 0:                         # r n^2 + (a - r cap) n - margin = 0
        b = a - r * cap
        n = (-b + math.sqrt(b * b + 4 * r * margin)) / (2 * r)
    n = min(n, p["limit"] * p["cv"] * price)
    over = max(0.0, n - cap)
    liq = (p["im"] / 100 + r * over) - (p["mm"] / 100 + p["mms"] / 100 * over)
    return n, liq


class Position:
    def __init__(self, sym, entry, opened, bal, kind, lenient, in_fee, maker_slip, hard_end):
        self.sym, self.e, self.opened, self.kind, self.lenient = sym, entry, opened, kind, lenient
        self.margin, self.extra = 0.49 * bal, 0.25 * bal
        self.n, ld = notional_for(sym, self.margin, entry)
        ld += self.extra / self.n
        self.in_cost = in_fee + maker_slip
        self.liq = entry * (1 + ld)
        self.stop = entry * 1.0225
        self.locked, self.lowest, self.end = False, entry, hard_end
        if kind == "sell":
            sc = bal / D.START
            c = self.in_cost + 0.0005
            self.target = entry * (1 - (500 * sc / self.n + c))
            self.lock_px = entry * (1 - (200 * sc / self.n + c))
        else:
            self.target = None

    def pnl(self, x):
        v = self.n * (-(x / self.e - 1) - self.in_cost - 0.0005 * x / self.e)
        return max(v, -(self.margin + self.extra))

    def tick(self, k, h, l, c):
        """-> (how, pnl) when the position closes in minute k, else None."""
        if h >= self.stop:
            x = self.stop * 1.001 if self.lenient else max(self.stop, c) * 1.001
            if not self.locked and x >= self.liq:
                return "LIQ", -(self.margin + self.extra)
            return ("LOCKED" if self.locked else "STOP"), self.pnl(x)
        if self.target is not None and l <= self.target * 0.999:
            return "TARGET", self.pnl(self.target)
        if self.kind == "sell":
            self.lowest = min(self.lowest, l)
            if not self.locked and l <= 0.99 * self.e:
                self.locked = True
                self.stop = min(self.stop, self.lock_px)
                if c >= self.stop:
                    return "LOCKED", self.pnl(c * 1.001)
            if self.locked:
                self.stop = min(self.stop, 1.008 * self.lowest)
                if c >= self.stop:
                    return "LOCKED", self.pnl(c * 1.001)
        if k >= self.end:
            return ("24h" if self.kind == "sell" else "TIME"), self.pnl(c * 1.001)
        return None


def run(days, variant, lenient):
    v = D.VARIANTS[variant]
    acct = dict(bal=D.START, bank=0.0, busy_abs=-1, month=None, stops=0)
    ledger = []
    for day, picks in days:
        t0 = int(day.timestamp()) // 60
        if day.strftime("%Y-%m") != acct["month"]:
            acct["month"], acct["stops"] = day.strftime("%Y-%m"), 0
            if v["bank"] and acct["bal"] > D.START:
                acct["bank"] += acct["bal"] - D.START
                acct["bal"] = D.START
        if acct["bal"] < v["floor"] or acct["stops"] >= 2 or acct["busy_abs"] > t0 + 2:
            continue
        orders = [dict(rank=r, sym=s, H=H, L=L, C=Cl, limit=Cl[2] * 1.05) for r, s, H, L, Cl in picks]
        state, pos, watch, src = "RESTING", None, None, None
        day_bal = acct["bal"]
        for k in range(3, 2881):
            if state == "RESTING":
                if k >= 1440:
                    break                                         # 24:00: orders cancelled, no trade
                filled = [o for o in orders if o["H"][k] >= o["limit"] * 1.0005]
                if filled:
                    src = min(filled, key=lambda o: o["rank"])   # same minute -> better rank; the rest cancelled
                    pos = Position(src["sym"], src["limit"], k, day_bal, "sell", lenient, 0.0002, 0.0005, min(2880, k + 1440))
                    state = "OPEN"
                continue
            if state == "OPEN":
                res = pos.tick(k, src["H"][k], src["L"][k], src["C"][k])
                if res is None:
                    continue
                how, pnl = res
                acct["bal"] = max(acct["bal"] + pnl, 0.0)
                acct["busy_abs"] = t0 + k
                acct["stops"] += how in ("STOP", "LIQ")
                ledger.append((day.date(), pos.sym, pos.kind, pos.opened, k, how, pnl, acct["bal"]))
                if (pos.kind == "sell" and v["resell"] and how in ("STOP", "LIQ") and k <= 1438
                        and acct["stops"] < 2 and acct["bal"] >= v["floor"]):
                    watch = dict(top=src["H"][k], stopped_at=k)
                    state = "WATCH"
                    continue
                break
            if state == "WATCH":
                if k >= 1440:
                    break
                if src["L"][k] <= 0.95 * watch["top"]:
                    px = min(0.95 * watch["top"], src["C"][k]) * 0.999
                    pos = Position(src["sym"], px, k, acct["bal"], "resell", lenient, 0.0005, 0.0, min(2880, watch["stopped_at"] + 1440))
                    state = "OPEN"
                else:
                    watch["top"] = max(watch["top"], src["H"][k])
    return ledger, acct["bal"], acct["bank"]

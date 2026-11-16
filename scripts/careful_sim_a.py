"""Careful sim, implementation A: built on the already-tested trade functions (run4_trailing.short for run 4,
clean_compare.size for sizing, run4_resell_search.entry_point / run4_resell_account.resell_usd for the re-sell), with
the account written to docs/careful_sim_spec.md."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import run4_resell_account as RA  # noqa: E402
import run4_trailing as T  # noqa: E402


def run(days, variant, lenient):
    v = D.VARIANTS[variant]
    bal, bank, busy, month, stops = D.START, 0.0, -1, None, 0
    refill = v.get("refill")
    ledger = []
    for day, picks in days:
        t0 = int(day.timestamp()) // 60
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops = m, 0
            if refill == "month" and bal < D.START:
                ledger.append((day.date(), "-", "reload", 0, 0, "RELOAD", D.START - bal, D.START))
                bal = D.START
            if v["bank"] and bal > D.START:
                bank += bal - D.START
                bal = D.START
        if bal < v["floor"] or stops >= v.get("stop_limit", 2) or busy > t0 + 2:
            continue
        size_bal = D.START if v.get("fixed") else bal
        s = size_bal / D.START
        best = None
        for rank, sym, hi, lo, cl in picks:
            r = T.short(sym, hi, lo, cl, 3, 0.49 * size_bal, 500 * s, 200 * s, 0.25 * size_bal, lenient, 0.008, None)
            if r is not None and (best is None or r[2] < best[1][2]):
                best = ((rank, sym, hi, lo, cl), r)
        if best is None:
            continue
        (rank, sym, hi, lo, cl), r = best
        bal = bal + r[1] if v.get("fixed") else max(bal + r[1], 0.0)
        busy = t0 + r[3]
        stops += r[0] in ("STOP", "LIQ")
        ledger.append((day.date(), sym, "sell", r[2], r[3], r[0], r[1], bal))
        if refill == "after2" and stops >= 2:
            ledger.append((day.date(), "-", "reload", 0, 0, "RELOAD", max(D.START - bal, 0.0), max(bal, D.START)))
            bal, stops = max(bal, D.START), 0
        if v["resell"] and r[0] in ("STOP", "LIQ") and r[3] <= 1438 and stops < 2 and bal >= v["floor"]:
            q = RA.resell_usd((day, sym, hi, lo, cl, r[3]), bal, lenient)
            if q is not None:
                bal = max(bal + q[1], 0.0)
                busy = t0 + q[3]
                stops += q[0] in ("STOP", "LIQ")
                ledger.append((day.date(), sym, "resell", q[2], q[3], q[0], q[1], bal))
    return ledger, bal, bank

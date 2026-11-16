"""The replay battery (scripts/replay_bot.py): the REAL bot against the simulated Delta, many runs, in parallel on
separate scratch databases of the local test PostgreSQL.
  A  clean runs      full history and each of the 22 monthly starts, STRICT and LENIENT: every trade must equal the
                     research rule's (same coin, fill minute, exit minute, exit kind, P&L within 1% / $1)
  B  real ticks      ~1 bp price ticks and Delta's real contract sizes (the bot rounds prices and contracts)
  C  every minute    a pass every minute while orders rest too (not only a heartbeat), on 2026-08..11
  D  faults          timeouts on reads, lost replies on writes (before and AFTER the exchange acted), refused margin
                     top-ups, failed stop edits, the bot process restarted every N passes, the by-client-id order
                     lookup missing orders that exist, both picks filling in the same minute (Delta refuses to
                     cancel an order that is already filled or gone) -- several seeds each.
                     The account must not halt, never hold a position without its stop, never hold two positions,
                     never add the extra margin twice, never move a stop up, and end flat.
  E  by design       a lost reply on the ENTRY order must HALT the account (the owner decides), with nothing unprotected.

    DELTABOT_TEST_DSN=postgresql://... PYTHONPATH=. python scripts/replay_battery.py
"""
import asyncio
import logging
import os
import sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_be_trail as BT  # noqa: E402
import careful_data as D  # noqa: E402
import replay_bot as R  # noqa: E402

BASE = os.environ["DELTABOT_TEST_DSN"]
READS = ("get_ticker", "get_positions", "get_position", "get_pending_orders", "get_open_orders", "order_history", "get_balance",
         "get_order_by_client_id")


def dsn_for(i):
    return BASE.rsplit("/", 1)[0] + f"/replay_{i}"


def fault_spec(kind):
    if kind == "reads":
        return {m: (0.01, "timeout") for m in READS}
    if kind == "writes":
        return {"add_position_margin": (0.15, "ambiguous"), "add_position_margin:after": (0.15, "ambiguous"),
                "edit_stop_price": (0.15, "venue"), "edit_stop_price:after": (0.15, "ambiguous"),
                "cancel_order": (0.05, "timeout"), "buy_back:after": (0.15, "ambiguous"), "set_order_leverage": (0.02, "rejected")}
    if kind == "refused":
        return {"add_position_margin": (0.5, "rejected")}
    if kind == "all":
        d = fault_spec("reads")
        d.update(fault_spec("writes"))
        return d
    if kind == "entry":
        return {"place_order": (0.03, "ambiguous")}
    return None


WORKER_DB = None


def init_worker(q):
    """Each pool worker owns ONE scratch database for its whole life (two runs never share one)."""
    global WORKER_DB
    WORKER_DB = q.get()


def job(arg):
    logging.disable(logging.CRITICAL)
    i, name, lo, hi, le, opts = arg
    worker = WORKER_DB
    days = [d for d in D.load() if lo <= d[0].strftime("%Y-%m") <= hi]
    kw = dict(opts)
    f = kw.pop("fault", None)
    kw["fault"] = fault_spec(f)
    try:
        res = asyncio.run(R.one(dsn_for(WORKER_DB), days, le, **kw))
    except Exception as exc:                              # noqa: BLE001 -- a harness failure is reported, not fatal
        import traceback
        return dict(name=name, lo=lo, le=le, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc()[-600:])
    ref = R.reference(days, le, month_by_exit=True)
    same, diffs = R.compare(res["bot"], ref, tol_rel=0.01 if kw.get("fine", True) else 0.05)
    res.update(name=name, lo=lo, le=le, ref_n=len(ref), ref_bal=ref[-1][6] if ref else R.S.RUN_START_USD, same=same,
               diffs=[(d[0], d[1], d[2]) for d in diffs[:6]], n_diff=len(diffs), worker=worker)
    return res


N_DB = 8
SEEDS = 10


async def make_dbs():
    import asyncpg
    con = await asyncpg.connect(BASE)
    for i in range(N_DB):
        if not await con.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", f"replay_{i}"):
            await con.execute(f"CREATE DATABASE replay_{i}")
    await con.close()


def show(r):
    ok = r["n_diff"] == 0 and r["status"] == "RUNNING" or (r["n_diff"] == 0 and r["status"] == "STOPPED")
    return (f"{r['name']:34s} {'LENIENT' if r['le'] else 'STRICT ':7s} bot {len(r['bot']):3d} trades / reference {r['ref_n']:3d}: identical {r['same']:3d}, "
            f"different {r['n_diff']:2d} | wallet ${r['bal']:9,.2f} vs ${r['ref_bal']:9,.2f} | {r['status']:7s} | passes {r['steps']:6d} | "
            f"unprotected {r['unprotected']}, two positions {r['two_positions']}, left open {r['open_left']}+{r['resting_left']} {'OK' if ok else '<<<'}")


def main():
    asyncio.run(make_dbs())
    # sanity: the reference rule equals the research function (careful_be_trail.t1) when given its extra margin
    n = 0
    for day, picks in D.load():
        for rank, sym, H, L, Cl in picks:
            for le in (False, True):
                a = R.ref_trade(sym, H, L, Cl, 3184.0, le, extra_frac=0.25 * 0.10 / 0.49)
                b = BT.t1(sym, H, L, Cl, 3184.0 * 0.10 / 0.49, le)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:] == b[2:] and abs(a[1] - b[1]) < 0.01)), (day, sym, a, b)
                n += a is not None
    print(f"SANITY PASSED: the replay's reference rule equals the research function on all {n} trades (both fills).\n")
    jobs, i = [], 0

    def add(name, lo, hi, le, **opts):
        nonlocal i
        jobs.append((i, name, lo, hi, le, opts))
        i += 1
    for le in (False, True):
        add("A full history", "2025-01", "2026-12", le)
        for mo in pd.period_range("2025-02", "2026-10", freq="M"):
            add(f"A start {mo}", str(mo), "2026-12", le)
        add("B real ticks, full history", "2025-01", "2026-12", le, fine=False)
        add("C a pass every minute, 2026-08..11", "2026-08", "2026-12", le, heartbeat=1)
    for seed in range(SEEDS):
        add(f"D1 read timeouts, seed {seed}", "2025-01", "2026-12", seed % 2 == 1, fault="reads", seed=seed)
        add(f"D2 lost / failed writes, seed {seed}", "2025-01", "2026-12", seed % 2 == 1, fault="writes", seed=seed)
        add(f"D3 restart every few passes, seed {seed}", "2025-01", "2026-12", seed % 2 == 1, restart_every=5 + seed, seed=seed)
        add(f"D4 everything at once, seed {seed}", "2025-01", "2026-12", seed % 2 == 1, fault="all", restart_every=9 + seed, seed=seed)
    for seed in range(2):
        add(f"D5 margin top-up refused half the time, seed {seed}", "2025-01", "2026-12", False, fault="refused", seed=seed)
    for seed in range(SEEDS):
        add(f"D6 order lookup misses 1 in 4, seed {seed}", "2025-01", "2026-12", seed % 2 == 1, miss=0.25, seed=seed)
        add(f"D8 everything + lookup misses + both picks may fill, seed {seed}", "2025-01", "2026-12", seed % 2 == 1, fault="all",
            restart_every=9 + seed, miss=0.15, double=True, seed=seed)
    for le in (False, True):
        add(f"D7 both picks may fill in one minute, {'lenient' if le else 'strict'}", "2025-01", "2026-12", le, double=True)
    for seed in range(4):
        add(f"E lost entry reply, seed {seed}", "2025-01", "2026-12", False, fault="entry", seed=seed)
    from multiprocessing import Queue
    q = Queue()
    for k in range(N_DB):
        q.put(k)
    with Pool(N_DB, initializer=init_worker, initargs=(q,)) as p:
        out = p.map(job, jobs, chunksize=1)
    errs = [r for r in out if "error" in r]
    for r in errs:
        print(f"HARNESS ERROR in {r['name']} ({'LENIENT' if r['le'] else 'STRICT'}): {r['error']}\n{r['tb']}")
    out = [r for r in out if "error" not in r]
    res = {r["name"] + ("|L" if r["le"] else "|S"): r for r in out}
    for sect, title in (("A", "A. CLEAN RUNS -- every trade against the research rule"), ("B", "B. REAL TICK AND CONTRACT SIZES"),
                        ("C", "C. A PASS EVERY MINUTE"), ("D", "D. FAULTS AND RESTARTS"), ("E", "E. LOST ENTRY REPLY (must halt, by design)")):
        rows = [r for r in out if r["name"].startswith(sect)]
        print(title)
        if sect == "A":
            tot = sum(len(r["bot"]) for r in rows)
            print(f"   {len(rows)} runs, {tot} bot trades, identical {sum(r['same'] for r in rows)}, different {sum(r['n_diff'] for r in rows)}; "
                  f"bot passes {sum(r['steps'] for r in rows):,}; halts {sum(r['status'] == 'HALTED' for r in rows)}; unprotected passes "
                  f"{sum(r['unprotected'] for r in rows)}; two positions {sum(r['two_positions'] for r in rows)}; wallet differences over $1: "
                  f"{sum(abs(r['bal'] - r['ref_bal']) > 1 for r in rows)}")
            for r in rows:
                if r["name"] == "A full history" or r["n_diff"] or r["status"] == "HALTED":
                    print("   " + show(r))
                    for d in r["diffs"]:
                        print("        DIFF", d)
        elif sect == "D":
            # Under faults the bot acts a pass later here and there, so its trades are not expected to equal the
            # reference one by one; what must hold are the SAFETY properties.
            clean = {False: res["A full history|S"]["bal"], True: res["A full history|L"]["bal"]}
            for grp in sorted({r["name"].split(",")[0] for r in rows}):
                g = [r for r in rows if r["name"].startswith(grp)]
                bad = [r for r in g if r["status"] == "HALTED" or r["unprotected"] or r["two_positions"] or r["open_left"] or r["resting_left"]
                       or r["margin_adds"] > r["fills"]]
                ev = {}
                for r in g:
                    for k, v in r["events"].items():
                        ev[k] = ev.get(k, 0) + v
                print(f"   {grp:40s} {len(g):2d} runs | faults injected {sum(r['faults'] for r in g):5d}, restarts {sum(r['restarts'] for r in g):5d}, "
                      f"bot passes {sum(r['steps'] for r in g):7,d} | trades {sum(len(r['bot']) for r in g):5d} | HALTED {sum(r['status'] == 'HALTED' for r in g)}, "
                      f"passes with a position and no stop {sum(r['unprotected'] for r in g)}, two positions {sum(r['two_positions'] for r in g)}, "
                      f"left open at the end {sum(r['open_left'] + r['resting_left'] for r in g)}, margin added twice {sum(max(0, r['margin_adds'] - r['fills']) for r in g)} "
                      f"-> {'ALL SAFE' if not bad else 'PROBLEM <<<'}")
                print(f"        fills {sum(r['fills'] for r in g)} (double fills {sum(r['double_fills'] for r in g)}, bought back at once: "
                      f"{ev.get('DOUBLE_FILL', 0)} events), extra margin landed on {sum(r['margin_adds'] for r in g)}; identical to the fault-free trade list: "
                      f"{sum(r['n_diff'] == 0 for r in g)} of {len(g)} runs; end wallet vs the fault-free run: " +
                      ", ".join(f"{r['bal'] - clean[r['le']]:+,.0f}" for r in g))
                print("        bot events: " + ", ".join(f"{k} {v}" for k, v in sorted(ev.items()) if k in (
                    "ERROR", "HALTED", "EXTRA_MARGIN_UNCLEAR", "EXTRA_MARGIN_REFUSED", "LOCK_FAILED", "TRAIL_FAILED", "EXIT_INCOMPLETE", "STOPPED", "DOUBLE_FILL")))
                for r in bad:
                    print("        <<< " + show(r) + f" | {r['reason'][:140]}")
        else:
            for r in rows:
                print("   " + show(r) + (f" | faults {r['faults']}" if sect == "E" else ""))
                if sect == "E":
                    print(f"        {'HALT reason: ' + r['reason'][:150] if r['status'] == 'HALTED' else 'no entry reply was lost in this run'}")
                for d in r["diffs"][:3] if sect in "BC" else []:
                    print("        DIFF", d)
        print()
    a = res["A full history|S"], res["A full history|L"]
    print(f"Bot end wallet, full history from $3,184: ${a[0]['bal']:,.2f} (strict) .. ${a[1]['bal']:,.2f} (lenient)  "
          f"[research account, 5.1% vs 5.102% extra margin and stops counted by pick day: $1,801 .. $3,890]")


if __name__ == "__main__":
    main()

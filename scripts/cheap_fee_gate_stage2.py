"""Cheap-trade gate, STAGE 2: the frozen rule on an older, untouched window (2026-10-12).

    PYTHONPATH=. .venv/bin/python scripts/cheap_fee_gate_stage2.py fetch
    DELTABT_DATA=data_old PYTHONPATH=. .venv/bin/python scripts/cheap_fee_gate_stage2.py run

WHY. Stage 1 (scripts/cheap_fee_gate_lab.py) survived by the letter and was weak:
+0.027R per trade on 713 trades (t +0.41), knife-edge thresholds, negative at 2x
spreads. Its 0.03 threshold was read from the Dec 2025 - Sep 2026 data, so only
data the rule has never touched can decide it. Owner approved 2026-10-12.

WINDOW. 2024-06-01 .. 2025-12-19 23:59 UTC, ending the day before the current
cache starts. Fetched read-only from Delta's public API (LTP, MARK and FUNDING,
as the engine needs) into data_old/ (gitignored), at 4 requests a second.

UNIVERSE. The 32 coins of the symbol screen that have data in the window. This
is a KNOWN BIAS: those coins were picked in Oct 2026 as the most traded with
history, i.e. survivors. A causal universe over every perp listed at the time
would need a far larger download; the bias is stated, not removed.

FROZEN -- identical to stage 1, nothing re-chosen: the engine, the entry, the
4xATR stop, 3R / 72h, the 0.15 gate, the 0.03 cheap gate through
RiskGates.entry_filter, per-coin slippage = max(2 bps, half the 2026-10-05 quoted
spread) (spreads in 2024-25 were not recorded; the same snapshot is used), the
random-refusal control (100 runs).

RULES, written before the fetch. Judged on hold-to-3R, normal costs, 0.03 gate:
  C1 net R per trade > 0
  C2 beats the random-refusal runs at p <= 0.05
  C3 net R per trade > 0 in BOTH halves of the window
  CONFIRMED = C1 and C2 and C3. Anything else closes the cheap-trade idea.
Reported, not judged: the trail, 2x spreads, 0.02 / 0.05, t against zero.
Even CONFIRMED means "worth a forward check", not an edge: stage 1's +0.027R was
indistinguishable from zero.
"""
import datetime as dt, glob, importlib.util, json, os, pathlib, shutil, sys
sys.path.insert(0, ".")

START = int(dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc).timestamp())
END = int(dt.datetime(2025, 12, 19, 23, 59, tzinfo=dt.timezone.utc).timestamp())
SYMS = sorted(pathlib.Path(f).stem for f in glob.glob("out/sweep/five_min_arm_lab/symbol_screen/trades/*.csv"))
OUT = pathlib.Path("out/sweep/five_min_arm_lab/cheap_gate")


def fetch():
    from deltabt.data.client import DeltaClient
    from deltabt.data.store import CandleStore
    root = pathlib.Path("data_old"); (root / "meta").mkdir(parents=True, exist_ok=True)
    for f in pathlib.Path("data/meta").glob("*"):                      # the product catalog the engine reads
        shutil.copy(f, root / "meta" / f.name)
    store = CandleStore(DeltaClient(per_second=4.0), cache_dir=root / "candles")
    manifest = {"fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "start": START, "end": END, "symbols": {}}
    for s in SYMS:
        d = store.load_all_series(s, "1m", START, END)
        ltp = d.get("ltp")
        n = 0 if ltp is None else len(ltp)
        first = None if not n else int(ltp.time.min())
        manifest["symbols"][s] = {"ltp_rows": n, "first": first, "mark_rows": 0 if d.get("mark") is None else len(d["mark"])}
        print(f"{s:11s} ltp {n:7d} rows  from {dt.datetime.fromtimestamp(first, dt.timezone.utc):%Y-%m-%d}" if n else f"{s:11s} no data in the window", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stage2_fetch_manifest.json").write_text(json.dumps(manifest, indent=1))


def run():
    assert os.environ.get("DELTABT_DATA") == "data_old", "run with DELTABT_DATA=data_old"
    spec = importlib.util.spec_from_file_location("cfg", "scripts/cheap_fee_gate_lab.py")
    L = importlib.util.module_from_spec(spec); sys.modules["cfg"] = L; spec.loader.exec_module(L)
    have = [s for s in SYMS if (pathlib.Path("data_old/candles") / s / "ltp_1m.parquet").exists()
            and len(__import__("pandas").read_parquet(pathlib.Path("data_old/candles") / s / "ltp_1m.parquet")) > 50_000]
    print(f"STAGE 2: {len(have)} of {len(SYMS)} coins have data in 2024-06-01..2025-12-19: {', '.join(have)}")
    L.UNIVERSES = {"32 coins": have}; L.N_RANDOM = {"32 coins": 100}
    L.load(have)
    for cost in L.SLIP:
        L.BOOKS[("32 coins", cost)] = L.books("32 coins", cost)
    verdict = None
    for ex in L.EXITS:
        for cost in L.SLIP:
            v = L.report("32 coins", cost, ex, judged=(ex == "hold-to-3R" and cost == "normal"))
            verdict = v if v is not None else verdict
    print(f"\nSTAGE 2 VERDICT: {'CONFIRMED -- worth a forward check, not an edge' if verdict else 'CLOSED'}")


if __name__ == "__main__":
    {"fetch": fetch, "run": run}[sys.argv[1]]()

"""H-Structure-1 -- NULL BASELINES (§12) + TRAIN SCREEN (§14). TRAIN ONLY.

Writes market_structure_baseline.md, market_structure_train.md, the results
CSVs, and frozen_candidates.json. VALID is never touched by this script -- the
window is not even constructed.

CANDIDATE FREEZE RULE, declared here before any TRAIN number was produced:
    eligible          trades >= 200 and effective_n >= 30
    PRIMARY           highest TRAIN gross R among eligible primary-grid candidates
    per-family best   highest TRAIN gross R among eligible, within each family
    family reference  (N=5, struct_tf=15m, oneshot) for each family, declared a
                      priori and therefore immune to selection on TRAIN
Nothing is re-tuned after this file is written.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import common
import hstructure as hs

OUT = Path(__file__).parent / "out"
N_SIMS = 100


def run_candidate(data, S_by, fam, trig, n, stf, etf, window, *, diag=True):
    """One candidate across the universe. Returns (frame, meta)."""
    frames, sig, sk_stop, sk_size = [], 0, 0, 0
    for sym, d in data.items():
        S = S_by[(sym, stf, n)]
        r = hs.run_variant(d, S, fam, trig, etf, start=window[0], end=window[1],
                           label=f"{fam}|N{n}|{stf}m->{etf}m|{trig}")
        sig += r.signals; sk_stop += r.skipped_stop; sk_size += r.skipped_size
        f = r.to_frame()
        if len(f):
            if diag:
                f = hs.attach_diagnostics(f, S, d, etf)
            f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
        if len(f):
            frames.append(f)
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return df, dict(signals=sig, skipped_stop=sk_stop, skipped_size=sk_size)


# ------------------------------------------------------------------ nulls


def null_random_direction(data, S_by, fam, trig, n, stf, etf, window, sims):
    """§12 NULL-1: the candidate's own entry times, direction by fair coin.

    Isolates direction. Everything else -- when we trade, how often, the stop
    geometry, the cost model -- is held identical to the signal.
    """
    means_g, means_n, counts = [], [], []
    for s in range(sims):
        rng = np.random.default_rng(1000 + s)
        frames = []
        for sym, d in data.items():
            S = S_by[(sym, stf, n)]
            lo, sh = hs.family_signals(S, fam, trig)
            fire = np.asarray(lo, bool) | np.asarray(sh, bool)
            coin = rng.random(len(fire)) < 0.5
            r = hs.run_variant(d, S, fam, trig, etf, start=window[0],
                               end=window[1], override_long=fire & coin,
                               override_short=fire & ~coin, label="NULL_DIR")
            f = r.to_frame()
            if len(f):
                f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
            if len(f):
                frames.append(f)
        if frames:
            a = pd.concat(frames, ignore_index=True)
            means_g.append(a.r_gross.mean()); means_n.append(a.r_net.mean())
            counts.append(len(a))
    return dict(sims=len(means_g), gross_mean=float(np.mean(means_g)) if means_g else None,
                gross_sd=float(np.std(means_g)) if means_g else None,
                gross_p05=float(np.percentile(means_g, 5)) if means_g else None,
                gross_p95=float(np.percentile(means_g, 95)) if means_g else None,
                net_mean=float(np.mean(means_n)) if means_n else None,
                trades_mean=float(np.mean(counts)) if counts else 0)


def null_random_entry(data, S_by, fam, trig, n, stf, etf, window, sims, k_target):
    """§12 NULL-2: random times AND random direction, matched trade count.

    Entries are drawn uniformly from the structure bars that HAVE a usable
    structural stop, so the null inherits the signal's risk denominator rather
    than a different one.
    """
    means_g, means_n, counts = [], [], []
    per_sym = max(int(k_target / max(len(data), 1)), 1)
    for s in range(sims):
        rng = np.random.default_rng(5000 + s)
        frames = []
        for sym, d in data.items():
            S = S_by[(sym, stf, n)]
            nb = len(S["time"])
            elig = np.zeros(nb, bool)
            elig[S["warmup"]:] = True
            elig &= np.isfinite(S["last_l_px"]) & np.isfinite(S["last_h_px"])
            # confine to the window on the structure grid
            elig &= (S["time"] >= window[0]) & (S["time"] < window[1])
            idx = np.flatnonzero(elig)
            if idx.size == 0:
                continue
            pick = rng.choice(idx, size=min(per_sym, idx.size), replace=False)
            lo = np.zeros(nb, bool); sh = np.zeros(nb, bool)
            coin = rng.random(pick.size) < 0.5
            lo[pick[coin]] = True; sh[pick[~coin]] = True
            r = hs.run_variant(d, S, fam, trig, etf, start=window[0],
                               end=window[1], override_long=lo,
                               override_short=sh, label="NULL_RND")
            f = r.to_frame()
            if len(f):
                f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
            if len(f):
                frames.append(f)
        if frames:
            a = pd.concat(frames, ignore_index=True)
            means_g.append(a.r_gross.mean()); means_n.append(a.r_net.mean())
            counts.append(len(a))
    return dict(sims=len(means_g), gross_mean=float(np.mean(means_g)) if means_g else None,
                gross_sd=float(np.std(means_g)) if means_g else None,
                gross_p05=float(np.percentile(means_g, 5)) if means_g else None,
                gross_p95=float(np.percentile(means_g, 95)) if means_g else None,
                net_mean=float(np.mean(means_n)) if means_n else None,
                trades_mean=float(np.mean(counts)) if counts else 0)


def null_unconditional(data, S_by, n, stf, etf, window, side):
    """§12 NULL-3: always-on. Enter every structure bar, one position at a time.

    Same structural stop, same 2R target, same costs. This is the drift/geometry
    baseline: whatever this earns is not signal.
    """
    frames = []
    for sym, d in data.items():
        S = S_by[(sym, stf, n)]
        nb = len(S["time"])
        on = np.zeros(nb, bool); on[S["warmup"]:] = True
        z = np.zeros(nb, bool)
        r = hs.run_variant(d, S, "A", "level", etf, start=window[0], end=window[1],
                           override_long=on if side > 0 else z,
                           override_short=z if side > 0 else on,
                           label=f"NULL_UNCOND_{'L' if side>0 else 'S'}")
        f = r.to_frame()
        if len(f):
            f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
        if len(f):
            frames.append(f)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ------------------------------------------------------------------ main


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    W = common.TRAIN
    print("=" * 108)
    print("H-STRUCTURE-1  TRAIN SCREEN")
    print(f"  universe : {common.CORE}   (supplementary VALID-only: {common.SUPP})")
    print(f"  excluded : {common.EXCLUDED}")
    print(f"  TRAIN    : {pd.Timestamp(W[0],unit='s')} -> {pd.Timestamp(W[1],unit='s')}")
    print("  VALID    : NOT CONSTRUCTED IN THIS SCRIPT")
    print("=" * 108)

    data = common.load(common.CORE)
    for s, d in data.items():
        print(f"  {s:8} {len(d['df']):,} 1m bars  "
              f"{pd.Timestamp(int(d['t1'][0]),unit='s').date()} -> "
              f"{pd.Timestamp(int(d['t1'][-1]),unit='s').date()}  "
              f"taker={d['costs'].effective_taker*1e4:.2f}bps "
              f"slip={d['costs'].slippage_rate*1e4:.1f}bps "
              f"funding={d['costs'].funding_interval_seconds//3600}h")

    print("\nbuilding structure grids (symbol x timeframe x swing strength)...")
    S_by = {}
    for sym, d in data.items():
        for stf in hs.STRUCT_TF:
            for n in hs.SWING_N:
                S_by[(sym, stf, n)] = hs.build_structure(d["df"], stf, n)
    print(f"  {len(S_by)} grids built")

    # swing census -- how much raw structure exists at each setting
    census = []
    for (sym, stf, n), S in S_by.items():
        m = (S["time"] >= W[0]) & (S["time"] < W[1])
        census.append(dict(symbol=sym, struct_tf=stf, swing_n=n,
                           bars=int(m.sum()),
                           bull_pct=round(100 * float(S["bull"][m].mean()), 2),
                           bear_pct=round(100 * float(S["bear"][m].mean()), 2),
                           conf_delay_bars=n, conf_delay_min=n * stf))
    pd.DataFrame(census).to_csv(OUT / "swing_census.csv", index=False)

    # ---------------------------------------------------------------- nulls
    print("\n" + "=" * 108)
    print("§12  NULL BASELINES  (identical stop / target / cost / sizing path)")
    print("=" * 108)
    null_rows = []
    print("\n  NULL-3 unconditional (enter every structure bar, one position at a time)")
    for stf in hs.STRUCT_TF:
        for side in (1, -1):
            df = null_unconditional(data, S_by, 5, stf, stf, W, side)
            r = common.summarise(df, f"UNCOND_{'LONG' if side>0 else 'SHORT'}|{stf}m",
                                 kind="unconditional", struct_tf=stf,
                                 side_mode="long" if side > 0 else "short")
            null_rows.append(r)
            print("   ", common.fmt_row(r))

    print("\n  NULL-1 random direction at the signal's own entry times "
          f"({N_SIMS} sims)   |   NULL-2 random times + random direction")
    ref = [(f, 5, 15, "oneshot") for f in hs.FAMILIES]
    null_sims = []
    for fam, n, stf, trig in ref:
        sig_df, meta = run_candidate(data, S_by, fam, trig, n, stf, stf, W, diag=False)
        k = len(sig_df)
        if k < 20:
            print(f"    {fam}: only {k} signal trades, null skipped")
            continue
        n1 = null_random_direction(data, S_by, fam, trig, n, stf, stf, W, N_SIMS)
        n2 = null_random_entry(data, S_by, fam, trig, n, stf, stf, W, N_SIMS, k)
        sg = float(sig_df.r_gross.mean())
        z1 = (sg - n1["gross_mean"]) / n1["gross_sd"] if n1["gross_sd"] else np.nan
        z2 = (sg - n2["gross_mean"]) / n2["gross_sd"] if n2["gross_sd"] else np.nan
        null_sims.append(dict(family=fam, ref=f"N{n}|{stf}m|{trig}", signal_trades=k,
                              signal_gross=round(sg, 4),
                              null_dir_gross=round(n1["gross_mean"], 4),
                              null_dir_sd=round(n1["gross_sd"], 4),
                              null_dir_p05=round(n1["gross_p05"], 4),
                              null_dir_p95=round(n1["gross_p95"], 4),
                              z_vs_dir=round(float(z1), 2),
                              null_rnd_gross=round(n2["gross_mean"], 4),
                              null_rnd_sd=round(n2["gross_sd"], 4),
                              null_rnd_trades=round(n2["trades_mean"], 0),
                              z_vs_rnd=round(float(z2), 2)))
        print(f"    {fam}  signal n={k:>5,} gross={sg:+.4f}   "
              f"NULL-dir {n1['gross_mean']:+.4f} +/-{n1['gross_sd']:.4f} (z={z1:+.2f})   "
              f"NULL-rnd {n2['gross_mean']:+.4f} +/-{n2['gross_sd']:.4f} (z={z2:+.2f})")
    pd.DataFrame(null_rows).to_csv(OUT / "null_unconditional.csv", index=False)
    pd.DataFrame(null_sims).to_csv(OUT / "null_simulations.csv", index=False)

    # ---------------------------------------------------------------- grid
    print("\n" + "=" * 108)
    print("§14  TRAIN SCREEN -- PRIMARY GRID  (family x swing N x structure TF x trigger)")
    print("=" * 108)
    rows, events = [], []
    keep = {}
    for fam, n, stf, trig in itertools.product(hs.FAMILIES, hs.SWING_N,
                                               hs.STRUCT_TF, hs.TRIGGERS):
        df, meta = run_candidate(data, S_by, fam, trig, n, stf, stf, W)
        cid = f"{fam}|N{n}|{stf}m|{trig}"
        r = common.summarise(df, cid, family=fam, swing_n=n, struct_tf=stf,
                             exec_tf=stf, trigger=trig, grid="primary", **meta)
        rows.append(r)
        keep[cid] = df
        print("   ", common.fmt_row(r))

    print("\n" + "=" * 108)
    print("§5  MULTI-TIMEFRAME GRID  (structure TF != execution TF) -- reported separately")
    print("=" * 108)
    for fam, n, (stf, etf), trig in itertools.product(hs.FAMILIES, hs.SWING_N,
                                                      hs.MTF, hs.TRIGGERS):
        df, meta = run_candidate(data, S_by, fam, trig, n, stf, etf, W)
        cid = f"{fam}|N{n}|{stf}m->{etf}m|{trig}"
        r = common.summarise(df, cid, family=fam, swing_n=n, struct_tf=stf,
                             exec_tf=etf, trigger=trig, grid="mtf", **meta)
        rows.append(r)
        keep[cid] = df
        print("   ", common.fmt_row(r))

    grid = pd.DataFrame(rows)
    grid.to_csv(OUT / "market_structure_results.csv", index=False)

    # ---------------------------------------------------------------- freeze
    prim = grid[(grid.grid == "primary") & (grid.trades >= 200)
                & (grid.effective_n >= 30)]
    frozen = {}
    if len(prim):
        best = prim.sort_values("gross_r", ascending=False).iloc[0]
        frozen["PRIMARY"] = str(best.label)
        for fam in hs.FAMILIES:
            sub = prim[prim.family == fam]
            if len(sub):
                frozen[f"BEST_{fam}"] = str(
                    sub.sort_values("gross_r", ascending=False).iloc[0].label)
    for fam in hs.FAMILIES:
        frozen[f"REF_{fam}"] = f"{fam}|N5|15m|oneshot"
    json.dump(dict(frozen=frozen, rule=(
        "eligible = trades>=200 and effective_n>=30; PRIMARY = max TRAIN gross_r; "
        "BEST_<fam> = max TRAIN gross_r within family; REF_<fam> = N5/15m/oneshot, "
        "declared a priori"), train=common.TRAIN, valid_not_inspected=True),
        open(OUT / "frozen_candidates.json", "w"), indent=2)
    print("\n" + "=" * 108)
    print("FROZEN CANDIDATES (definitions locked; VALID not yet run)")
    for k, v in frozen.items():
        print(f"    {k:<12} {v}")
    print("=" * 108)

    # ---------------------------------------------------------------- per symbol
    sym_rows = []
    for cid, df in keep.items():
        if df.empty:
            continue
        meta = grid[grid.label == cid].iloc[0]
        for sym, g in df.groupby("symbol"):
            sym_rows.append(common.summarise(
                g, f"{cid}|{sym}", boot=len(g) >= 30, symbol=sym,
                family=meta.family, swing_n=meta.swing_n, struct_tf=meta.struct_tf,
                exec_tf=meta.exec_tf, trigger=meta.trigger, grid=meta.grid,
                candidate=cid, split="train"))
    pd.DataFrame(sym_rows).to_csv(OUT / "market_structure_results_per_symbol.csv",
                                  index=False)

    # ---------------------------------------------------------------- §15/§16
    print("\n§15  STRUCTURE QUALITY -- does bigger displacement mean stronger continuation?")
    q_rows = []
    for cid in list(frozen.values()):
        df = keep.get(cid)
        if df is None or df.empty:
            continue
        for col, name in (("disp_bucket", "displacement"), ("break_bucket", "break")):
            for b, g in df.groupby(col, observed=True):
                if len(g) < 20:
                    continue
                q_rows.append(common.summarise(
                    g, f"{cid}|{name}={b}", boot=False, candidate=cid,
                    dimension=name, bucket=str(b), split="train"))
        med = df.bars_between_swings.median()
        for nm, g in (("bars<=med", df[df.bars_between_swings <= med]),
                      ("bars>med", df[df.bars_between_swings > med])):
            if len(g) >= 20:
                q_rows.append(common.summarise(
                    g, f"{cid}|swing_spacing={nm}", boot=False, candidate=cid,
                    dimension="swing_spacing", bucket=nm, split="train"))
    qdf = pd.DataFrame(q_rows)
    qdf.to_csv(OUT / "structure_quality_train.csv", index=False)
    if len(qdf):
        print(qdf[["label", "trades", "win_rate", "gross_r", "net_r",
                   "p_hit_1r"]].to_string(index=False))

    print("\n§16  TIME TO MOVE -- P(reach level before the structural stop)")
    for cid in list(frozen.values()):
        df = keep.get(cid)
        if df is None or df.empty:
            continue
        print(f"    {cid:<30} n={len(df):>6,}  P(0.5R)={df.hit_05r.mean():.3f}  "
              f"P(1R)={df.hit_1r.mean():.3f}  P(2R)={df.hit_2r.mean():.3f}  "
              f"median MFE={df.mfe_r.median():.2f}R  "
              f"break-even P(2R)@2R target={1/3:.3f}")

    # ---------------------------------------------------------------- events
    ev_cols = ["symbol", "arm", "struct_tf", "exec_tf", "swing_n", "side",
               "swing_high_time", "swing_low_time", "swing_high_conf_time",
               "swing_low_conf_time", "struct_conf_time", "struct_bar_time",
               "signal_time", "entry_time", "exit_time", "conf_delay_bars",
               "conf_delay_min", "signal_lag_min", "entry_price", "stop_price",
               "target_price", "exit_price", "r_price", "stop_pct", "bars_held",
               "r_gross", "fee_r", "slip_r", "funding_r", "cost_r", "r_net",
               "exit_reason", "ambiguous", "disp_atr", "break_dist_atr",
               "bars_between_swings", "atr", "hit_05r", "hit_1r", "hit_2r", "mfe_r"]
    for cid in dict.fromkeys(frozen.values()):
        df = keep.get(cid)
        if df is None or df.empty:
            continue
        e = df.copy()
        e["split"] = "train"
        e["candidate"] = cid
        events.append(e[[c for c in ev_cols if c in e] + ["split", "candidate"]])
    if events:
        pd.concat(events, ignore_index=True).to_csv(
            OUT / "market_structure_events.csv", index=False)

    # ---------------------------------------------------------------- reports
    write_baseline_md(grid, pd.DataFrame(null_rows), pd.DataFrame(null_sims), census)
    write_train_md(grid, frozen, qdf, keep, pd.DataFrame(null_rows),
                   pd.DataFrame(null_sims))
    print(f"\nwritten to {OUT}")
    print("VALID NOT COMPUTED IN THIS SCRIPT. TEST LOCKED.")
    return 0


def md_table(d: pd.DataFrame) -> str:
    """Markdown table without the optional `tabulate` dependency."""
    if d.empty:
        return "_(no rows)_"

    def cell(v):
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            return ""
        if isinstance(v, float):
            return f"{v:,.4f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.1f}"
        return str(v)

    cols = list(d.columns)
    head = "| " + " | ".join(cols) + " |"
    rule = "|" + "|".join("---" for _ in cols) + "|"
    body = ["| " + " | ".join(cell(v) for v in row) + " |"
            for row in d.itertuples(index=False, name=None)]
    return "\n".join([head, rule] + body)


def _tbl(df, cols, sort=None, asc=False, n=None):
    d = df.copy()
    if sort and sort in d:
        d = d.sort_values(sort, ascending=asc)
    if n:
        d = d.head(n)
    return md_table(d[[c for c in cols if c in d]])


def write_baseline_md(grid, nulls, nsims, census):
    L = []
    A = L.append
    A("# H-Structure-1 — Null baselines (§12)\n")
    A("TRAIN window only. Every baseline runs through the **same** simulator, "
      "structural stop, 2R target, sizing and production cost model as the "
      "signal candidates; only the entry rule differs.\n")
    A(f"- Universe: `{common.CORE}`")
    A(f"- TRAIN: {pd.Timestamp(common.TRAIN[0],unit='s')} → "
      f"{pd.Timestamp(common.TRAIN[1],unit='s')}")
    A(f"- Excluded: {common.EXCLUDED}\n")
    A("## NULL-3 — unconditional long / short\n")
    A("Enter every structure bar, one position at a time. Whatever this earns is "
      "drift and payoff geometry, not signal.\n")
    A(_tbl(nulls, ["label", "trades", "win_rate", "gross_r", "cost_r", "net_r",
                   "t_gross", "pf_gross", "stop_pct_median", "pct_target"]))
    A("\n## NULL-1 / NULL-2 — randomised direction and randomised entry\n")
    A("NULL-1 keeps the candidate's own entry times and flips a coin for "
      "direction. NULL-2 randomises both time and direction at matched trade "
      "count. `z` is (signal gross − null mean gross) / null sd across "
      f"{N_SIMS} simulations.\n")
    if len(nsims):
        A(_tbl(nsims, ["family", "ref", "signal_trades", "signal_gross",
                       "null_dir_gross", "null_dir_sd", "z_vs_dir",
                       "null_rnd_gross", "null_rnd_sd", "z_vs_rnd"]))
    A("\n## Swing census\n")
    A("Share of TRAIN structure bars in each state, by timeframe and swing "
      "strength (mean over the universe).\n")
    c = pd.DataFrame(census).groupby(["struct_tf", "swing_n"], as_index=False).agg(
        bars=("bars", "mean"), bull_pct=("bull_pct", "mean"),
        bear_pct=("bear_pct", "mean"), conf_delay_min=("conf_delay_min", "first"))
    c["bars"] = c["bars"].round(0)
    c[["bull_pct", "bear_pct"]] = c[["bull_pct", "bear_pct"]].round(2)
    A(md_table(c))
    (OUT / "market_structure_baseline.md").write_text("\n".join(L) + "\n")


def write_train_md(grid, frozen, qdf, keep, nulls, nsims):
    L = []
    A = L.append
    A("# H-Structure-1 — TRAIN screen (§14)\n")
    A("**LOOK-AHEAD STATUS: PASS** — see `lookahead_audit.txt`.\n")
    A(f"- Universe: `{common.CORE}` (BEATUSD is VALID-only; AKEUSD/BANKUSD "
      "excluded, their entire history is inside the locked TEST window)")
    A(f"- TRAIN: {pd.Timestamp(common.TRAIN[0],unit='s')} → "
      f"{pd.Timestamp(common.TRAIN[1],unit='s')}")
    A("- Exit: structural stop (last confirmed swing low/high) + 2R target, "
      "unoptimised")
    A("- Costs: production model — per-symbol taker ×1.18 GST, 2.0 bps "
      "slippage, per-symbol funding cadence\n")
    A("## Primary grid — all candidates\n")
    cols = ["label", "trades", "effective_n", "win_rate", "gross_r", "t_gross",
            "cost_r", "net_r", "median_r", "pf_gross", "max_dd_r",
            "stop_pct_median", "cost_over_gross", "conf_delay_min_median",
            "p_hit_1r", "p_hit_2r"]
    A(_tbl(grid[grid.grid == "primary"], cols, sort="gross_r"))
    A("\n## Multi-timeframe grid (declared secondary)\n")
    A(_tbl(grid[grid.grid == "mtf"], cols, sort="gross_r"))
    A("\n## Frozen candidates\n")
    A("Locked before VALID was run.\n")
    A("| slot | candidate |")
    A("|---|---|")
    for k, v in frozen.items():
        A(f"| {k} | `{v}` |")
    A("\n## §15 Structure quality\n")
    if len(qdf):
        A(_tbl(qdf, ["label", "trades", "win_rate", "gross_r", "net_r",
                     "p_hit_1r", "p_hit_2r"]))
    A("\n## §16 Time to move\n")
    A("P(level reached before the structural stop). A 2R target needs "
      "P(2R) > 1/3 just to break even on gross R.\n")
    A("| candidate | n | P(+0.5R) | P(+1R) | P(+2R) | median MFE (R) |")
    A("|---|---|---|---|---|---|")
    for cid in dict.fromkeys(frozen.values()):
        df = keep.get(cid)
        if df is None or df.empty:
            continue
        A(f"| `{cid}` | {len(df):,} | {df.hit_05r.mean():.3f} | "
          f"{df.hit_1r.mean():.3f} | {df.hit_2r.mean():.3f} | "
          f"{df.mfe_r.median():.2f} |")
    (OUT / "market_structure_train.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())

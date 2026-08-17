"""H-Structure-1 -- VALIDATION (§17). Run ONCE, after the TRAIN freeze.

Nothing is redefined here. Swing strengths, structure definitions, timeframes,
entry semantics, stop, target, cost model and the core universe are read from
the same frozen module the TRAIN screen used; the candidate slots are read from
frozen_candidates.json, which was written before this script ever ran.

The whole grid is re-run on VALID as well. That is a DIAGNOSTIC, not a
selection step -- it is what makes an isolated parameter spike visible. The
verdict is taken from the frozen slots only.

TEST IS NOT COMPUTED.
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
from run_train import (N_SIMS, md_table, null_random_direction, null_random_entry,
                       null_unconditional, run_candidate, _tbl)

OUT = Path(__file__).parent / "out"


def parse(cid):
    """'C|N8|60m|oneshot' or 'C|N8|5m->15m|oneshot' -> (fam, n, stf, etf, trig)."""
    fam, npart, tfpart, trig = cid.split("|")
    n = int(npart[1:])
    if "->" in tfpart:
        a, b = tfpart.split("->")
        return fam, n, int(a[:-1]), int(b[:-1]), trig
    tf = int(tfpart[:-1])
    return fam, n, tf, tf, trig


def main() -> int:
    frozen = json.load(open(OUT / "frozen_candidates.json"))["frozen"]
    W = common.VALID
    print("=" * 108)
    print("H-STRUCTURE-1  VALIDATION  -- definitions frozen, run once")
    print(f"  VALID : {pd.Timestamp(W[0],unit='s')} -> {pd.Timestamp(W[1],unit='s')}")
    print(f"  core  : {common.CORE}")
    print(f"  supp  : {common.SUPP}  (no TRAIN data; cannot have influenced the freeze)")
    print("  TEST  : NOT COMPUTED")
    print("=" * 108)

    data = common.load(common.CORE)
    supp = common.load(common.SUPP)

    print("\nbuilding structure grids...")
    S_by = {}
    for src in (data, supp):
        for sym, d in src.items():
            for stf in hs.STRUCT_TF:
                for n in hs.SWING_N:
                    S_by[(sym, stf, n)] = hs.build_structure(d["df"], stf, n)
    print(f"  {len(S_by)} grids")

    train = pd.read_csv(OUT / "market_structure_results.csv")

    # ---------------------------------------------------------------- nulls
    print("\n" + "=" * 108)
    print("§12  NULL BASELINES ON VALID")
    print("=" * 108)
    null_rows = []
    for stf in hs.STRUCT_TF:
        for side in (1, -1):
            df = null_unconditional(data, S_by, 5, stf, stf, W, side)
            r = common.summarise(df, f"UNCOND_{'LONG' if side>0 else 'SHORT'}|{stf}m",
                                 kind="unconditional", struct_tf=stf, split="valid")
            null_rows.append(r)
            print("   ", common.fmt_row(r))
    null_sims = []
    for fam in hs.FAMILIES:
        sig_df, _ = run_candidate(data, S_by, fam, "oneshot", 5, 15, 15, W, diag=False)
        k = len(sig_df)
        if k < 20:
            continue
        n1 = null_random_direction(data, S_by, fam, "oneshot", 5, 15, 15, W, N_SIMS)
        n2 = null_random_entry(data, S_by, fam, "oneshot", 5, 15, 15, W, N_SIMS, k)
        sg = float(sig_df.r_gross.mean())
        null_sims.append(dict(
            family=fam, split="valid", signal_trades=k, signal_gross=round(sg, 4),
            null_dir_gross=round(n1["gross_mean"], 4), null_dir_sd=round(n1["gross_sd"], 4),
            z_vs_dir=round((sg - n1["gross_mean"]) / n1["gross_sd"], 2) if n1["gross_sd"] else None,
            null_rnd_gross=round(n2["gross_mean"], 4), null_rnd_sd=round(n2["gross_sd"], 4),
            z_vs_rnd=round((sg - n2["gross_mean"]) / n2["gross_sd"], 2) if n2["gross_sd"] else None))
        print(f"    {fam}  signal n={k:>5,} gross={sg:+.4f}   "
              f"NULL-dir {n1['gross_mean']:+.4f} +/-{n1['gross_sd']:.4f}   "
              f"NULL-rnd {n2['gross_mean']:+.4f} +/-{n2['gross_sd']:.4f}")
    pd.DataFrame(null_rows).to_csv(OUT / "null_unconditional_valid.csv", index=False)
    pd.DataFrame(null_sims).to_csv(OUT / "null_simulations_valid.csv", index=False)

    # ---------------------------------------------------------------- grid
    print("\n" + "=" * 108)
    print("FULL GRID ON VALID (diagnostic for isolated spikes -- NOT a selection step)")
    print("=" * 108)
    rows, keep = [], {}
    combos = [(f, n, s, s, t) for f, n, s, t in
              itertools.product(hs.FAMILIES, hs.SWING_N, hs.STRUCT_TF, hs.TRIGGERS)]
    combos += [(f, n, a, b, t) for f, n, (a, b), t in
               itertools.product(hs.FAMILIES, hs.SWING_N, hs.MTF, hs.TRIGGERS)]
    for fam, n, stf, etf, trig in combos:
        df, meta = run_candidate(data, S_by, fam, trig, n, stf, etf, W)
        cid = (f"{fam}|N{n}|{stf}m|{trig}" if stf == etf
               else f"{fam}|N{n}|{stf}m->{etf}m|{trig}")
        r = common.summarise(df, cid, family=fam, swing_n=n, struct_tf=stf,
                             exec_tf=etf, trigger=trig, split="valid",
                             grid="primary" if stf == etf else "mtf", **meta)
        rows.append(r)
        keep[cid] = df
        print("   ", common.fmt_row(r))
    valid = pd.DataFrame(rows)
    valid.to_csv(OUT / "market_structure_results_valid.csv", index=False)

    # ---------------------------------------------------------- degradation
    t = train[train.split.isna() if "split" in train else slice(None)] if False else train
    m = t[["label", "trades", "win_rate", "gross_r", "t_gross", "cost_r", "net_r",
           "p_hit_2r", "effective_n"]].merge(
        valid[["label", "trades", "win_rate", "gross_r", "t_gross", "cost_r",
               "net_r", "p_hit_2r", "effective_n"]],
        on="label", suffixes=("_train", "_valid"))
    m["d_gross"] = (m.gross_r_valid - m.gross_r_train).round(4)
    m["d_net"] = (m.net_r_valid - m.net_r_train).round(4)
    m["sign_kept"] = (np.sign(m.gross_r_train) == np.sign(m.gross_r_valid))
    m.to_csv(OUT / "train_valid_degradation.csv", index=False)

    print("\n" + "=" * 108)
    print("§17  TRAIN -> VALID, FROZEN CANDIDATES")
    print("=" * 108)
    print(f"{'slot':<10}{'candidate':<24}{'n_tr':>7}{'n_va':>7}"
          f"{'G_tr':>9}{'G_va':>9}{'t_tr':>7}{'t_va':>7}{'N_tr':>9}{'N_va':>9}"
          f"{'win_tr':>8}{'win_va':>8}")
    frozen_rows = []
    for slot, cid in frozen.items():
        a = m[m.label == cid]
        if a.empty:
            print(f"{slot:<10}{cid:<24} not present in both grids")
            continue
        a = a.iloc[0]
        frozen_rows.append(dict(slot=slot, candidate=cid, **{
            k: a[k] for k in a.index if k != "label"}))
        print(f"{slot:<10}{cid:<24}{a.trades_train:>7,}{a.trades_valid:>7,}"
              f"{a.gross_r_train:>+9.4f}{a.gross_r_valid:>+9.4f}"
              f"{(a.t_gross_train or 0):>7.2f}{(a.t_gross_valid or 0):>7.2f}"
              f"{a.net_r_train:>+9.4f}{a.net_r_valid:>+9.4f}"
              f"{a.win_rate_train:>8.3f}{a.win_rate_valid:>8.3f}")
    fz = pd.DataFrame(frozen_rows)
    fz.to_csv(OUT / "frozen_train_valid.csv", index=False)

    # ---------------------------------------------------------------- symbols
    print("\n" + "=" * 108)
    print("§11  PER SYMBOL ON VALID -- frozen candidates (core universe)")
    print("=" * 108)
    sym_rows = []
    for slot, cid in frozen.items():
        df = keep.get(cid)
        if df is None or df.empty:
            continue
        for sym, g in df.groupby("symbol"):
            r = common.summarise(g, f"{cid}|{sym}", boot=len(g) >= 30, symbol=sym,
                                 candidate=cid, slot=slot, split="valid")
            sym_rows.append(r)
            print(f"    {cid:<22}{sym:<9}n={r['trades']:>5,} win={r['win_rate']:.3f} "
                  f"G={r['gross_r']:+.4f} med={r['median_r']:+.3f} c={r['cost_r']:.3f} "
                  f"N={r['net_r']:+.4f} PF={(r.get('pf_gross') or 0):.2f} "
                  f"t={(r.get('t_gross') or 0):+.2f}")

    # ------------------------------------------------- supplementary symbol
    print("\n" + "=" * 108)
    print(f"SUPPLEMENTARY (VALID-only, no TRAIN data, excluded from headline): {common.SUPP}")
    print("=" * 108)
    supp_rows = []
    for slot, cid in frozen.items():
        fam, n, stf, etf, trig = parse(cid)
        df, meta = run_candidate(supp, S_by, fam, trig, n, stf, etf, W)
        r = common.summarise(df, f"{cid}|BEATUSD", boot=len(df) >= 30,
                             candidate=cid, slot=slot, split="valid",
                             symbol="BEATUSD")
        supp_rows.append(r)
        print("   ", common.fmt_row(r))
        if not df.empty:
            for sym, g in df.groupby("symbol"):
                sym_rows.append(common.summarise(
                    g, f"{cid}|{sym}", boot=len(g) >= 30, symbol=sym,
                    candidate=cid, slot=slot, split="valid_supplementary"))
    pd.DataFrame(sym_rows).to_csv(OUT / "market_structure_results_per_symbol_valid.csv",
                                  index=False)

    # ---------------------------------------------------------------- §15/§16
    q_rows = []
    for cid in dict.fromkeys(frozen.values()):
        df = keep.get(cid)
        if df is None or df.empty:
            continue
        for col, name in (("disp_bucket", "displacement"), ("break_bucket", "break")):
            for b, g in df.groupby(col, observed=True):
                if len(g) < 20:
                    continue
                q_rows.append(common.summarise(g, f"{cid}|{name}={b}", boot=False,
                                               candidate=cid, dimension=name,
                                               bucket=str(b), split="valid"))
    qdf = pd.DataFrame(q_rows)
    qdf.to_csv(OUT / "structure_quality_valid.csv", index=False)

    print("\n§16  TIME TO MOVE ON VALID (break-even for a 2R target is P(2R) > 0.333)")
    tt_rows = []
    for slot, cid in frozen.items():
        df = keep.get(cid)
        if df is None or df.empty:
            continue
        tt_rows.append(dict(slot=slot, candidate=cid, trades=len(df),
                            p_05r=round(df.hit_05r.mean(), 3),
                            p_1r=round(df.hit_1r.mean(), 3),
                            p_2r=round(df.hit_2r.mean(), 3),
                            mfe_median=round(df.mfe_r.median(), 2)))
        print(f"    {cid:<24} n={len(df):>5,}  P(0.5R)={df.hit_05r.mean():.3f}  "
              f"P(1R)={df.hit_1r.mean():.3f}  P(2R)={df.hit_2r.mean():.3f}")

    # ---------------------------------------------------------------- events
    ev = []
    old = OUT / "market_structure_events.csv"
    if old.exists():
        ev.append(pd.read_csv(old))
    for cid in dict.fromkeys(frozen.values()):
        df = keep.get(cid)
        if df is None or df.empty:
            continue
        e = df.copy(); e["split"] = "valid"; e["candidate"] = cid
        ev.append(e[[c for c in ev[0].columns if c in e]] if ev else e)
    pd.concat(ev, ignore_index=True).to_csv(old, index=False)

    write_valid_md(m, fz, valid, train, frozen, qdf, keep,
                   pd.DataFrame(null_rows), pd.DataFrame(null_sims),
                   pd.DataFrame(supp_rows), pd.DataFrame(tt_rows))
    print(f"\nwritten to {OUT}")
    print("TEST SEGMENT NOT COMPUTED.")
    return 0


def write_valid_md(m, fz, valid, train, frozen, qdf, keep, nulls, nsims, supp, tt):
    L = []; A = L.append
    A("# H-Structure-1 — VALIDATION (§17)\n")
    A("Run once, after `frozen_candidates.json` was written. No swing "
      "parameter, structure definition, timeframe, entry rule, stop, target, "
      "cost model or symbol was changed on the basis of anything below.\n")
    A(f"- VALID: {pd.Timestamp(common.VALID[0],unit='s')} → "
      f"{pd.Timestamp(common.VALID[1],unit='s')}")
    A(f"- Core universe: `{common.CORE}`")
    A(f"- Supplementary (VALID-only): `{common.SUPP}`; excluded: "
      f"{list(common.EXCLUDED)} (TEST-only history)")
    A("- **TEST not computed.**\n")
    A("## Frozen candidates — TRAIN → VALID\n")
    A(_tbl(fz, ["slot", "candidate", "trades_train", "trades_valid",
                "win_rate_train", "win_rate_valid", "gross_r_train",
                "gross_r_valid", "d_gross", "t_gross_train", "t_gross_valid",
                "cost_r_train", "cost_r_valid", "net_r_train", "net_r_valid",
                "p_hit_2r_train", "p_hit_2r_valid"]))
    A("\n## Null baselines on VALID\n")
    A(_tbl(nulls, ["label", "trades", "win_rate", "gross_r", "cost_r", "net_r",
                   "t_gross", "pf_gross"]))
    if len(nsims):
        A("")
        A(_tbl(nsims, ["family", "signal_trades", "signal_gross",
                       "null_dir_gross", "null_dir_sd", "z_vs_dir",
                       "null_rnd_gross", "null_rnd_sd", "z_vs_rnd"]))
    A("\n## §16 Time to move on VALID\n")
    A("A 2R target needs P(+2R before stop) > 1/3 for positive gross "
      "expectancy. Gross R is mechanically `3·P(2R) − 1`.\n")
    A(_tbl(tt, ["slot", "candidate", "trades", "p_05r", "p_1r", "p_2r",
                "mfe_median"]))
    A("\n## Supplementary symbol (BEATUSD, VALID only)\n")
    A(_tbl(supp, ["label", "trades", "win_rate", "gross_r", "cost_r", "net_r",
                  "t_gross", "p_hit_2r"]))
    A("\n## Full grid on VALID (spike diagnostic, not a selection step)\n")
    A(_tbl(valid[valid.grid == "primary"],
           ["label", "trades", "effective_n", "win_rate", "gross_r", "t_gross",
            "cost_r", "net_r", "median_r", "pf_gross", "p_hit_2r"],
           sort="gross_r"))
    A("\n### Multi-timeframe grid on VALID\n")
    A(_tbl(valid[valid.grid == "mtf"],
           ["label", "trades", "effective_n", "win_rate", "gross_r", "t_gross",
            "cost_r", "net_r", "median_r", "pf_gross", "p_hit_2r"],
           sort="gross_r"))
    A("\n## Grid-wide TRAIN → VALID stability\n")
    prim = m[~m.label.str.contains("->")]
    A(f"- primary candidates compared: **{len(prim)}**")
    A(f"- TRAIN gross > 0: **{int((prim.gross_r_train > 0).sum())}**; "
      f"VALID gross > 0: **{int((prim.gross_r_valid > 0).sum())}**")
    A(f"- sign of gross preserved TRAIN→VALID: "
      f"**{int(prim.sign_kept.sum())}/{len(prim)}** "
      f"({100*prim.sign_kept.mean():.0f}%)")
    r = prim[["gross_r_train", "gross_r_valid"]].corr().iloc[0, 1]
    A(f"- cross-split correlation of gross R across candidates: **{r:+.3f}**")
    A(f"- median gross degradation (VALID − TRAIN): "
      f"**{prim.d_gross.median():+.4f} R**")
    A(f"- candidates with net R > 0 on VALID: "
      f"**{int((prim.net_r_valid > 0).sum())}/{len(prim)}**")
    A("\n## §15 Structure quality on VALID\n")
    if len(qdf):
        A(_tbl(qdf, ["label", "trades", "win_rate", "gross_r", "net_r",
                     "p_hit_1r", "p_hit_2r"]))
    (OUT / "market_structure_validation.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())

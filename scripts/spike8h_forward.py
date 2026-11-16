"""Forward paper test of the spike fade with a 10% stop and an 8-hour clock -- docs/spike8h_forward_prereg.md.
The rule is frozen there. This script only (1) reads the paper runner's picks, (2) fetches Delta's public 1m candles
for every finished day, (3) appends to the ledger and prints the running result. Read-only everywhere.

    python scripts/spike8h_forward.py pull      # host: read new picks from production (aws ssm, SELECT only)
    PYTHONPATH=. python scripts/spike8h_forward.py run     # container: update out/careful/spike8h_forward.csv
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

OUT = Path("out/careful")
PICKS = OUT / "forward_picks.csv"
LEDGER = OUT / "spike8h_forward.csv"
START_DAY = "2026-11-15"
HOST, REGION = "i-0aac6d85d802033bf", "ap-south-1"
FEE_IN, FEE_OUT = 0.000212, 0.000531
N = 2881


REMOTE = """
import asyncio, os, asyncpg
from app.persistence.db_auth import connect_kwargs
async def main():
    con = await asyncpg.connect(**connect_kwargs(os.environ['DATABASE_URL']))
    rows = await con.fetch("SELECT DISTINCT to_char(day AT TIME ZONE 'UTC','YYYY-MM-DD') d, symbol, rank "
                           "FROM fade_positions WHERE day >= '%s' ORDER BY 1, 3")
    for r in rows:
        print("PICK,%%s,%%s,%%s" %% (r['d'], r['symbol'], r['rank']))
    await con.close()
asyncio.run(main())
""" % START_DAY


def pull():
    py = REMOTE
    cmd = "docker exec -i -e PYTHONPATH=/app deltabot python - <<'PYEOF'\n" + py + "\nPYEOF"
    spec = json.dumps({"InstanceIds": [HOST], "DocumentName": "AWS-RunShellScript", "Parameters": {"commands": [cmd]}})
    cid = subprocess.check_output(["aws", "ssm", "send-command", "--region", REGION, "--cli-input-json", spec,
                                   "--query", "Command.CommandId", "--output", "text"], text=True).strip()
    for _ in range(20):
        time.sleep(3)
        r = json.loads(subprocess.check_output(["aws", "ssm", "get-command-invocation", "--region", REGION, "--command-id", cid,
                                                "--instance-id", HOST, "--output", "json"], text=True))
        if r["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
            break
    if r["Status"] != "Success":
        raise SystemExit(f"pull failed: {r['Status']} {r.get('StandardErrorContent', '')[:300]}")
    rows = sorted({tuple(x.split(",")[1:]) for x in r["StandardOutputContent"].splitlines() if x.startswith("PICK,")})
    PICKS.write_text("day,symbol,rank\n" + "".join(",".join(x) + "\n" for x in rows))
    print(f"{len(rows)} picks, {rows[0][0]} .. {rows[-1][0]} -> {PICKS}")


def candles(sym, t0):
    req = urllib.request.Request(f"https://api.india.delta.exchange/v2/history/candles?resolution=1m&symbol={sym}&start={t0}&end={t0 + 2 * 86400}",
                                 headers={"User-Agent": "deltabt-research"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r).get("result") or []
        except Exception:  # noqa: BLE001
            time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"fetch failed {sym} {t0}")


def run():
    import numpy as np
    import pandas as pd
    sys.path.insert(0, str(Path(__file__).parent))
    from spike_flip_back import trade
    P = pd.read_csv(PICKS, parse_dates=["day"])
    L = pd.read_csv(LEDGER, parse_dates=["day"]) if LEDGER.exists() else pd.DataFrame()
    done = set(zip(L.day, L.symbol)) if len(L) else set()
    now, new = time.time(), []
    for x in P.itertuples():
        t0 = int(x.day.timestamp())
        if (x.day, x.symbol) in done or now < t0 + 86400 + 8 * 3600 + 600:     # the day's last possible exit must be past
            continue
        hi, lo, cl = np.full(N, -np.inf), np.full(N, np.inf), np.full(N, np.nan)
        for c in candles(x.symbol, t0):
            k = (int(c["time"]) - t0) // 60
            if 0 <= k < N:
                hi[k], lo[k], cl[k] = float(c["high"]), float(c["low"]), float(c["close"])
        traded = int(np.isfinite(cl).sum())
        cl = pd.Series(cl).ffill().to_numpy()
        row = dict(day=x.day, symbol=x.symbol, rank=x.rank, minutes_traded=traded, entry=np.nan)
        if traded and np.isfinite(cl[2]):
            row["entry"] = cl[2] * 1.05
            for fill in ("STRICT", "LENIENT"):
                r = trade(hi, lo, cl, None, 8, fill)
                row[f"{fill.lower()}_ret"], row[f"{fill.lower()}_how"], row[f"{fill.lower()}_min"] = (r if r else (np.nan, "NO FILL", 0))
        new.append(row)
        time.sleep(0.2)
    if new:
        L = pd.concat([L, pd.DataFrame(new)], ignore_index=True)
        L.to_csv(LEDGER, index=False)
    if not len(L):
        print("no finished day yet")
        return
    f = L[L.strict_how.notna() & (L.strict_how != "NO FILL")]
    print(f"ledger: {len(L)} picks on {L.day.nunique()} days ({L.day.min():%Y-%m-%d} .. {L.day.max():%Y-%m-%d}); {len(f)} filled; {len(new)} new this run")
    for fill in ("strict", "lenient"):
        r = f[f"{fill}_ret"]
        if len(r):
            net = r - FEE_IN - FEE_OUT * (1 - r)
            print(f"  {fill.upper():8} before fees: win {100 * (r > 0).mean():3.0f}%  avg win {100 * r[r > 0].mean():+.2f}%  avg loss {100 * r[r <= 0].mean():+.2f}%  "
                  f"mean {100 * r.mean():+.3f}%   after real fees: mean {100 * net.mean():+.3f}%")
    print(f"  verdict due at 60 filled trades (now {len(f)}); until then this is a progress note.")
    print(L.tail(8).to_string(index=False))


if __name__ == "__main__":
    {"pull": pull, "run": run}[sys.argv[1]]()

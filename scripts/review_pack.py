"""Build a self-contained REVIEW PACK of every deployed paper strategy and the real account, for independent
reviews by different LLMs (owner 2026-11-11: "run the review on all paper strategies ... using different LLMs").

    python3 scripts/review_pack.py                 # -> out/review_pack/<UTC date>/ (gitignored: trading data)

Every reviewer gets the SAME folder and the SAME prompt (PROMPT.md) and nothing else -- no CONTEXT.md, no
conclusions of ours -- so the reviews are independent. Contents:

  data/          the live tables as CSV, read-only over SSM from the bot host (no order ids, no keys)
  rules/         each strategy's written rules (the pre-registrations and module docs)
  expectations/  the backtest results each strategy was started on
  PROMPT.md      the review question;   MANIFEST.md   what each file is, with sha256

Needs AWS credentials that can run SSM on the bot host (the same as every probe this repo runs).
"""
from __future__ import annotations

import base64
import datetime as dt
import gzip
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGION = "ap-south-1"
HOST_TAG = "deltabt-paper-bot"

#: table -> SELECT (read-only). Order ids (entry_cid, exit_cids) and venue blobs are left out on purpose.
TABLES = {
    "paper_fade_positions": "SELECT account, day, symbol, rank, prev_change, prev_turnover, atrp, entry_time, entry_price, "
                            "stop_price, target_price, contracts, contract_value, equity_before, status, skip_reason, "
                            "exit_time, exit_price, exit_reason, gross, fees, funding, pnl, risk, r "
                            "FROM fade_positions ORDER BY day, account, rank",
    "paper_fade_days": "SELECT day, status, at FROM fade_days ORDER BY day",
    "real_fade_live_positions": "SELECT day, symbol, rank, status, skip_reason, contracts, leverage, risk_pct, balance_before, "
                                "paper_entry, ref_price, limit_price, stop_price, entry_time, entry_price, entry_fee, "
                                "exit_time, exit_price, exit_fee, exit_reason, pnl, r FROM fade_live_positions ORDER BY day, id",
    "real_fade_live_state": "SELECT status, reason, at, balance, balance_at, high_water, run_version, added_usd FROM fade_live_state",
    "real_fade_live_events": "SELECT at, kind, symbol FROM fade_live_events ORDER BY at",
    "paper_funding_positions": "SELECT opened_period, symbol, side, signal, contracts, contract_value, entry_time, entry_price, "
                               "entry_mark, status, exit_time, exit_price, exit_mark, settlements, funding, gross, fees, pnl "
                               "FROM funding_positions ORDER BY entry_time, symbol",
    "paper_funding_periods": "SELECT period, status, equity, detail FROM funding_periods ORDER BY period",
    "paper_option_legs": "SELECT expiry, underlying, kind, symbol, strike, spot, entry_time, mark, best_bid, best_ask, bid_size, "
                         "mark_iv, delta, contracts, contract_value, notional, premium, fee_in, status, skip_reason, "
                         "settled_at, settle_value, fee_out, pnl FROM option_legs ORDER BY expiry, underlying, kind",
    "paper_option_days": "SELECT expiry, status, at FROM option_days ORDER BY expiry",
}

RULES = {
    "rule_A_ladder_real_and_paper_LADDER.md": "docs/fade_live_ladder_prereg.md",
    "rule_A_real_version1.md": "docs/fade_live_prereg.md",
    "rule_QUICK_real_plan_never_run.md": "docs/fade_live_quick_prereg.md",
    "options_strangles.md": "docs/option_selling_prereg.md",
    "options_paper_how_to_read.md": "docs/options_paper_read.md",
}
MODULE_DOCS = {
    "paper_accounts_A_QUICK_LADDER.md": "live/fade.py",
    "funding_collection.md": "live/funding_paper.py",
    "options_paper_account.md": "live/options_paper.py",
}
EXPECTATIONS = [
    "out/backtest_survivors.txt", "out/compare_ladder_funding.txt", "out/funding_collection_detail.txt",
    "out/gainer_fade/delta_picks.txt", "out/gainer_fade/hold_length.txt", "out/gainer_fade/costed.txt",
    "out/gainer_fade/binance_judge.txt", "out/options/build.txt", "out/options/test.txt",
]

PROMPT = """# Review request

You are an independent reviewer of a small crypto trading operation on Delta Exchange India (perpetual futures and
daily options). Use ONLY the files in this folder. Other reviewers get the same folder; do not assume any
conclusion is already known.

Strategies to review (rules in `rules/`, backtest expectations in `expectations/`, live records in `data/`):
1. Rule A — short the two previous-UTC-day top gainers for 24 h, stop at 2 x ATR14%. Paper account `A` ($250,
   2% risk) and the REAL account (`real_*`, run 3 from 2026-11-09, ladder 10/5/3% risk; run 1 before it).
2. QUICK — rule A plus a take-profit at 0.2 x the stop distance. Paper account `QUICK`.
3. LADDER — rule A with the real account's sizing, on paper ($1,500). Paper account `LADDER`.
4. Funding collection — every 3 days, short the highest-funding fifth and long the lowest. `paper_funding_*`
   (started 2026-11-12; it may have few or no rows yet).
5. Option strangles — sell 1%-OTM BTC and ETH strangles daily. `paper_option_*`.

For EACH strategy answer:
- **Record so far:** trades/periods, win rate, mean result per trade (R where defined, else % or $), total, and
  the standard error of the mean.
- **Versus expectation:** is the live/paper record consistent with its backtest, given the sample size? Say
  how many standard errors apart, and how many more trades would be needed to tell an edge from zero.
- **Implementation:** anything in the data suggesting the strategy is NOT being run as its rules say (fills vs
  marks, skipped or missed days, funding sign, sizing, stops, exits, paper vs real differences).
- **Risk:** the worst outcome the rules allow, and whether the current size is reasonable for that.
- **Verdict:** continue / change (what) / stop, with your confidence (low / medium / high) and the one number
  that would most change your mind.

Finish with a one-table summary across strategies and a short list of the three most important actions.
Show your arithmetic for every number you state. Say "not enough data" where that is the honest answer.
"""


def sh(*a: str) -> str:
    return subprocess.run(list(a), check=True, capture_output=True, text=True).stdout.strip()


def host() -> str:
    return sh("aws", "ec2", "describe-instances", "--region", REGION, "--filters", f"Name=tag:Name,Values={HOST_TAG}",
              "Name=instance-state-name,Values=running", "--query", "Reservations[0].Instances[0].InstanceId",
              "--output", "text")


def remote_csv(instance: str, sql: str) -> str:
    """Run one read-only SELECT in the bot container; returns CSV text (gzip+base64 over SSM's 24 KB limit)."""
    py = f"""
import asyncio, os, csv, io, gzip, base64, json
import asyncpg
from app.persistence.db_auth import connect_kwargs
async def main():
    c = await asyncpg.connect(**connect_kwargs(os.environ["DATABASE_URL"]))
    try:
        if not await c.fetchval("SELECT to_regclass($1) IS NOT NULL", {json.dumps(sql.split(" FROM ")[1].split()[0])}):
            print("MISSING"); return
        rows = await c.fetch({json.dumps(sql)})
    finally:
        await c.close()
    buf = io.StringIO(); w = csv.writer(buf)
    if rows:
        w.writerow(rows[0].keys())
        for r in rows: w.writerow(["" if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else v) for v in r.values()])
    print("B64:" + base64.b64encode(gzip.compress(buf.getvalue().encode())).decode())
asyncio.run(main())
"""
    cmd = "docker exec -i -e PYTHONPATH=/app deltabot python - <<'PYEOF'\n" + py + "PYEOF\n"
    doc = {"InstanceIds": [instance], "DocumentName": "AWS-RunShellScript", "Comment": "review pack (read-only)",
           "Parameters": {"commands": [cmd]}}
    cid = sh("aws", "ssm", "send-command", "--region", REGION, "--cli-input-json", json.dumps(doc),
             "--query", "Command.CommandId", "--output", "text")
    subprocess.run(["aws", "ssm", "wait", "command-executed", "--region", REGION, "--command-id", cid,
                    "--instance-id", instance], check=False)
    out = sh("aws", "ssm", "get-command-invocation", "--region", REGION, "--command-id", cid, "--instance-id", instance,
             "--query", "StandardOutputContent", "--output", "text")
    if "MISSING" in out:
        return ""
    line = next((x for x in out.splitlines() if x.startswith("B64:")), None)
    if line is None:
        raise RuntimeError(f"no data returned: {out[-300:]}")
    return gzip.decompress(base64.b64decode(line[4:])).decode()


def module_doc(path: str) -> str:
    src = (ROOT / path).read_text()
    start = src.index('"""') + 3
    return src[start:src.index('"""', start)].strip() + "\n"


def main() -> None:
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    out = ROOT / "out" / "review_pack" / day
    for sub in ("data", "rules", "expectations"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    inst = host()
    print(f"bot host {inst}; pack -> {out}", flush=True)
    rows = {}
    for name, sql in TABLES.items():
        csv_text = remote_csv(inst, sql)
        (out / "data" / f"{name}.csv").write_text(csv_text)
        rows[name] = max(len(csv_text.splitlines()) - 1, 0)
        print(f"  data/{name}.csv: {rows[name]} rows", flush=True)
    for dst, src in RULES.items():
        (out / "rules" / dst).write_text((ROOT / src).read_text())
    for dst, src in MODULE_DOCS.items():
        (out / "rules" / dst).write_text(f"(from {src})\n\n" + module_doc(src))
    for src in EXPECTATIONS:
        (out / "expectations" / src.replace("out/", "").replace("/", "__")).write_text((ROOT / src).read_text())
    (out / "PROMPT.md").write_text(PROMPT)
    lines = [f"# Review pack {day}", "", f"Built {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC from the live "
             "database (read-only) and the repository. Give every reviewer this folder and PROMPT.md, nothing else.", "",
             "| file | rows / source | sha256 |", "|---|---|---|"]
    for f in sorted(p for p in out.rglob("*") if p.is_file() and p.name != "MANIFEST.md"):
        rel = f.relative_to(out).as_posix()
        what = (f"{rows[f.stem]} rows" if f.parent.name == "data"
                else next((s for d, s in {**RULES, **MODULE_DOCS}.items() if d == f.name), "")
                or next((s for s in EXPECTATIONS if s.replace("out/", "").replace("/", "__") == f.name), ""))
        lines.append(f"| `{rel}` | {what} | `{hashlib.sha256(f.read_bytes()).hexdigest()[:16]}` |")
    (out / "MANIFEST.md").write_text("\n".join(lines) + "\n")
    print(f"done: {sum(1 for _ in out.rglob('*') if _.is_file())} files in {out}")


if __name__ == "__main__":
    sys.exit(main())

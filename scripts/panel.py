"""Review panel over OpenRouter: send a document plus standing context to several models, save every reply.

    PYTHONPATH=. .venv/bin/python scripts/panel.py estimate docs/x.md            # token count and cost, no call
    PYTHONPATH=. .venv/bin/python scripts/panel.py run docs/x.md --tag S0-plan   # the panel; replies to out/panel/<tag>/

The key is read from OPENROUTER_API_KEY or ~/.config/openrouter/api_key and is never printed or
written anywhere. Models get TEXT only: the document, the standing context (CONTEXT.md's CLOSED list
and the venue facts) and a role prompt. No raw data, no credentials. Roles are split so the panel is
not four copies of one opinion. docs/discovery_programme_plan_v1.md §5.
"""
import argparse, datetime as dt, json, os, pathlib, re, sys, time
import requests

API = "https://openrouter.ai/api/v1"
MODELS = {                                                    # one per family, roles split (breadth dropped 10-13: its proposals were all on the kill list)
    "leakage":  "openai/gpt-5.6-terra-pro",
    "code":     "anthropic/claude-sonnet-5.5",
    "devil":    "x-ai/grok-4.7",
    "stats":    "google/gemini-3.8-flash",
    "breadth":  "deepseek/deepseek-v4-pro",
}
ROLES = {
    "leakage": "You are a quant reviewer whose only job is to find look-ahead bias, data leakage, survivorship bias and selection-after-the-fact in the document below. List each concrete hole with the line it comes from and how to fix it. If you find none, say so and why you are confident.",
    "code":    "You are a senior engineer reviewing the design and any code in the document for bugs that would silently change results: timestamp units, off-by-one bars, fill assumptions, fee double-counting, resampling, thread or numeric issues. Be specific and terse.",
    "devil":   "You are a sceptical PM. Argue that each proposed mechanism is noise or already known to be dead, using the kill list in the context. Then say which single item, if any, you would fund and why.",
    "stats":   "You are a statistician. Check the pass bars, controls, clustering, power and multiple-comparison handling. State what effect size the design can detect and what would fool it.",
    "breadth": "You are a crypto market microstructure researcher. Given the venue facts and the kill list, propose up to five concrete, venue-specific things to measure that are NOT on the kill list, each with the mechanism and what number would show it is real.",
}


def key():
    k = os.environ.get("OPENROUTER_API_KEY") or pathlib.Path("~/.config/openrouter/api_key").expanduser().read_text().strip()
    assert k.startswith("sk-or-"), "no OpenRouter key found"
    return k


def context():
    c = pathlib.Path("CONTEXT.md").read_text()
    closed = c[c.index("## What is CLOSED"):c.index("## Measurement rules")] if "## What is CLOSED" in c else c[:20000]
    facts = ("VENUE FACTS (verified 2026-10-13): Delta Exchange India. 231 perpetuals, 884 BTC/ETH options (daily and weekly "
             "expiries, 0.001 BTC contracts, 0.01% fees, BTC options ~$1.5B/day turnover), 6 move contracts, spot in INR for BTC/ETH/SOL/XRP. "
             "NO dated futures. Perp fees: taker 0.05%, maker 0.02%, x1.18 GST; round trip ~0.158% with 2 bps slippage; thin alts quote "
             "10-20 bps spreads. Funding settles every 8h (00/08/16 UTC); daily options settle 12:00 UTC. Owner account $250-500; owner "
             "wants selective trading, few positions at a time, capital idle most of the time. Data on disk: Delta 1m prices/mark/funding "
             "Jun 2024-Sep 2026 for 32 coins; Binance 1m volume/taker flow and 5m OI/positioning; 177 listings' first 45 days; "
             "daily ATM straddle outcomes 2024-26. Delta's option surface was measured efficient (no variance premium; IV tracks RV at 0.99).")
    return facts + "\n\nKILL LIST (what is already closed on this venue, from CONTEXT.md):\n" + closed


def call(model, system, user, max_tokens=12000):
    r = requests.post(f"{API}/chat/completions", headers={"Authorization": f"Bearer {key()}", "HTTP-Referer": "https://github.com/yesterdaysrebel/deltabt", "X-Title": "deltabt panel"},
                      json={"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": max_tokens, "temperature": 0.3},
                      timeout=600)
    r.raise_for_status(); j = r.json()
    return j["choices"][0]["message"]["content"], j.get("usage", {})


def prices():
    m = {x["id"]: x.get("pricing", {}) for x in requests.get(f"{API}/models", timeout=60).json()["data"]}
    return {role: (float(m.get(mid, {}).get("prompt", 0)), float(m.get(mid, {}).get("completion", 0))) for role, mid in MODELS.items()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("mode", choices=["estimate", "run"]); ap.add_argument("doc"); ap.add_argument("--tag", default=None)
    ap.add_argument("--roles", default=",".join(MODELS)); a = ap.parse_args()
    doc = pathlib.Path(a.doc).read_text(); ctx = context(); roles = a.roles.split(",")
    user = f"DOCUMENT UNDER REVIEW ({a.doc}):\n\n{doc}\n\n---\nSTANDING CONTEXT:\n{ctx}"
    toks = len(user) // 4 + 300
    if a.mode == "estimate":
        pr = prices(); tot = 0
        for role in roles:
            cin, cout = pr[role]; cost = toks * cin + 8000 * cout; tot += cost
            print(f"  {role:8s} {MODELS[role]:32s} ~{toks:,} in / 8,000 out -> ${cost:.3f}")
        print(f"  total ~${tot:.2f} for {len(roles)} models"); sys.exit(0)
    tag = a.tag or re.sub(r"\W+", "-", pathlib.Path(a.doc).stem); out = pathlib.Path("out/panel") / tag; out.mkdir(parents=True, exist_ok=True)
    log = {"doc": a.doc, "when": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "models": {}}
    for role in roles:
        t0 = time.time()
        try:
            text, usage = call(MODELS[role], ROLES[role], user)
        except Exception as e:
            text, usage = f"ERROR: {e!r}", {}
        (out / f"{role}_{MODELS[role].split('/')[-1]}.md").write_text(f"# {role} — {MODELS[role]}\n\n{text}\n")
        log["models"][role] = {"model": MODELS[role], "usage": usage, "seconds": round(time.time() - t0)}
        print(f"  {role:8s} {MODELS[role]:32s} {usage.get('prompt_tokens', '?')} in / {usage.get('completion_tokens', '?')} out, {round(time.time() - t0)}s", flush=True)
    (out / "log.json").write_text(json.dumps(log, indent=1))
    try:
        d = requests.get(f"{API}/auth/key", headers={"Authorization": f"Bearer {key()}"}, timeout=30).json()["data"]
        print(f"  key usage so far ${d.get('usage')}, remaining ${d.get('limit_remaining')} of ${d.get('limit')}")
    except Exception:
        pass

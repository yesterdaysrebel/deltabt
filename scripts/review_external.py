"""Run the review pack (scripts/review_pack.py) through models OUTSIDE the Claude family via OpenRouter
(owner 2026-11-11: "run external review using openrouter? I have 3$ balance").

    python3 scripts/review_external.py out/review_pack/<date> [--budget 2.5] [--models a,b,...]

The key is read from $OPENROUTER_API_KEY or ~/.openrouter_key (never printed, never written anywhere). Each model
gets the SAME single message: PROMPT.md + every file of the pack inline (it cannot open files or run code). Its
answer is saved to <pack>/reviews/<model>.md with the token usage and cost OpenRouter reports. A pre-flight
estimate at the listed prices must fit --budget, and the spend so far is checked before every call.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API = "https://openrouter.ai/api/v1"
MODELS = ["openai/gpt-6.1-sol", "google/gemini-4-argon", "x-ai/grok-4.7", "moonshotai/kimi-k3.1-pro",
          "qwen/qwen3.8-max-0902", "mistralai/mistral-large-4-0", "deepseek/deepseek-v4.1-flash"]
MAX_OUT = 12000
NOTE = ("\n\nYou cannot open files or run code: the whole pack is inline below, each file under a `===== FILE` "
        "header. Do the arithmetic by hand and show it. Answer in Markdown.\n")


def key() -> str:
    k = os.environ.get("OPENROUTER_API_KEY") or ""
    p = Path.home() / ".openrouter_key"
    if not k and p.exists():
        k = p.read_text().strip()
    if not k:
        sys.exit("no OpenRouter key: set OPENROUTER_API_KEY or write it to ~/.openrouter_key (chmod 600)")
    return k


def call(path: str, body: dict | None, k: str, timeout: int = 900) -> dict:
    req = urllib.request.Request(API + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json",
                                          "X-Title": "trading review"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def message(pack: Path, prompt: str = "PROMPT.md") -> str:
    parts = [(pack / prompt).read_text(), NOTE]
    for sub in ("data", "rules", "expectations"):
        for f in sorted((pack / sub).iterdir()):
            parts.append(f"\n===== FILE {sub}/{f.name}\n{f.read_text()}")
    return "".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pack")
    ap.add_argument("--budget", type=float, default=2.5)
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--prompt", default="PROMPT.md", help="the question file inside the pack")
    ap.add_argument("--tag", default="", help="suffix for the output files, e.g. 'improve'")
    a = ap.parse_args()
    pack = Path(a.pack)
    (pack / "reviews").mkdir(exist_ok=True)
    k = key()
    msg = message(pack, a.prompt)
    tokens_in = len(msg) / 2.8                                     # CSV digits tokenise densely: err high
    prices = {m["id"]: m.get("pricing", {}) for m in call("/models", None, k, 60)["data"]}
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    est = {}
    for m in models:
        p = prices.get(m)
        if p is None:
            sys.exit(f"{m} is not on OpenRouter's model list")
        est[m] = tokens_in * float(p.get("prompt") or 0) + MAX_OUT * float(p.get("completion") or 0)
    print(f"message {len(msg):,} chars (~{tokens_in:,.0f} tokens); worst case ${sum(est.values()):.2f} for {len(models)} "
          f"models (budget ${a.budget:.2f})")
    if sum(est.values()) > a.budget:
        sys.exit("estimate exceeds the budget: drop models or raise --budget")
    spent = 0.0
    for m in models:
        if spent + est[m] > a.budget:
            print(f"  {m}: skipped, would pass the budget (spent ${spent:.3f})")
            continue
        t0 = time.time()
        try:
            r = call("/chat/completions", {"model": m, "max_tokens": MAX_OUT, "usage": {"include": True},
                                           "messages": [{"role": "user", "content": msg}]}, k)
        except Exception as exc:                                   # noqa: BLE001 -- one model failing is not fatal
            print(f"  {m}: FAILED {type(exc).__name__}: {str(exc)[:200]}")
            continue
        text = (r.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        u = r.get("usage") or {}
        cost = float(u.get("cost") or est[m])
        spent += cost
        name = m.replace("/", "__") + (f".{a.tag}" if a.tag else "") + ".md"
        (pack / "reviews" / name).write_text(
            f"<!-- {m} via OpenRouter; {u.get('prompt_tokens')} in / {u.get('completion_tokens')} out tokens; "
            f"cost ${cost:.4f}; {time.time() - t0:.0f}s -->\n\n{text}\n")
        print(f"  {m}: {len(text):,} chars, {u.get('prompt_tokens')} in / {u.get('completion_tokens')} out, "
              f"${cost:.4f} ({time.time() - t0:.0f}s) -> reviews/{name}", flush=True)
    print(f"total spent ${spent:.3f}")


if __name__ == "__main__":
    main()

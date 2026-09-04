"""One email that compares the concurrent arms, from their own facts files.

    python3 scripts/arms_digest.py facts/*/facts.json

WHY THIS EXISTS
    Two arms now run, and the whole reason for running two is that they can be
    compared. Nothing compared them: each stack produced its own 90-line report
    and an operator held both in their head, nightly, to answer the only
    question the pair was set up to answer -- which one is working.

    It reads the small `facts` file each daily report already writes rather
    than parsing the reports, so the two cannot drift: if a number is not in
    the facts file it does not appear here, and nothing here is recomputed.

WHAT IT DELIBERATELY DOES NOT DO
    It does not rank the arms, and it does not say one is better. On any given
    night the sample is far too small for that -- both arms need months, and
    the report says so on its own line. This lists them side by side and marks
    which need a human. Drawing the conclusion is the operator's job and the
    numbers to draw it from are in `out/sweep/`, not in one night.
"""

from __future__ import annotations

import json
import pathlib
import sys


def load(paths: list[str]) -> list[dict]:
    out = []
    for path in paths:
        try:
            with open(path) as fh:
                out.append(json.load(fh))
        except (OSError, json.JSONDecodeError) as exc:
            out.append({"stack": pathlib.Path(path).parent.name,
                        "verdict": "unreadable",
                        "error": f"facts file unreadable -- {exc}"})
    return sorted(out, key=lambda f: str(f.get("stack") or ""))


def render(arms: list[dict]) -> tuple[str, int]:
    if not arms:
        return "# DeltaBt arms\n\nNo facts files were produced.\n", 1

    day = next((a.get("day") for a in arms if a.get("day")), "")
    need = [a for a in arms if a.get("verdict") != "clear"]
    head = "NEEDS ATTENTION" if need else "ALL CLEAR"

    out = [f"# DeltaBt — both arms — {day} (UTC)\n",
           f"**{head}** · {len(arms)} arm(s) running\n"]

    if need:
        for a in need:
            for p in a.get("problems") or [a.get("error", "unreadable")]:
                out.append(f"- **{a.get('stack')}**: {p}")
        out.append("")

    out.append("| arm | strategy | day | closed today | open | equity | health |")
    out.append("|---|---|---|---|---|---|---|")
    for a in arms:
        out.append(
            f"| `{a.get('stack') or '?'}` "
            f"| {a.get('strategy') or '—'} "
            f"| {a.get('day_of') or '—'} "
            f"| {a.get('closed_line') or 'none'} "
            f"| {a.get('open_line') or 'none'} "
            f"| {a.get('equity_line') or '—'} "
            f"| {a.get('health_line') or '—'} |")
    out.append("")

    # The one cross-arm number worth stating, and only when both traded.
    traded = [a for a in arms if a.get("closed_today")]
    if len(traded) > 1:
        parts = [f"{a.get('stack')} {a.get('r_today', 0):+.2f}R on "
                 f"{a.get('closed_today')} trade(s)" for a in traded]
        out.append("Today: " + "; ".join(parts) + ".")
        out.append("One day separates nothing. Both arms need months, and each "
                   "report says so on its own sample line.\n")
    elif traded:
        a = traded[0]
        out.append(f"Only `{a.get('stack')}` closed anything today "
                   f"({a.get('r_today', 0):+.2f}R on {a.get('closed_today')}).\n")
    else:
        out.append("Neither arm closed a trade today.\n")

    out.append("Each arm's full report is the artifact of its own job.")
    return "\n".join(out) + "\n", (1 if need else 0)


def main() -> int:
    text, rc = render(load(sys.argv[1:]))
    print(text)
    return rc


if __name__ == "__main__":
    sys.exit(main())

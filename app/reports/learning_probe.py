"""The daily report's "Learning" figures, read from the bot's database.

SELF-CONTAINED ON PURPOSE: standard library only, and a connection passed in.
deploy/aws/db_probe.py is base64-embedded into the monitor SSM document and
may import nothing but the standard library and asyncpg, so this function is
meant to be copied into it verbatim and called as

    out["learning"] = await collect_learning(con)

scripts/brief_report.py renders out["learning"] (learning_lines). Read-only.
"""
import datetime as _dt
import json as _json
import os as _os


async def collect_learning(con, now=None):
    """Signals of the previous UTC day by what happened to them (and how many
    were takeable), labels so far, and the latest weekly grade -- FOR THE RULE
    THIS BOT RUNS (2026-10-20: the rule changed on 10-19 and the line pooled
    both rules' labels and showed the old rule's grade). The family comes from
    DELTABOT_VARIANT ("SPEC:<family>@..."); without one, nothing is filtered.
    Never raises: a missing table or column (a database from before
    2026-10-13) or any error is reported in "error"."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - _dt.timedelta(days=1)
    out = {"day": start.strftime("%Y-%m-%d")}
    variant = (_os.environ.get("DELTABOT_VARIANT") or "").strip()
    family = variant[5:].split("@")[0].strip() if variant.upper().startswith("SPEC:") else None
    like = f"{family}@%" if family else "%"
    out["family"] = family
    try:
        # RECORD-ONLY coins (2026-10-16) are watched, never traded: their skip
        # is "record_only", or -- without readings -- their refusal says so.
        rows = await con.fetch(
            "SELECT COALESCE(features->>'skip', "
            "  CASE WHEN outcome = 'APPROVED' THEN 'taken' "
            "  WHEN rejection_reason LIKE 'record_only%' THEN 'record_only' "
            "  ELSE 'other' END) AS k, "
            "  count(*) AS n, count(*) FILTER (WHERE features IS NULL) AS no_readings, "
            "  count(*) FILTER (WHERE jsonb_array_length("
            "    COALESCE(features->'errors', '[]'::jsonb)) > 0) AS partial "
            "FROM strategy_signals WHERE direction IS NOT NULL "
            "AND bar_open >= $1 AND bar_open < $2 AND strategy_version LIKE $3 GROUP BY 1",
            start, today, like)
        out["signals"] = {r["k"]: int(r["n"]) for r in rows}
        out["no_readings"] = sum(int(r["no_readings"]) for r in rows)
        out["partial_readings"] = sum(int(r["partial"]) for r in rows)
        out["record_only"] = out["signals"].get("record_only", 0)
        # TAKEABLE: what the signal lab would have had as a row (no earlier
        # takeable signal on the coin still open); NULL until decided.
        tk = await con.fetchrow(
            "SELECT count(*) FILTER (WHERE o.takeable) AS takeable, "
            "count(*) FILTER (WHERE o.takeable IS NULL) AS undecided, "
            "count(*) FILTER (WHERE o.takeable AND (s.features->>'record_only' = 'true' "
            "  OR s.rejection_reason LIKE 'record_only%')) AS takeable_record_only "
            "FROM strategy_signals s LEFT JOIN signal_outcomes o ON o.signal_id = s.id "
            "WHERE s.direction IS NOT NULL AND s.bar_open >= $1 AND s.bar_open < $2 "
            "AND s.strategy_version LIKE $3",
            start, today, like)
        out["takeable"] = int(tk["takeable"])
        out["takeable_undecided"] = int(tk["undecided"])
        out["takeable_record_only"] = int(tk["takeable_record_only"])
        lab = await con.fetchrow(
            "SELECT count(*) FILTER (WHERE o.r_net IS NOT NULL) AS n, "
            "count(*) FILTER (WHERE o.r_net IS NULL) AS unlabellable, "
            "avg(o.r_net) AS mean_r, "
            "count(*) FILTER (WHERE o.takeable AND o.r_net IS NOT NULL) AS n_takeable, "
            "avg(o.r_net) FILTER (WHERE o.takeable) AS takeable_mean_r "
            "FROM signal_outcomes o JOIN strategy_signals s ON s.id = o.signal_id "
            "WHERE s.strategy_version LIKE $1", like)
        out["labelled"] = int(lab["n"])
        out["unlabellable"] = int(lab["unlabellable"])
        out["mean_r"] = None if lab["mean_r"] is None else round(float(lab["mean_r"]), 3)
        out["labelled_takeable"] = int(lab["n_takeable"])
        out["takeable_mean_r"] = (None if lab["takeable_mean_r"] is None
                                  else round(float(lab["takeable_mean_r"]), 3))
        out["waiting"] = int(await con.fetchval(
            "SELECT count(*) FROM strategy_signals s LEFT JOIN signal_outcomes o "
            "ON o.signal_id = s.id WHERE o.id IS NULL AND s.direction IS NOT NULL "
            "AND s.entry_price IS NOT NULL AND s.strategy_version LIKE $1", like))
        g = await con.fetchrow(
            "SELECT week_start, n_test, auc, top_mean_r, all_mean_r, random_p, top_k "
            "FROM model_grades WHERE $1::text IS NULL OR detail->>'family' = $1 "
            "ORDER BY week_start DESC LIMIT 1", family)
        if g is not None:
            out["grade"] = {k: (g[k].strftime("%Y-%m-%d") if k == "week_start" else
                                None if g[k] is None else float(g[k]))
                            for k in ("week_start", "n_test", "auc", "top_mean_r",
                                      "all_mean_r", "random_p", "top_k")}
    except Exception as exc:                    # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return _json.loads(_json.dumps(out, default=str))

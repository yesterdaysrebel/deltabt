"""deploy/aws/db_probe.py journal mode: the output must fit SSM's cap.

SSM keeps 24,000 bytes of a command's output. In journal mode the probe drops
the OLDEST closed trades (never an open one) until its gzip+base64 output is
inside its budget, keeps each trade's exit rows with it, and says how many it
dropped. Without this a long run would cut the database section off and the
report would lose every figure at once.
"""
from __future__ import annotations

import importlib.util
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("db_probe", ROOT / "deploy/aws/db_probe.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def a_run(n_closed, n_open=1):
    rnd = random.Random(7)
    journal = [[i, "BEATUSD", 1, "CLOSED", rnd.randint(1, 400), rnd.random(), rnd.random(),
                rnd.random(), 250.0, 1790942233 + i, 1790949433 + i, rnd.random(),
                "STOP_LOSS", rnd.uniform(-1, 3), rnd.uniform(-5, 15), 0.3, False, 12.0]
               for i in range(n_closed)]
    journal += [[f"pos_open_{j}", "BANKUSD", 1, "OPEN", 125, 0.03, 0.029, 0.033, 380.0,
                 1790999999, None, None, None, None, None, 0.0, False, None]
                for j in range(n_open)]
    exits = [[i, rule, 1790949433 + i, rnd.random(), "STOP_LOSS", rnd.uniform(-1, 3), True]
             for i in range(n_closed) for rule in ("ladder", "trail")]
    return {"journal": journal, "exits": exits}


def test_a_small_run_is_untouched():
    out = a_run(10)
    probe._fit_journal(out)
    assert out["journal_omitted"] == 0 and len(out["journal"]) == 11


def test_a_long_run_is_trimmed_to_the_budget_oldest_first():
    out = a_run(400)
    probe._fit_journal(out, budget=12_000)
    assert probe._encoded_size(out) <= 12_000
    kept = [row for row in out["journal"] if row[3] == "CLOSED"]
    assert out["journal_omitted"] == 400 - len(kept) > 0
    assert kept[0][0] == out["journal_omitted"], "the oldest went first"
    assert out["journal"][-1][3] == "OPEN", "an open trade is never dropped"


def test_exit_rows_stay_with_their_trade():
    out = a_run(300)
    probe._fit_journal(out, budget=12_000)
    for e in out["exits"]:
        row = out["journal"][e[0]]
        assert row[3] == "CLOSED" and row[0] == e[2] - 1790949433, \
            "an exit row must still point at its own trade after the shift"

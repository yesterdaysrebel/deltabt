"""cmd_fetch exit codes. A fetch that cached nothing must not report success."""

from __future__ import annotations

import pandas as pd

from deltabt import cli

ARGS = ["fetch", "--symbols", "BTCUSD", "--days", "1", "--offline"]


def series(n: int) -> dict[str, pd.DataFrame]:
    f = pd.DataFrame({"time": [1735689600 + 60 * i for i in range(n)]})
    return {"ltp": f, "mark": f, "funding": f.head(max(n // 60, 0))}


def test_populated_fetch_reports_bars_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(cli.CandleStore, "load_all_series",
                        lambda self, *a, **k: series(5))
    assert cli.main(ARGS) == 0
    assert "5 1m bars" in capsys.readouterr().out


def test_empty_window_exits_nonzero(monkeypatch, capsys):
    """An inverted or pre-listing window returns frames, not an exception."""
    monkeypatch.setattr(cli.CandleStore, "load_all_series",
                        lambda self, *a, **k: series(0))
    assert cli.main(ARGS) == 1
    out = capsys.readouterr().out
    assert "SKIPPED (no candles in the requested window)" in out
    assert "cached under" not in out


def test_symbol_that_raises_is_skipped(monkeypatch, capsys):
    def boom(self, *a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(cli.CandleStore, "load_all_series", boom)
    assert cli.main(ARGS) == 1
    assert "SKIPPED (api down)" in capsys.readouterr().out


def test_one_good_symbol_among_failures_exits_zero(monkeypatch):
    def mixed(self, symbol, *a, **k):
        if symbol == "BTCUSD":
            raise RuntimeError("api down")
        return series(0) if symbol == "ETHUSD" else series(3)

    monkeypatch.setattr(cli.CandleStore, "load_all_series", mixed)
    assert cli.main(["fetch", "--symbols", "BTCUSD", "ETHUSD", "SOLUSD",
                     "--days", "1", "--offline"]) == 0


def test_offline_does_not_refresh_the_product_catalog(monkeypatch):
    called = []
    monkeypatch.setattr(cli.CandleStore, "load_all_series",
                        lambda self, *a, **k: series(5))
    monkeypatch.setattr(cli.ProductCatalog, "all",
                        lambda self, **k: called.append(1) or {})
    assert cli.main(ARGS) == 0
    assert called == []

"""Top-gainers universe (live/universe.py, owner 2026-10-13)."""

import asyncio

import pytest

from live import universe


def row(sym, change, turnover=1_000_000, tag="crypto", status="operational",
        ctype="perpetual_futures"):
    return {"symbol": sym, "ltp_change_24h": str(change), "turnover_usd": turnover,
            "top_tag": tag, "product_trading_status": status, "contract_type": ctype}


def test_sorted_by_24h_change_and_cut_at_n():
    rows = [row("A", 5), row("B", 40), row("C", 12), row("D", 1)]
    assert universe.top_gainers(rows, n=3, min_turnover_usd=0) == ["B", "C", "A"]


def test_excludes_non_crypto_thin_halted_losers_and_non_perps():
    rows = [
        row("STOCK", 50, tag="tradfi"),
        row("THIN", 40, turnover=10_000),
        row("HALTED", 30, status="disrupted_cancel_only"),
        row("FALLING", -5),
        row("FLAT", 0),
        row("OPTION", 25, ctype="call_options"),
        row("GOOD", 3),
    ]
    assert universe.top_gainers(rows, n=10, min_turnover_usd=250_000) == ["GOOD"]


def test_missing_or_garbage_fields_do_not_qualify():
    bad = {"symbol": "X", "ltp_change_24h": None, "turnover_usd": "n/a",
           "top_tag": "crypto", "product_trading_status": "operational",
           "contract_type": "perpetual_futures"}
    assert universe.top_gainers([bad, row("Y", 2)], n=5, min_turnover_usd=0) == ["Y"]


def test_held_coins_are_kept_even_off_the_list():
    rows = [row("A", 9), row("B", 8)]
    got = universe.pick(rows, held=["OLD", "A"], env={"DELTABOT_TOP_N": "2"})
    assert got == ("A", "B", "OLD")


def test_nothing_qualifies_and_nothing_held_refuses():
    with pytest.raises(ValueError, match="no coin qualifies"):
        universe.pick([row("A", -1)], held=[], env={})


def test_mode_defaults_to_fixed_and_rejects_typos():
    assert universe.mode({}) == "fixed"
    assert universe.mode({"DELTABOT_UNIVERSE": "top_gainers"}) == "top_gainers"
    with pytest.raises(ValueError):
        universe.mode({"DELTABOT_UNIVERSE": "top-gainers"})


def test_settings_from_env():
    assert universe.settings_from_env({}) == (10, 250_000.0)
    assert universe.settings_from_env(
        {"DELTABOT_TOP_N": "5", "DELTABOT_MIN_TURNOVER_USD": "500000"}) == (5, 500_000.0)
    with pytest.raises(ValueError):
        universe.settings_from_env({"DELTABOT_TOP_N": "0"})


def test_fixed_mode_entrypoint_uses_venue_list(monkeypatch):
    import live.__main__ as entry
    monkeypatch.delenv("DELTABOT_UNIVERSE", raising=False)
    got = asyncio.run(entry._universe(client=None, venue="prod", database_url="unused"))
    assert got == (("BEATUSD", "AKEUSD", "BANKUSD"), (), {})   # nothing record-only


def test_top_gainers_entrypoint_merges_held_from_database(monkeypatch):
    import live.__main__ as entry

    class Pos:
        def __init__(self, s):
            self.symbol = s

    class FakeRepo:
        def __init__(self, *a, **k):
            pass

        async def connect(self):
            pass

        async def load_open_positions(self):
            return [Pos("HELDUSD")]

        async def close(self):
            pass

    class FakeClient:
        def _read(self, path, params=None):
            assert path == "/v2/tickers"
            return [row("GTCUSD", 46), row("STXUSD", 11)]

    monkeypatch.setenv("DELTABOT_UNIVERSE", "top_gainers")
    monkeypatch.setattr(entry, "PostgresRepository", FakeRepo)
    got, record_only, info = asyncio.run(entry._universe(FakeClient(), "prod", "postgresql://x"))
    assert got == ("GTCUSD", "STXUSD", "HELDUSD")
    assert record_only == ()       # no 11th gainer; the default watchlist is not listed here
    # what the pick saw goes to the signal recorder; a held coin has none
    assert info["GTCUSD"]["rank"] == 1 and info["GTCUSD"]["change"] == 46
    assert info["STXUSD"]["rank"] == 2 and "HELDUSD" not in info


def test_cli_refuses_to_register_an_experiment_in_top_gainers_mode(monkeypatch):
    import live.__main__ as entry
    from live.config import ConfigError
    monkeypatch.setenv("DELTA_ENV", "prod")
    monkeypatch.setenv("DELTABOT_UNIVERSE", "top_gainers")
    with pytest.raises(ConfigError, match="re-picks its coins daily"):
        entry.cli_entry(["forward-test", "start"])


def test_daily_repick_is_30s_past_the_next_utc_midnight():
    import live.__main__ as entry
    # 2026-10-13 18:00:00 UTC -> 2026-10-14 00:00:30 UTC
    assert entry._next_repick(1791914400) == 1791936030
    # exactly midnight -> the NEXT midnight, never "now"
    assert entry._next_repick(1791936000) == 1792022430


def test_max_leverage_is_configurable_and_bounded(monkeypatch):
    from app.config.settings import Settings
    monkeypatch.delenv("DELTABOT_MAX_LEVERAGE", raising=False)
    assert Settings.from_env().risk.max_leverage == 3.0
    monkeypatch.setenv("DELTABOT_MAX_LEVERAGE", "5")
    assert Settings.from_env().risk.max_leverage == 5.0
    monkeypatch.setenv("DELTABOT_MAX_LEVERAGE", "25")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_ranked_gainers_carry_rank_change_and_turnover():
    rows = [row("A", 5, 3e5), row("B", 40, 9e5), row("C", 12, 5e5)]
    got = universe.ranked_gainers(rows, n=2, min_turnover_usd=0)
    assert [(r["symbol"], r["rank"], r["change"], r["turnover"]) for r in got] == [
        ("B", 1, 40.0, 9e5), ("C", 2, 12.0, 5e5)]
    assert universe.pick(rows, [], env={"DELTABOT_TOP_N": "2"}) == ("B", "C")


def test_the_signal_recorder_gets_plain_pick_data(monkeypatch):
    import live.__main__ as entry
    from app.config.variants import resolve_strategy
    from deltabt.costs import SymbolCosts

    class Cat:
        def get(self, s):
            return {"launch_time": "2026-01-02T00:00:00Z"}

    monkeypatch.setattr(entry, "ProductCatalog", Cat)
    monkeypatch.delenv("DELTABOT_SIGNAL_FEATURES", raising=False)
    costs = {"GTCUSD": SymbolCosts(symbol="GTCUSD", tick_size=0.001, contract_value=10.0,
                                   maker_fee=0.0002, taker_fee=0.0005, max_leverage=25,
                                   position_size_limit=1e6, funding_interval_seconds=28800)}
    picks = {"GTCUSD": {"rank": 1, "change": 46.0, "turnover": 2e6, "tags": ["meme"]}}
    strategy = resolve_strategy({"DELTABOT_VARIANT": "SPEC:manual_scalp_both_t3_st@5"})
    rec = entry.signal_recorder(strategy, ("GTCUSD",), picks, costs)
    assert rec.picks == {"GTCUSD": {"rank": 1, "change": 46.0, "turnover": 2e6}}
    assert rec.tags == {"GTCUSD": "meme"} and rec.contract_values == {"GTCUSD": 10.0}
    assert rec.launches["GTCUSD"] == 1767312000 and rec.pick_day % 86400 == 0
    monkeypatch.setenv("DELTABOT_SIGNAL_FEATURES", "0")
    assert entry.signal_recorder(strategy, ("GTCUSD",), picks, costs) is None

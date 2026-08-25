"""The quote recorder must not lose data, because lost snapshots are unrecoverable.

Delta publishes no quote history, so anything this recorder drops is gone
permanently. These tests are about durability and schema stability rather than
about any research result.
"""

from __future__ import annotations

import pandas as pd
import pytest

from deltabt.data import quote_recorder as qr


def make_ticker(symbol="C-BTC-96000-301026", bid="1541", ask="1575.5", **over):
    t = {
        "symbol": symbol,
        "underlying_asset_symbol": "BTC",
        "mark_price": "1579.11893582",
        "spot_price": "79563.8",
        "timestamp": 1787584216602131,
        "oi_contracts": "6016",
        "turnover_usd": 183229.2177,
        "volume": 2.348,
        "greeks": {"delta": "0.197", "gamma": "0.0000176", "vega": "94.6",
                   "theta": "-32.69", "spot": "79567.1"},
        "quotes": {"best_bid": bid, "best_ask": ask, "bid_size": "6171",
                   "ask_size": "12", "mark_iv": "0.462", "bid_iv": "0.4579",
                   "ask_iv": "0.4616"},
    }
    t.update(over)
    return t


class FakeClient:
    """Serves a fixed surface; can be told to fail on a given contract type."""

    def __init__(self, calls=None, fail_on=None, empty_on=None):
        self.calls = calls if calls is not None else []
        self.fail_on = fail_on
        self.empty_on = empty_on

    def tickers(self, contract_types="perpetual_futures"):
        self.calls.append(contract_types)
        if contract_types == self.fail_on:
            raise RuntimeError("boom")
        if contract_types == self.empty_on:
            return []
        right = "C" if contract_types == "call_options" else "P"
        return [make_ticker(symbol=f"{right}-BTC-{k}-301026") for k in (90000, 96000)]


class TestSnapshot:
    def test_pulls_both_rights(self):
        c = FakeClient()
        df = qr.snapshot(c, now=1_000)
        assert c.calls == ["call_options", "put_options"]
        assert len(df) == 4
        assert set(df["symbol"].str[0]) == {"C", "P"}

    def test_schema_is_fixed_not_payload_derived(self):
        """A field Delta adds or removes must not change the stored columns."""
        df = qr.snapshot(FakeClient(), now=1_000)
        assert list(df.columns) == list(qr._COLUMNS)

    def test_extra_payload_fields_are_ignored(self):
        class Extra(FakeClient):
            def tickers(self, contract_types="perpetual_futures"):
                out = super().tickers(contract_types)
                for t in out:
                    t["some_new_field_delta_added"] = 1
                return out

        df = qr.snapshot(Extra(), now=1_000)
        assert list(df.columns) == list(qr._COLUMNS)

    def test_exchange_timestamp_normalised_to_seconds(self):
        df = qr.snapshot(FakeClient(), now=1_000)
        assert int(df["exchange_ts"].iloc[0]) == 1787584216

    def test_snapshot_ts_is_the_poll_instant(self):
        df = qr.snapshot(FakeClient(), now=4_242)
        assert set(df["snapshot_ts"]) == {4_242}

    def test_partial_failure_yields_nothing(self):
        """Better no snapshot than one silently missing every put."""
        with pytest.raises(RuntimeError):
            qr.snapshot(FakeClient(fail_on="put_options"), now=1_000)

    def test_empty_response_is_an_error_not_an_empty_snapshot(self):
        with pytest.raises(RuntimeError, match="empty ticker response"):
            qr.snapshot(FakeClient(empty_on="put_options"), now=1_000)

    def test_missing_quotes_become_nan_not_zero(self):
        """A one-sided book is not a zero bid; zero would poison the spread stats."""
        class NoQuotes(FakeClient):
            def tickers(self, contract_types="perpetual_futures"):
                out = super().tickers(contract_types)
                for t in out:
                    t["quotes"] = {}
                return out

        df = qr.snapshot(NoQuotes(), now=1_000)
        assert df["best_bid"].isna().all()
        assert df["mark_iv"].isna().all()


class TestAppend:
    def test_writes_a_utc_day_partition(self, tmp_path):
        df = qr.snapshot(FakeClient(), now=1_787_584_216)
        path = qr.append(df, quote_dir=tmp_path)
        assert path.name == "quotes_2026-08-24.parquet"
        assert len(pd.read_parquet(path)) == 4

    def test_second_snapshot_appends_rather_than_replaces(self, tmp_path):
        qr.append(qr.snapshot(FakeClient(), now=1_787_584_216), quote_dir=tmp_path)
        path = qr.append(qr.snapshot(FakeClient(), now=1_787_584_816), quote_dir=tmp_path)
        assert len(pd.read_parquet(path)) == 8

    def test_same_snapshot_twice_is_deduplicated(self, tmp_path):
        """A retry must not double-count contracts into the spread statistics."""
        df = qr.snapshot(FakeClient(), now=1_787_584_216)
        qr.append(df, quote_dir=tmp_path)
        path = qr.append(df, quote_dir=tmp_path)
        assert len(pd.read_parquet(path)) == 4

    def test_refuses_an_empty_frame(self, tmp_path):
        with pytest.raises(ValueError):
            qr.append(pd.DataFrame(), quote_dir=tmp_path)

    def test_leaves_no_temp_file_behind(self, tmp_path):
        qr.append(qr.snapshot(FakeClient(), now=1_787_584_216), quote_dir=tmp_path)
        assert list(tmp_path.glob("*.tmp")) == []

    def test_separate_days_are_separate_partitions(self, tmp_path):
        qr.append(qr.snapshot(FakeClient(), now=1_787_584_216), quote_dir=tmp_path)
        qr.append(qr.snapshot(FakeClient(), now=1_787_584_216 + 86_400), quote_dir=tmp_path)
        assert len(list(tmp_path.glob("quotes_*.parquet"))) == 2


class TestResilience:
    def test_run_survives_a_failing_poll(self, tmp_path, monkeypatch):
        """An HTTP error at 03:00 must not kill the recorder until morning."""
        calls = {"n": 0}

        def flaky(client=None, *, quote_dir=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return 4

        monkeypatch.setattr(qr, "record_once", flaky)
        monkeypatch.setattr(qr, "DeltaClient", lambda *a, **k: None)
        written = qr.run(interval=0, quote_dir=tmp_path, max_snapshots=2)
        assert calls["n"] == 3, "the failed poll was retried on the next tick"
        assert written == 2


class TestSpreadSummary:
    def test_median_half_spread_matches_definition(self):
        df = pd.DataFrame({"best_bid": [90.0], "best_ask": [110.0]})
        assert qr._median_half_spread(df) == pytest.approx(0.1)

    def test_ignores_one_sided_and_crossed_books(self):
        df = pd.DataFrame({"best_bid": [90.0, 0.0, 120.0], "best_ask": [110.0, 50.0, 100.0]})
        assert qr._median_half_spread(df) == pytest.approx(0.1)

    def test_no_two_sided_quotes_is_nan(self):
        df = pd.DataFrame({"best_bid": [0.0], "best_ask": [0.0]})
        assert qr._median_half_spread(df) != qr._median_half_spread(df)  # NaN

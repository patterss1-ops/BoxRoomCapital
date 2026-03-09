from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from research.engine_a.runtime_data import EngineARuntimeDataProvider


def test_engine_a_runtime_provider_builds_pipeline_payload(monkeypatch):
    monkeypatch.setattr(
        EngineARuntimeDataProvider,
        "_root_symbols",
        lambda self: ["ES"],
    )
    monkeypatch.setattr(
        "research.engine_a.runtime_data.get_front_contract",
        lambda root_symbol, as_of: SimpleNamespace(instrument_id=1, expiry_date=date(2026, 6, 15)),
    )
    monkeypatch.setattr(
        "research.engine_a.runtime_data.build_multiple_prices",
        lambda root_symbol, as_of: SimpleNamespace(current_price=5200.0, next_price=5180.0),
    )
    monkeypatch.setattr(
        "research.engine_a.runtime_data.build_continuous_series",
        lambda root_symbol: [
            SimpleNamespace(bar_date=date(2025, 1, 1) + timedelta(days=idx), price=100.0 + idx)
            for idx in range(260)
        ],
    )
    monkeypatch.setattr(
        "research.engine_a.runtime_data.get_carry_series",
        lambda root_symbol, start, end: [{"carry": -5.0}, {"carry": -3.0}, {"carry": -1.0}],
    )
    monkeypatch.setattr(
        "research.engine_a.runtime_data.get_instrument",
        lambda instrument_id: SimpleNamespace(multiplier=50.0),
    )

    provider = EngineARuntimeDataProvider(capital_base=250000.0)

    payload = provider("2026-03-09T00:00:00Z")

    assert payload["capital"] == 250000.0
    assert payload["price_history"]["ES"][-1] == 359.0
    assert payload["term_structure"]["ES"]["front_price"] == 5200.0
    assert payload["term_structure"]["ES"]["deferred_price"] == 5180.0
    assert payload["contract_sizes"]["ES"] == 260000.0
    assert payload["current_positions"]["ES"] == 0.0
    assert set(payload["regime_inputs"]) == {"vix", "vix_percentile", "index_data", "yield_data", "macro_data"}


def test_engine_a_runtime_provider_raises_when_history_is_missing(monkeypatch):
    monkeypatch.setattr(EngineARuntimeDataProvider, "_root_symbols", lambda self: ["ES"])
    monkeypatch.setattr(
        "research.engine_a.runtime_data.get_front_contract",
        lambda root_symbol, as_of: SimpleNamespace(instrument_id=1, expiry_date=date(2026, 6, 15)),
    )
    monkeypatch.setattr(
        "research.engine_a.runtime_data.build_multiple_prices",
        lambda root_symbol, as_of: SimpleNamespace(current_price=5200.0, next_price=5180.0),
    )
    monkeypatch.setattr(
        "research.engine_a.runtime_data.build_continuous_series",
        lambda root_symbol: [SimpleNamespace(bar_date=date(2026, 1, 1), price=100.0)],
    )
    monkeypatch.setattr(
        "research.engine_a.runtime_data.get_carry_series",
        lambda root_symbol, start, end: [],
    )
    monkeypatch.setattr(
        "research.engine_a.runtime_data.get_instrument",
        lambda instrument_id: SimpleNamespace(multiplier=50.0),
    )

    provider = EngineARuntimeDataProvider()

    with pytest.raises(ValueError, match="Insufficient canonical futures history"):
        provider("2026-03-09T00:00:00Z")


def test_engine_a_runtime_provider_limits_carry_history_lookback(monkeypatch):
    seen = {}
    monkeypatch.setattr(EngineARuntimeDataProvider, "_root_symbols", lambda self: ["ES"])
    monkeypatch.setattr(
        "research.engine_a.runtime_data.get_front_contract",
        lambda root_symbol, as_of: SimpleNamespace(instrument_id=1, expiry_date=date(2026, 6, 15)),
    )
    monkeypatch.setattr(
        "research.engine_a.runtime_data.build_multiple_prices",
        lambda root_symbol, as_of: SimpleNamespace(current_price=5200.0, next_price=5180.0),
    )
    monkeypatch.setattr(
        "research.engine_a.runtime_data.build_continuous_series",
        lambda root_symbol: [
            SimpleNamespace(bar_date=date(2025, 1, 1) + timedelta(days=idx), price=100.0 + idx)
            for idx in range(260)
        ],
    )

    def _carry_series(root_symbol, start, end):
        seen["start"] = start
        seen["end"] = end
        return []

    monkeypatch.setattr("research.engine_a.runtime_data.get_carry_series", _carry_series)
    monkeypatch.setattr(
        "research.engine_a.runtime_data.get_instrument",
        lambda instrument_id: SimpleNamespace(multiplier=50.0),
    )

    provider = EngineARuntimeDataProvider(carry_lookback_days=30)

    provider("2026-03-09T00:00:00Z")

    assert seen["start"] == date(2026, 2, 7)
    assert seen["end"] == date(2026, 3, 9)

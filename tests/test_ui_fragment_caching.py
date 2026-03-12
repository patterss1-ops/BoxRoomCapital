from __future__ import annotations

from unittest.mock import patch

import config
from app.api import server
from app.api import shared as _shared_mod
from broker.base import AccountInfo


class _BrokerStub:
    is_demo = True

    def __init__(self) -> None:
        self.account_calls: list[float | None] = []
        self.position_calls: list[float | None] = []
        self.market_calls: list[tuple[str, float | None]] = []

    def is_connected(self) -> bool:
        return True

    def get_account_info(self, timeout: float | None = None) -> AccountInfo:
        self.account_calls.append(timeout)
        return AccountInfo(
            balance=10_000.0,
            equity=10_250.0,
            unrealised_pnl=250.0,
            open_positions=1,
            currency="GBP",
        )

    def get_positions(self, timeout: float | None = None):
        self.position_calls.append(timeout)
        return []

    def get_market_info(self, epic: str, timeout: float | None = None):
        self.market_calls.append((epic, timeout))
        return {
            "snapshot": {
                "marketStatus": "TRADEABLE",
                "bid": 100.0,
                "offer": 101.0,
            }
        }


def _clear_fragment_caches() -> None:
    server._FRAGMENT_CACHE.clear()
    server._FRAGMENT_CACHE_REFRESH_LOCKS.clear()


def test_broker_snapshot_cache_shared_across_fragments():
    stub = _BrokerStub()
    _clear_fragment_caches()
    with patch.object(_shared_mod, "_broker", stub):
        first = server._get_broker_snapshot()
        second = server._get_broker_snapshot()

    assert first["connected"] is True
    assert second["connected"] is True

    assert stub.account_calls == [server._UI_BROKER_TIMEOUT_SECONDS]
    assert stub.position_calls == [server._UI_BROKER_TIMEOUT_SECONDS]


def test_market_browser_cache_avoids_repeat_market_fetches():
    stub = _BrokerStub()
    _clear_fragment_caches()
    with patch.object(_shared_mod, "_broker", stub):
        first = server._get_market_browser_context()
        second = server._get_market_browser_context()

    assert first["connected"] is True
    assert second["connected"] is True

    assert len(stub.market_calls) == len(config.MARKET_MAP)
    assert all(timeout == server._UI_BROKER_MARKET_TIMEOUT_SECONDS for _, timeout in stub.market_calls)


def test_risk_briefing_cache_reuses_payload():
    _clear_fragment_caches()
    payload = {"ok": True, "status": "GREEN"}
    with patch.object(server, "build_risk_briefing_payload", return_value=payload) as builder:
        first = server._get_risk_briefing_context()
        second = server._get_risk_briefing_context()

    assert first == payload
    assert second == payload
    builder.assert_called_once_with()


def test_portfolio_analytics_cache_reuses_payload_by_day_bucket():
    _clear_fragment_caches()
    seen_days: list[int] = []

    def _builder(*, days: int):
        seen_days.append(days)
        return {"ok": True, "days": days}

    with patch.object(server, "build_portfolio_analytics_payload", side_effect=_builder):
        first = server._get_portfolio_analytics_context(days=30)
        second = server._get_portfolio_analytics_context(days=30)
        third = server._get_portfolio_analytics_context(days=60)

    assert first == {"ok": True, "days": 30}
    assert second == {"ok": True, "days": 30}
    assert third == {"ok": True, "days": 60}
    assert seen_days == [30, 60]

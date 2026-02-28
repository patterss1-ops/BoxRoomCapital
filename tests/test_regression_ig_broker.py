"""
Regression tests for IG Broker adapter — preservation of existing IG paths.

A-008: Verifies connection, account info, positions, market info,
spread betting orders, options trading, and failure injection paths
all work correctly with mocked HTTP responses.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch, PropertyMock
from datetime import datetime

from broker.ig import IGBroker
from broker.base import (
    BrokerCapabilities,
    OrderResult,
    Position,
    AccountInfo,
    OptionMarket,
    SpreadOrderResult,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def broker():
    """An IGBroker with a mocked session (simulates already connected)."""
    b = IGBroker(is_demo=True)
    b.session = MagicMock()
    b.session.headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json; charset=UTF-8",
        "X-IG-API-KEY": "test-key",
        "CST": "test-cst",
        "X-SECURITY-TOKEN": "test-token",
    }
    return b


def _mock_response(status_code=200, json_data=None, headers=None, text=""):
    """Create a mock HTTP response."""
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.headers = headers or {}
    resp.text = text
    return resp


# ─── Capabilities ────────────────────────────────────────────────────────


class TestIGCapabilities:
    """Verify the IG capability matrix is unchanged."""

    def test_ig_supports_spreadbet(self):
        assert IGBroker.capabilities.supports_spreadbet is True

    def test_ig_supports_cfd(self):
        assert IGBroker.capabilities.supports_cfd is True

    def test_ig_supports_options(self):
        assert IGBroker.capabilities.supports_options is True

    def test_ig_supports_short(self):
        assert IGBroker.capabilities.supports_short is True

    def test_ig_supports_live(self):
        assert IGBroker.capabilities.supports_live is True

    def test_ig_does_not_support_spot_etf(self):
        assert IGBroker.capabilities.supports_spot_etf is False

    def test_ig_does_not_support_paper(self):
        assert IGBroker.capabilities.supports_paper is False


# ─── Connection ──────────────────────────────────────────────────────────


class TestIGConnection:
    @patch("broker.ig.requests.Session")
    @patch("broker.ig.config")
    def test_connect_success(self, mock_config, mock_session_cls):
        mock_config.IG_API_KEY = "test"
        mock_config.IG_USERNAME = "user"
        mock_config.IG_PASSWORD = "pass"
        mock_config.IG_ACC_NUMBER = ""

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.headers = {}

        auth_resp = _mock_response(
            200,
            json_data={"currentAccountId": "ACC1"},
            headers={"CST": "abc", "X-SECURITY-TOKEN": "xyz"},
        )
        mock_session.post.return_value = auth_resp

        b = IGBroker(is_demo=True)
        result = b.connect()
        assert result is True
        assert b.session is not None

    @patch("broker.ig.requests.Session")
    @patch("broker.ig.config")
    def test_connect_failure(self, mock_config, mock_session_cls):
        mock_config.IG_API_KEY = "test"
        mock_config.IG_USERNAME = "user"
        mock_config.IG_PASSWORD = "wrong"
        mock_config.IG_ACC_NUMBER = ""

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.headers = {}

        auth_resp = _mock_response(401, text="Unauthorized")
        mock_session.post.return_value = auth_resp

        b = IGBroker(is_demo=True)
        result = b.connect()
        assert result is False

    def test_disconnect_clears_session(self, broker):
        assert broker.session is not None
        broker.disconnect()
        assert broker.session is None

    def test_not_connected_returns_empty(self):
        b = IGBroker(is_demo=True)
        assert b.get_positions() == []
        assert b.get_account_info().balance == 0


# ─── Account Info ────────────────────────────────────────────────────────


class TestIGAccountInfo:
    @patch("broker.ig.config")
    def test_account_info_success(self, mock_config, broker):
        mock_config.IG_ACC_NUMBER = "ACC1"
        broker.session.get.return_value = _mock_response(200, {
            "accounts": [{
                "accountId": "ACC1",
                "accountType": "SPREADBET",
                "balance": {"balance": 5000, "profitLoss": 150},
                "currency": "GBP",
            }]
        })
        info = broker.get_account_info()
        assert info.balance == 5000
        assert info.equity == 5150  # balance + profitLoss
        assert info.unrealised_pnl == 150
        assert info.currency == "GBP"

    @patch("broker.ig.config")
    def test_account_info_api_failure(self, mock_config, broker):
        mock_config.IG_ACC_NUMBER = "ACC1"
        broker.session.get.return_value = _mock_response(500)
        info = broker.get_account_info()
        assert info.balance == 0
        assert info.equity == 0


# ─── Positions ───────────────────────────────────────────────────────────


class TestIGPositions:
    def test_get_positions_success(self, broker):
        broker._deal_map["DEAL1"] = ("FTSE100", "ibs_credit_spreads")
        broker.session.get.return_value = _mock_response(200, {
            "positions": [{
                "market": {"epic": "IX.D.FTSE.DAILY.IP"},
                "position": {
                    "dealId": "DEAL1",
                    "direction": "BUY",
                    "size": 2.0,
                    "openLevel": 7500.5,
                    "profit": 120,
                },
            }]
        })
        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0].ticker == "FTSE100"
        assert positions[0].strategy == "ibs_credit_spreads"
        assert positions[0].direction == "long"
        assert positions[0].size == 2.0
        assert positions[0].entry_price == 7500.5

    def test_unknown_deal_id_uses_epic(self, broker):
        broker.session.get.return_value = _mock_response(200, {
            "positions": [{
                "market": {"epic": "IX.D.FTSE.DAILY.IP"},
                "position": {
                    "dealId": "UNKNOWN_DEAL",
                    "direction": "SELL",
                    "size": 1,
                    "openLevel": 7400,
                    "profit": 0,
                },
            }]
        })
        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0].ticker == "IX.D.FTSE.DAILY.IP"
        assert positions[0].strategy == "unknown"

    def test_positions_api_failure(self, broker):
        broker.session.get.return_value = _mock_response(500)
        positions = broker.get_positions()
        assert positions == []

    def test_get_position_specific(self, broker):
        broker._deal_map["D1"] = ("SPX500", "ibs_credit_spreads")
        broker.session.get.return_value = _mock_response(200, {
            "positions": [{
                "market": {"epic": "IX.D.SPTRD.DAILY.IP"},
                "position": {
                    "dealId": "D1",
                    "direction": "BUY",
                    "size": 1,
                    "openLevel": 5500,
                    "profit": 0,
                },
            }]
        })
        pos = broker.get_position("SPX500", "ibs_credit_spreads")
        assert pos is not None
        assert pos.ticker == "SPX500"

    def test_get_position_not_found(self, broker):
        broker.session.get.return_value = _mock_response(200, {"positions": []})
        pos = broker.get_position("NONE", "none")
        assert pos is None


# ─── Market Info ─────────────────────────────────────────────────────────


class TestIGMarketInfo:
    def test_market_info_success(self, broker):
        broker.session.get.return_value = _mock_response(200, {
            "instrument": {"name": "FTSE 100", "expiry": "DFB"},
            "snapshot": {"marketStatus": "TRADEABLE"},
        })
        info = broker.get_market_info("IX.D.FTSE.DAILY.IP")
        assert info is not None
        assert info["instrument"]["name"] == "FTSE 100"

    def test_market_info_403_blocks_epic(self, broker):
        broker.session.get.return_value = _mock_response(403)
        info = broker.get_market_info("BLOCKED.EPIC")
        assert info is None
        assert "BLOCKED.EPIC" in broker._blocked_epics

    def test_market_info_404_blocks_epic(self, broker):
        broker.session.get.return_value = _mock_response(404)
        info = broker.get_market_info("MISSING.EPIC")
        assert info is None
        assert "MISSING.EPIC" in broker._blocked_epics

    def test_blocked_epic_skipped(self, broker):
        broker._blocked_epics.add("SKIP.ME")
        info = broker.get_market_info("SKIP.ME")
        assert info is None
        broker.session.get.assert_not_called()

    @patch("broker.ig.config")
    def test_get_epic_from_config(self, mock_config, broker):
        mock_config.MARKET_MAP = {
            "FTSE100": {"epic": "IX.D.FTSE.DAILY.IP"},
        }
        epic = broker.get_epic("FTSE100")
        assert epic == "IX.D.FTSE.DAILY.IP"

    @patch("broker.ig.config")
    def test_get_epic_blocked(self, mock_config, broker):
        mock_config.MARKET_MAP = {
            "FTSE100": {"epic": "IX.D.FTSE.DAILY.IP"},
        }
        broker._blocked_epics.add("IX.D.FTSE.DAILY.IP")
        epic = broker.get_epic("FTSE100")
        assert epic is None

    @patch("broker.ig.config")
    def test_get_epic_missing_ticker(self, mock_config, broker):
        mock_config.MARKET_MAP = {}
        epic = broker.get_epic("NONEXISTENT")
        assert epic is None


# ─── Order Placement ─────────────────────────────────────────────────────


class TestIGOrders:
    @patch("broker.ig.time.sleep")
    @patch("broker.ig.config")
    def test_place_long_success(self, mock_config, mock_sleep, broker):
        mock_config.MARKET_MAP = {
            "FTSE100": {"epic": "IX.D.FTSE.DAILY.IP", "currency": "GBP"},
        }
        # Mock market info
        market_resp = _mock_response(200, {
            "dealingRules": {"minNormalStopOrLimitDistance": {"value": 10}},
            "instrument": {"expiry": "DFB"},
        })
        # Mock order placement
        order_resp = _mock_response(200, {"dealReference": "REF123"})
        # Mock confirm
        confirm_resp = _mock_response(200, {
            "dealStatus": "ACCEPTED",
            "dealId": "DEAL123",
            "level": 7500.5,
            "reason": "",
        })

        broker.session.get.side_effect = [market_resp, confirm_resp]
        broker.session.post.return_value = order_resp

        result = broker.place_long("FTSE100", 1.0, "ibs_credit_spreads")
        assert result.success is True
        assert result.order_id == "DEAL123"
        assert result.fill_price == 7500.5
        assert ("FTSE100", "ibs_credit_spreads") == broker._deal_map["DEAL123"]

    @patch("broker.ig.time.sleep")
    @patch("broker.ig.config")
    def test_place_order_not_connected(self, mock_config, mock_sleep):
        b = IGBroker(is_demo=True)
        mock_config.MARKET_MAP = {"FTSE100": {"epic": "IX.D.FTSE.DAILY.IP"}}
        result = b.place_long("FTSE100", 1, "test")
        assert result.success is False
        assert "not connected" in result.message.lower()

    @patch("broker.ig.time.sleep")
    @patch("broker.ig.config")
    def test_place_order_blocked_epic(self, mock_config, mock_sleep, broker):
        mock_config.MARKET_MAP = {"FTSE100": {"epic": "BLOCKED"}}
        broker._blocked_epics.add("BLOCKED")
        result = broker.place_long("FTSE100", 1, "test")
        assert result.success is False
        assert "blocked" in result.message.lower() or "no accessible" in result.message.lower()

    @patch("broker.ig.time.sleep")
    @patch("broker.ig.config")
    def test_place_order_403_blocks_epic(self, mock_config, mock_sleep, broker):
        mock_config.MARKET_MAP = {"FTSE100": {"epic": "IX.D.FTSE.DAILY.IP", "currency": "GBP"}}

        market_resp = _mock_response(200, {
            "dealingRules": {},
            "instrument": {"expiry": "DFB"},
        })
        broker.session.get.return_value = market_resp
        broker.session.post.return_value = _mock_response(403, text="No access")

        result = broker.place_long("FTSE100", 1, "test")
        assert result.success is False
        assert "IX.D.FTSE.DAILY.IP" in broker._blocked_epics

    @patch("broker.ig.time.sleep")
    @patch("broker.ig.config")
    def test_place_order_rejected(self, mock_config, mock_sleep, broker):
        mock_config.MARKET_MAP = {"FTSE100": {"epic": "IX.D.FTSE.DAILY.IP", "currency": "GBP"}}

        market_resp = _mock_response(200, {
            "dealingRules": {},
            "instrument": {"expiry": "DFB"},
        })
        order_resp = _mock_response(200, {"dealReference": "REF123"})
        confirm_resp = _mock_response(200, {
            "dealStatus": "REJECTED",
            "dealId": "DEAL123",
            "level": 0,
            "reason": "MARKET_CLOSED",
        })

        broker.session.get.side_effect = [market_resp, confirm_resp]
        broker.session.post.return_value = order_resp

        result = broker.place_long("FTSE100", 1, "test")
        assert result.success is False
        assert "rejected" in result.message.lower()

    @patch("broker.ig.time.sleep")
    @patch("broker.ig.config")
    def test_place_order_no_deal_ref(self, mock_config, mock_sleep, broker):
        mock_config.MARKET_MAP = {"FTSE100": {"epic": "IX.D.FTSE.DAILY.IP", "currency": "GBP"}}

        market_resp = _mock_response(200, {
            "dealingRules": {},
            "instrument": {"expiry": "DFB"},
        })
        order_resp = _mock_response(200, {})  # No dealReference
        broker.session.get.return_value = market_resp
        broker.session.post.return_value = order_resp

        result = broker.place_long("FTSE100", 1, "test")
        assert result.success is False
        assert "no deal reference" in result.message.lower()


# ─── Option Spread Trading ──────────────────────────────────────────────


class TestIGOptionSpreads:
    @patch("broker.ig.time.sleep")
    def test_spread_success(self, mock_sleep, broker):
        """Both legs fill successfully."""
        market_resp = _mock_response(200, {
            "instrument": {"expiry": "07-MAR-26"},
            "snapshot": {"marketStatus": "TRADEABLE"},
        })
        order_resp = _mock_response(200, {"dealReference": "REF-S"})
        confirm_short = _mock_response(200, {
            "dealStatus": "ACCEPTED", "dealId": "SHORT1", "level": 15.5,
        })
        order_resp2 = _mock_response(200, {"dealReference": "REF-L"})
        confirm_long = _mock_response(200, {
            "dealStatus": "ACCEPTED", "dealId": "LONG1", "level": 5.2,
        })

        broker.session.get.side_effect = [market_resp, confirm_short, market_resp, confirm_long]
        broker.session.post.side_effect = [order_resp, order_resp2]

        result = broker.place_option_spread(
            "OP.D.SPX.5400.P", "OP.D.SPX.5300.P", 1.0, "SPX500", "ibs_credit_spreads"
        )
        assert result.success is True
        assert result.short_deal_id == "SHORT1"
        assert result.long_deal_id == "LONG1"
        assert result.net_premium == pytest.approx(10.3)

    @patch("broker.ig.time.sleep")
    def test_spread_short_leg_fails(self, mock_sleep, broker):
        """Short leg fails — no rollback needed."""
        broker._blocked_epics.add("OP.D.SPX.5400.P")
        result = broker.place_option_spread(
            "OP.D.SPX.5400.P", "OP.D.SPX.5300.P", 1.0, "SPX", "test"
        )
        assert result.success is False
        assert "short leg" in result.message.lower() or "blocked" in result.message.lower()

    @patch("broker.ig.time.sleep")
    def test_spread_long_leg_fails_rollback(self, mock_sleep, broker):
        """Long leg fails — must close short leg (rollback)."""
        # Short leg succeeds
        market_resp = _mock_response(200, {
            "instrument": {"expiry": "07-MAR-26"},
        })
        short_order_resp = _mock_response(200, {"dealReference": "REF-S"})
        short_confirm = _mock_response(200, {
            "dealStatus": "ACCEPTED", "dealId": "SHORT1", "level": 15.0,
        })

        # Long leg: EPIC blocked
        broker._blocked_epics.add("OP.D.SPX.5300.P")

        # Rollback close
        rollback_order_resp = _mock_response(200, {"dealReference": "REF-RB"})
        rollback_confirm = _mock_response(200, {
            "dealStatus": "ACCEPTED", "dealId": "CLOSE-SHORT1", "level": 14.5,
        })

        broker.session.get.side_effect = [market_resp, short_confirm, rollback_confirm]
        broker.session.post.side_effect = [short_order_resp, rollback_order_resp]

        result = broker.place_option_spread(
            "OP.D.SPX.5400.P", "OP.D.SPX.5300.P", 1.0, "SPX", "test"
        )
        assert result.success is False
        assert "reversed" in result.message.lower() or "long leg" in result.message.lower()

    def test_spread_not_connected(self):
        b = IGBroker(is_demo=True)
        result = b.place_option_spread("A", "B", 1, "SPX", "test")
        assert result.success is False
        assert "not connected" in result.message.lower()


# ─── Option Leg Validation ──────────────────────────────────────────────


class TestIGOptionValidation:
    def test_validate_tradeable_leg(self, broker):
        broker.session.get.return_value = _mock_response(200, {
            "snapshot": {"marketStatus": "TRADEABLE"},
            "dealingRules": {"minDealSize": {"value": 0.5}},
        })
        result = broker.validate_option_leg("OP.D.SPX.5400.P", 1.0)
        assert result["ok"] is True

    def test_validate_market_closed(self, broker):
        broker.session.get.return_value = _mock_response(200, {
            "snapshot": {"marketStatus": "CLOSED"},
            "dealingRules": {},
        })
        result = broker.validate_option_leg("OP.D.SPX.5400.P", 1.0)
        assert result["ok"] is False
        assert result["code"] == "MARKET_NOT_TRADEABLE"

    def test_validate_size_below_min(self, broker):
        broker.session.get.return_value = _mock_response(200, {
            "snapshot": {"marketStatus": "TRADEABLE"},
            "dealingRules": {"minDealSize": {"value": 2.0}},
        })
        result = broker.validate_option_leg("OP.D.SPX.5400.P", 0.5)
        assert result["ok"] is False
        assert result["code"] == "SIZE_BELOW_MIN"

    def test_validate_no_market_info(self, broker):
        broker._blocked_epics.add("NOPE")
        result = broker.validate_option_leg("NOPE", 1.0)
        assert result["ok"] is False
        assert result["code"] == "NO_MARKET_INFO"


# ─── Option Search ──────────────────────────────────────────────────────


class TestIGOptionSearch:
    def test_search_returns_options(self, broker):
        broker.session.get.return_value = _mock_response(200, {
            "markets": [
                {
                    "epic": "OP.D.SPX.5400.P.IP",
                    "instrumentName": "US 500 5400 Put 07-MAR-26",
                    "bid": 15.5,
                    "offer": 17.0,
                    "expiry": "07-MAR-26",
                },
            ]
        })
        results = broker.search_option_markets("US 500 PUT")
        assert len(results) == 1
        assert results[0].strike == 5400.0
        assert results[0].option_type == "PUT"
        assert results[0].bid == 15.5

    def test_search_skips_blocked_epics(self, broker):
        broker._blocked_epics.add("OP.D.SPX.5400.P.IP")
        broker.session.get.return_value = _mock_response(200, {
            "markets": [{"epic": "OP.D.SPX.5400.P.IP", "instrumentName": "test", "bid": 0, "offer": 0}]
        })
        results = broker.search_option_markets("US 500")
        assert len(results) == 0

    def test_search_api_failure(self, broker):
        broker.session.get.return_value = _mock_response(500)
        results = broker.search_option_markets("US 500")
        assert results == []

    def test_search_not_connected(self):
        b = IGBroker(is_demo=True)
        results = b.search_option_markets("US 500")
        assert results == []


# ─── Failure Injection: Network Errors ───────────────────────────────────


class TestIGNetworkFailures:
    def test_account_info_network_exception(self, broker):
        broker.session.get.side_effect = Exception("Connection refused")
        info = broker.get_account_info()
        assert info.balance == 0

    def test_positions_network_exception(self, broker):
        broker.session.get.side_effect = Exception("Timeout")
        positions = broker.get_positions()
        assert positions == []

    def test_market_info_network_exception(self, broker):
        broker.session.get.side_effect = Exception("DNS failure")
        info = broker.get_market_info("IX.D.FTSE.DAILY.IP")
        assert info is None

    @patch("broker.ig.time.sleep")
    @patch("broker.ig.config")
    def test_order_network_exception(self, mock_config, mock_sleep, broker):
        mock_config.MARKET_MAP = {"FTSE100": {"epic": "IX.D.FTSE.DAILY.IP", "currency": "GBP"}}

        market_resp = _mock_response(200, {
            "dealingRules": {},
            "instrument": {"expiry": "DFB"},
        })
        broker.session.get.return_value = market_resp
        broker.session.post.side_effect = Exception("Network error")

        result = broker.place_long("FTSE100", 1, "test")
        assert result.success is False

    @patch("broker.ig.time.sleep")
    def test_confirm_endpoint_failure_fallback(self, mock_sleep, broker):
        """If confirm returns non-200, should fallback to position check."""
        # First get: confirm fails
        # Second get: positions found
        confirm_fail = _mock_response(500)
        positions_resp = _mock_response(200, {
            "positions": [{
                "market": {"epic": "TEST"},
                "position": {"dealId": "D1", "direction": "BUY", "size": 1, "openLevel": 100, "profit": 0},
            }]
        })
        broker.session.get.side_effect = [confirm_fail, positions_resp]

        result = broker._confirm_deal("REF1", "TEST", "test_strat", 1.0)
        assert result.success is True
        assert "confirm unavailable" in result.message.lower()


# ─── Deal Map Tracking ──────────────────────────────────────────────────


class TestDealMapTracking:
    @patch("broker.ig.time.sleep")
    def test_deal_map_populated_on_fill(self, mock_sleep, broker):
        confirm_resp = _mock_response(200, {
            "dealStatus": "ACCEPTED",
            "dealId": "NEW_DEAL",
            "level": 7500,
        })
        broker.session.get.return_value = confirm_resp

        result = broker._confirm_deal("REF1", "FTSE100", "ibs_credit_spreads", 1.0)
        assert result.success is True
        assert broker._deal_map["NEW_DEAL"] == ("FTSE100", "ibs_credit_spreads")

    def test_deal_map_not_populated_on_reject(self, broker):
        confirm_resp = _mock_response(200, {
            "dealStatus": "REJECTED",
            "dealId": "BAD_DEAL",
            "reason": "MARKET_CLOSED",
        })
        broker.session.get.return_value = confirm_resp

        result = broker._confirm_deal("REF1", "FTSE100", "test", 1.0)
        assert result.success is False
        assert "BAD_DEAL" not in broker._deal_map


# ─── Blocked EPICs ──────────────────────────────────────────────────────


class TestBlockedEPICs:
    def test_blocked_epic_persists(self, broker):
        broker.session.get.return_value = _mock_response(403)
        broker.get_market_info("BLOCKED.EPIC")
        assert "BLOCKED.EPIC" in broker._blocked_epics

        # Second call should not hit API
        broker.session.get.reset_mock()
        broker.get_market_info("BLOCKED.EPIC")
        broker.session.get.assert_not_called()

    def test_blocked_epic_prevents_order(self, broker):
        broker._blocked_epics.add("TEST.EPIC")
        result = broker._place_option_leg("TEST.EPIC", "SELL", 1.0, "TEST", "strat")
        assert result.success is False
        assert "blocked" in result.message.lower()

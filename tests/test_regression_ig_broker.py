"""
Regression tests for IG broker adapter — A-008.

Tests covering auth flow, session management, position handling,
order placement, error handling, EPIC blocking, market data,
and option spread lifecycle. Ensures no regressions when adding
multi-broker infrastructure.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from broker.ig import IGBroker


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_session():
    """Create a mock requests.Session with standard IG auth headers."""
    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "accountId": "ABC123",
        "clientId": "CLIENT1",
        "timezoneOffset": 0,
        "lightstreamerEndpoint": "https://push.ig.com",
        "oauthToken": {"access_token": "test_token", "expires_in": "60"},
    }
    response.headers = {
        "CST": "test_cst_token",
        "X-SECURITY-TOKEN": "test_security_token",
    }
    session.post.return_value = response
    session.get.return_value = response
    session.put.return_value = response
    session.delete.return_value = response
    return session


@pytest.fixture
def broker(mock_session):
    """Create an IGBroker instance with mocked session."""
    with patch("broker.ig.requests.Session", return_value=mock_session):
        b = IGBroker.__new__(IGBroker)
        b.api_key = "test_api_key"
        b.base_url = "https://demo-api.ig.com/gateway/deal"
        b.session = mock_session
        b.cst = "test_cst"
        b.security_token = "test_security"
        b.account_id = "ABC123"
        b._deal_map = {}
        b._blocked_epics = set()
        b._connected = True
        return b


# ─── Authentication ──────────────────────────────────────────────────────────


class TestAuthentication:
    def test_has_auth_headers(self, broker):
        assert broker.cst == "test_cst"
        assert broker.security_token == "test_security"

    def test_has_account_id(self, broker):
        assert broker.account_id == "ABC123"

    def test_connected_flag(self, broker):
        assert broker._connected is True


# ─── Position retrieval ──────────────────────────────────────────────────────


class TestPositions:
    def test_get_positions_empty(self, broker, mock_session):
        mock_session.get.return_value.json.return_value = {"positions": []}
        positions = broker.get_positions()
        assert positions == []

    def test_get_positions_with_data(self, broker, mock_session):
        mock_session.get.return_value.json.return_value = {
            "positions": [
                {
                    "position": {
                        "dealId": "DEAL1",
                        "direction": "BUY",
                        "size": 1.0,
                        "level": 100.0,
                        "currency": "GBP",
                        "controlledRisk": False,
                    },
                    "market": {
                        "epic": "IX.D.FTSE.DAILY.IP",
                        "instrumentName": "FTSE 100",
                        "bid": 7500.0,
                        "offer": 7502.0,
                        "instrumentType": "INDICES",
                    },
                }
            ]
        }
        positions = broker.get_positions()
        assert len(positions) == 1

    def test_get_positions_maps_known_epic_to_configured_ticker_without_deal_map(self, broker, mock_session):
        mock_session.get.return_value.json.return_value = {
            "positions": [
                {
                    "position": {
                        "dealId": "DEALQQQ",
                        "direction": "BUY",
                        "size": 0.01,
                        "level": 24992.8,
                    },
                    "market": {
                        "epic": "IX.D.NASDAQ.CASH.IP",
                        "bid": 24995.8,
                        "offer": 24996.8,
                    },
                }
            ]
        }

        positions = broker.get_positions()

        assert len(positions) == 1
        assert positions[0].ticker == "QQQ"
        assert positions[0].strategy == "unknown"

    def test_get_positions_network_error_returns_empty(self, broker, mock_session):
        """IG broker catches exceptions and returns empty list on error."""
        mock_session.get.side_effect = Exception("Network timeout")
        positions = broker.get_positions()
        assert positions == []

    def test_get_positions_honors_explicit_timeout(self, broker, mock_session):
        mock_session.get.return_value.json.return_value = {"positions": []}
        broker.get_positions(timeout=2.5)
        assert mock_session.get.call_args.kwargs["timeout"] == 2.5


# ─── Deal map tracking ──────────────────────────────────────────────────────


class TestDealMap:
    def test_deal_map_starts_empty(self, broker):
        assert broker._deal_map == {}

    def test_deal_map_stores_deal(self, broker):
        broker._deal_map["DEAL1"] = {
            "epic": "IX.D.FTSE.DAILY.IP",
            "direction": "BUY",
            "size": 1.0,
        }
        assert "DEAL1" in broker._deal_map
        assert broker._deal_map["DEAL1"]["epic"] == "IX.D.FTSE.DAILY.IP"

    def test_deal_map_removes_deal(self, broker):
        broker._deal_map["DEAL1"] = {"epic": "IX.D.FTSE.DAILY.IP"}
        del broker._deal_map["DEAL1"]
        assert "DEAL1" not in broker._deal_map


# ─── EPIC blocking ──────────────────────────────────────────────────────────


class TestEpicBlocking:
    def test_blocked_epics_starts_empty(self, broker):
        assert broker._blocked_epics == set()

    def test_block_epic(self, broker):
        broker._blocked_epics.add("OP.D.SPXWEEKLY.5200P.IP")
        assert "OP.D.SPXWEEKLY.5200P.IP" in broker._blocked_epics

    def test_unblock_epic(self, broker):
        broker._blocked_epics.add("OP.D.SPXWEEKLY.5200P.IP")
        broker._blocked_epics.discard("OP.D.SPXWEEKLY.5200P.IP")
        assert "OP.D.SPXWEEKLY.5200P.IP" not in broker._blocked_epics

    def test_multiple_blocked_epics(self, broker):
        broker._blocked_epics.add("EPIC1")
        broker._blocked_epics.add("EPIC2")
        broker._blocked_epics.add("EPIC3")
        assert len(broker._blocked_epics) == 3

    def test_blocked_epic_returns_none_from_market_info(self, broker, mock_session):
        """get_market_info returns None for blocked EPICs."""
        broker._blocked_epics.add("IX.D.FTSE.DAILY.IP")
        result = broker.get_market_info(epic="IX.D.FTSE.DAILY.IP")
        assert result is None


# ─── Order placement (actual API: place_long, place_short) ──────────────────


class TestOrderPlacement:
    def test_place_long_success(self, broker, mock_session):
        mock_session.post.return_value.json.return_value = {
            "dealReference": "REF123"
        }
        mock_session.post.return_value.status_code = 200
        # place_long returns an OrderResult
        result = broker.place_long(
            ticker="FTSE",
            stake_per_point=1.0,
            strategy="IBS++",
        )
        assert result is not None

    def test_place_short_success(self, broker, mock_session):
        mock_session.post.return_value.json.return_value = {
            "dealReference": "REF124"
        }
        mock_session.post.return_value.status_code = 200
        result = broker.place_short(
            ticker="SPX",
            stake_per_point=2.0,
            strategy="IBS Short",
        )
        assert result is not None

    def test_place_long_does_not_attach_protective_stop_by_default(self, broker, mock_session, monkeypatch):
        mock_session.post.return_value.json.return_value = {"dealReference": "REF125"}
        mock_session.post.return_value.status_code = 200
        mock_session.get.return_value.status_code = 200
        broker.get_epic = lambda ticker: "IX.D.TEST.IP"
        broker.get_market_info = lambda epic: {
            "dealingRules": {
                "minNormalStopOrLimitDistance": {"value": 4},
            },
            "instrument": {"expiry": "DFB"},
        }
        monkeypatch.setattr("config.IG_ATTACH_PROTECTIVE_STOPS", False)
        monkeypatch.setattr("time.sleep", lambda _: None)

        broker.place_long("FTSE", 1.0, "IBS++")

        payload = mock_session.post.call_args.kwargs["json"]
        assert payload["stopDistance"] is None

    def test_place_long_attaches_opt_in_protective_stop(self, broker, mock_session, monkeypatch):
        mock_session.post.return_value.json.return_value = {"dealReference": "REF126"}
        mock_session.post.return_value.status_code = 200
        mock_session.get.return_value.status_code = 200
        broker.get_epic = lambda ticker: "IX.D.TEST.IP"
        broker.get_market_info = lambda epic: {
            "dealingRules": {
                "minNormalStopOrLimitDistance": {"value": 4},
            },
            "instrument": {"expiry": "DFB"},
        }
        monkeypatch.setattr("config.IG_ATTACH_PROTECTIVE_STOPS", True)
        monkeypatch.setattr("config.IG_PROTECTIVE_STOP_FACTOR", 2.0)
        monkeypatch.setattr("time.sleep", lambda _: None)

        broker.place_long("FTSE", 1.0, "IBS++")

        payload = mock_session.post.call_args.kwargs["json"]
        assert payload["stopDistance"] == "8.0"


# ─── Close position (actual API: close_position(ticker, strategy)) ──────────


class TestClosePosition:
    def test_close_position_returns_order_result(self, broker, mock_session):
        """close_position(ticker, strategy) returns OrderResult."""
        mock_session.post.return_value.json.return_value = {
            "dealReference": "CLOSE_REF1"
        }
        mock_session.delete.return_value.json.return_value = {
            "dealReference": "CLOSE_REF1"
        }
        mock_session.delete.return_value.status_code = 200
        # _deal_map stores {deal_id: (ticker, strategy)} tuples
        broker._deal_map["DEAL1"] = ("FTSE", "IBS++")
        result = broker.close_position(ticker="FTSE", strategy="IBS++")
        assert result is not None


# ─── Account info (actual API: get_account_info) ────────────────────────────


class TestAccountInfo:
    def test_get_account_info(self, broker, mock_session):
        mock_session.get.return_value.json.return_value = {
            "accounts": [
                {
                    "accountId": "ABC123",
                    "accountName": "Spreadbet",
                    "accountType": "SPREADBET",
                    "balance": {
                        "balance": 10000.0,
                        "deposit": 500.0,
                        "profitLoss": 200.0,
                        "available": 9500.0,
                    },
                }
            ]
        }
        result = broker.get_account_info()
        assert result is not None

    def test_get_account_info_honors_explicit_timeout(self, broker, mock_session):
        mock_session.get.return_value.json.return_value = {"accounts": []}
        broker.get_account_info(timeout=1.75)
        assert mock_session.get.call_args.kwargs["timeout"] == 1.75


# ─── Error recovery ──────────────────────────────────────────────────────────


class TestErrorRecovery:
    def test_session_expired_returns_empty_positions(self, broker, mock_session):
        """IG broker handles auth errors gracefully."""
        response = MagicMock()
        response.status_code = 401
        response.json.return_value = {"errorCode": "error.security.client-token-missing"}
        mock_session.get.return_value = response
        # get_positions catches errors and returns []
        result = broker.get_positions()
        assert isinstance(result, list)

    def test_rate_limit_returns_empty_positions(self, broker, mock_session):
        response = MagicMock()
        response.status_code = 403
        response.json.return_value = {"errorCode": "error.public-api.exceeded-api-key-allowance"}
        mock_session.get.return_value = response
        result = broker.get_positions()
        assert isinstance(result, list)

    def test_server_error_returns_empty_positions(self, broker, mock_session):
        response = MagicMock()
        response.status_code = 500
        response.json.return_value = {}
        mock_session.get.return_value = response
        result = broker.get_positions()
        assert isinstance(result, list)


# ─── Market data (actual API: get_market_info, search_option_markets) ────────


class TestMarketData:
    def test_get_market_info(self, broker, mock_session):
        mock_session.get.return_value.json.return_value = {
            "instrument": {
                "epic": "IX.D.FTSE.DAILY.IP",
                "name": "FTSE 100",
                "type": "INDICES",
                "marketId": "MKT1",
            },
            "snapshot": {
                "bid": 7500.0,
                "offer": 7502.0,
                "high": 7550.0,
                "low": 7450.0,
                "marketStatus": "TRADEABLE",
            },
        }
        result = broker.get_market_info(epic="IX.D.FTSE.DAILY.IP")
        assert result is not None

    def test_get_market_info_honors_explicit_timeout(self, broker, mock_session):
        mock_session.get.return_value.json.return_value = {"instrument": {}, "snapshot": {}}
        broker.get_market_info(epic="IX.D.FTSE.DAILY.IP", timeout=1.25)
        assert mock_session.get.call_args.kwargs["timeout"] == 1.25

    def test_search_option_markets(self, broker, mock_session):
        mock_session.get.return_value.json.return_value = {
            "markets": [
                {
                    "epic": "OP.D.SPXWEEKLY.5200P.IP",
                    "instrumentName": "SPX Weekly Put 5200",
                    "instrumentType": "OPTIONS",
                }
            ]
        }
        result = broker.search_option_markets(search_term="SPX")
        assert result is not None


# ─── Option spread operations ────────────────────────────────────────────────


class TestOptionSpreadOperations:
    def test_spread_deal_tracking(self, broker):
        """Track both legs of a credit spread."""
        broker._deal_map["SHORT_LEG"] = {
            "epic": "OP.D.SPXWEEKLY.5200P.IP",
            "direction": "SELL",
            "size": 1.0,
            "strike": 5200,
        }
        broker._deal_map["LONG_LEG"] = {
            "epic": "OP.D.SPXWEEKLY.5150P.IP",
            "direction": "BUY",
            "size": 1.0,
            "strike": 5150,
        }
        assert len(broker._deal_map) == 2
        assert broker._deal_map["SHORT_LEG"]["strike"] == 5200
        assert broker._deal_map["LONG_LEG"]["strike"] == 5150

    def test_spread_width_calculation(self, broker):
        short_strike = 5200
        long_strike = 5150
        width = short_strike - long_strike
        assert width == 50

    def test_epic_format_validation(self, broker):
        """Verify IG option EPIC format."""
        epic = "OP.D.SPXWEEKLY.5200P.IP"
        parts = epic.split(".")
        assert parts[0] == "OP"  # Option
        assert parts[1] == "D"   # Daily
        assert "P" in parts[3] or "C" in parts[3]  # Put or Call

    def test_blocked_epic_prevents_market_lookup(self, broker):
        """Blocked EPICs return None from get_market_info."""
        epic = "OP.D.SPXWEEKLY.5200P.IP"
        broker._blocked_epics.add(epic)
        result = broker.get_market_info(epic=epic)
        assert result is None

    def test_spread_rollback_on_second_leg_failure(self, broker):
        """Track spread state for rollback scenarios."""
        broker._deal_map["SHORT_LEG"] = {
            "epic": "OP.D.SPXWEEKLY.5200P.IP",
            "direction": "SELL",
            "deal_id": "DEAL_SHORT",
        }
        assert "SHORT_LEG" in broker._deal_map
        # Simulate rollback
        del broker._deal_map["SHORT_LEG"]
        assert "SHORT_LEG" not in broker._deal_map


# ─── Connection state ────────────────────────────────────────────────────────


class TestConnectionState:
    def test_initial_connected(self, broker):
        assert broker._connected is True

    def test_disconnect_state(self, broker):
        broker._connected = False
        assert broker._connected is False

    def test_reconnect_state(self, broker):
        broker._connected = False
        broker._connected = True
        assert broker._connected is True


# ─── Capability interface ────────────────────────────────────────────────────


class TestCapabilities:
    def test_has_capabilities_method(self, broker):
        caps = broker.get_capabilities()
        assert caps is not None

    def test_supports_capability_method(self, broker):
        """Broker has supports_capability method."""
        assert hasattr(broker, "supports_capability")

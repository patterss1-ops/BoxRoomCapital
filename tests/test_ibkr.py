"""
Tests for IBKR broker adapter (A-003).

All tests use a mock IB client — no TWS/Gateway connection required.
Covers: connect, disconnect, account info, positions, place order,
cancel order, order status, health check, capability declaration.
"""
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock
from types import SimpleNamespace

from broker.base import BrokerCapabilities
from broker.ibkr import IBKRBroker


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_mock_ib():
    """Create a mock IB client with sensible defaults."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.managedAccounts.return_value = ["DU1234567"]
    ib.reqCurrentTime.return_value = datetime(2026, 2, 28, 14, 0, 0)
    return ib


def _make_account_values(account="DU1234567"):
    """Create mock account values matching IBKR format."""
    return [
        SimpleNamespace(tag="NetLiquidation", value="100000.00", currency="USD", account=account, modelCode=""),
        SimpleNamespace(tag="TotalCashValue", value="50000.00", currency="USD", account=account, modelCode=""),
        SimpleNamespace(tag="UnrealizedPnL", value="1500.00", currency="USD", account=account, modelCode=""),
        SimpleNamespace(tag="BuyingPower", value="200000.00", currency="USD", account=account, modelCode=""),
    ]


def _make_ib_position(symbol="SPY", qty=10, avg_cost=4500.0, con_id=756733):
    """Create a mock IBKR position."""
    contract = SimpleNamespace(symbol=symbol, conId=con_id, secType="STK")
    return SimpleNamespace(
        account="DU1234567",
        contract=contract,
        position=qty,
        avgCost=avg_cost,
    )


def _make_trade(order_id=1, status="Submitted", filled=0, remaining=10, avg_price=0.0):
    """Create a mock Trade object."""
    order = SimpleNamespace(orderId=order_id, action="BUY", totalQuantity=10, orderType="MKT")
    order_status = SimpleNamespace(
        status=status,
        filled=filled,
        remaining=remaining,
        avgFillPrice=avg_price,
        lastFillPrice=avg_price,
    )
    return SimpleNamespace(
        order=order,
        orderStatus=order_status,
        contract=SimpleNamespace(symbol="SPY", conId=756733),
        fills=[],
        log=[],
        isDone=lambda: status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"),
        isActive=lambda: status in ("PendingSubmit", "ApiPending", "PreSubmitted", "Submitted"),
    )


@pytest.fixture
def mock_ib():
    return _make_mock_ib()


@pytest.fixture
def broker(mock_ib):
    """Create IBKRBroker with injected mock client."""
    b = IBKRBroker(
        host="127.0.0.1",
        port=7497,
        client_id=1,
        account="DU1234567",
        ib_client=mock_ib,
    )
    return b


# ─── Capability declaration ─────────────────────────────────────────────────


class TestCapabilities:
    def test_ibkr_supports_spot_etf(self):
        b = IBKRBroker(ib_client=MagicMock())
        assert b.capabilities.supports_spot_etf is True

    def test_ibkr_supports_options(self):
        b = IBKRBroker(ib_client=MagicMock())
        assert b.capabilities.supports_options is True

    def test_ibkr_supports_futures(self):
        b = IBKRBroker(ib_client=MagicMock())
        assert b.capabilities.supports_futures is True

    def test_ibkr_supports_short(self):
        b = IBKRBroker(ib_client=MagicMock())
        assert b.capabilities.supports_short is True

    def test_ibkr_supports_paper(self):
        b = IBKRBroker(ib_client=MagicMock())
        assert b.capabilities.supports_paper is True

    def test_ibkr_supports_live(self):
        b = IBKRBroker(ib_client=MagicMock())
        assert b.capabilities.supports_live is True

    def test_ibkr_no_spreadbet(self):
        b = IBKRBroker(ib_client=MagicMock())
        assert b.capabilities.supports_spreadbet is False

    def test_ibkr_no_cfd(self):
        b = IBKRBroker(ib_client=MagicMock())
        assert b.capabilities.supports_cfd is False

    def test_capability_via_helper(self):
        b = IBKRBroker(ib_client=MagicMock())
        assert b.supports_capability("supports_spot_etf") is True
        assert b.supports_capability("supports_spreadbet") is False


# ─── Connection ──────────────────────────────────────────────────────────────


class TestConnection:
    def test_connect_success(self, broker, mock_ib):
        result = broker.connect()
        assert result is True
        assert broker._connected is True
        mock_ib.connect.assert_called_once()

    def test_connect_sets_account_from_managed(self, mock_ib):
        b = IBKRBroker(host="127.0.0.1", port=7497, account="", ib_client=mock_ib)
        b.connect()
        assert b.account == "DU1234567"

    def test_connect_failure(self, broker, mock_ib):
        mock_ib.connect.side_effect = ConnectionRefusedError("No TWS")
        result = broker.connect()
        assert result is False
        assert broker._connected is False

    def test_disconnect(self, broker, mock_ib):
        broker._connected = True
        broker.disconnect()
        mock_ib.disconnect.assert_called_once()
        assert broker._connected is False

    def test_is_connected(self, broker, mock_ib):
        broker._connected = True
        assert broker.is_connected() is True
        mock_ib.isConnected.return_value = False
        assert broker.is_connected() is False

    def test_no_ib_client_connect(self):
        b = IBKRBroker(ib_client=None)
        b._ib = None
        result = b.connect()
        assert result is False


# ─── Account info ────────────────────────────────────────────────────────────


class TestAccountInfo:
    def test_get_account_info(self, broker, mock_ib):
        broker._connected = True
        mock_ib.accountValues.return_value = _make_account_values()
        mock_ib.positions.return_value = [_make_ib_position()]

        info = broker.get_account_info()
        assert info.balance == 50000.0
        assert info.equity == 100000.0
        assert info.unrealised_pnl == 1500.0
        assert info.open_positions == 1
        assert info.currency == "USD"

    def test_account_info_not_connected(self, broker, mock_ib):
        mock_ib.isConnected.return_value = False
        info = broker.get_account_info()
        assert info.balance == 0.0
        assert info.equity == 0.0


# ─── Positions ───────────────────────────────────────────────────────────────


class TestPositions:
    def test_get_positions_long(self, broker, mock_ib):
        broker._connected = True
        mock_ib.positions.return_value = [_make_ib_position("SPY", 10, 4500.0)]

        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0].ticker == "SPY"
        assert positions[0].direction == "long"
        assert positions[0].size == 10
        assert positions[0].entry_price == 450.0  # 4500 / 10

    def test_get_positions_short(self, broker, mock_ib):
        broker._connected = True
        mock_ib.positions.return_value = [_make_ib_position("QQQ", -5, 2000.0)]

        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0].direction == "short"
        assert positions[0].size == 5
        assert positions[0].entry_price == 400.0  # 2000 / 5

    def test_get_positions_empty(self, broker, mock_ib):
        broker._connected = True
        mock_ib.positions.return_value = []
        assert broker.get_positions() == []

    def test_get_positions_not_connected(self, broker, mock_ib):
        mock_ib.isConnected.return_value = False
        assert broker.get_positions() == []

    def test_get_position_found(self, broker, mock_ib):
        broker._connected = True
        mock_ib.positions.return_value = [_make_ib_position("SPY", 10, 4500.0)]
        pos = broker.get_position("SPY", "ibs")
        assert pos is not None
        assert pos.ticker == "SPY"

    def test_get_position_not_found(self, broker, mock_ib):
        broker._connected = True
        mock_ib.positions.return_value = [_make_ib_position("SPY", 10, 4500.0)]
        pos = broker.get_position("QQQ", "ibs")
        assert pos is None


# ─── Order placement ─────────────────────────────────────────────────────────


class TestOrderPlacement:
    def test_place_long_market_order(self, broker, mock_ib):
        broker._connected = True
        trade = _make_trade(order_id=42, status="Submitted")
        mock_ib.qualifyContracts.return_value = [SimpleNamespace(symbol="SPY")]
        mock_ib.placeOrder.return_value = trade

        result = broker.place_long("SPY", 10, "ibs")
        assert result.success is True
        assert result.order_id == "42"
        mock_ib.placeOrder.assert_called_once()

    def test_place_short_market_order(self, broker, mock_ib):
        broker._connected = True
        trade = _make_trade(order_id=43, status="Submitted")
        mock_ib.qualifyContracts.return_value = [SimpleNamespace(symbol="QQQ")]
        mock_ib.placeOrder.return_value = trade

        result = broker.place_short("QQQ", 5, "ibs_short")
        assert result.success is True
        assert result.order_id == "43"

    def test_place_limit_order(self, broker, mock_ib):
        broker._connected = True
        trade = _make_trade(order_id=44, status="Submitted")
        mock_ib.qualifyContracts.return_value = [SimpleNamespace(symbol="SPY")]
        mock_ib.placeOrder.return_value = trade

        result = broker.place_limit_order("SPY", 10, "BUY", 450.0, "ibs")
        assert result.success is True
        assert result.order_id == "44"

    def test_place_order_not_connected(self, broker, mock_ib):
        mock_ib.isConnected.return_value = False
        result = broker.place_long("SPY", 10, "ibs")
        assert result.success is False
        assert "not connected" in result.message.lower()

    def test_place_order_qualify_fails(self, broker, mock_ib):
        broker._connected = True
        mock_ib.qualifyContracts.return_value = []  # empty = failed to qualify

        result = broker.place_long("INVALID", 10, "ibs")
        assert result.success is False
        assert "qualify" in result.message.lower() or "failed" in result.message.lower()

    def test_place_order_broker_error(self, broker, mock_ib):
        broker._connected = True
        mock_ib.qualifyContracts.return_value = [SimpleNamespace(symbol="SPY")]
        mock_ib.placeOrder.side_effect = RuntimeError("Broker rejected")

        result = broker.place_long("SPY", 10, "ibs")
        assert result.success is False
        assert "rejected" in result.message.lower()

    def test_close_position(self, broker, mock_ib):
        broker._connected = True
        # Mock position
        mock_ib.positions.return_value = [_make_ib_position("SPY", 10, 4500.0)]
        # Mock order placement
        trade = _make_trade(order_id=45, status="Submitted")
        mock_ib.qualifyContracts.return_value = [SimpleNamespace(symbol="SPY")]
        mock_ib.placeOrder.return_value = trade

        result = broker.close_position("SPY", "ibs")
        assert result.success is True

    def test_close_position_not_found(self, broker, mock_ib):
        broker._connected = True
        mock_ib.positions.return_value = []

        result = broker.close_position("SPY", "ibs")
        assert result.success is False
        assert "no position" in result.message.lower()


# ─── Order management ────────────────────────────────────────────────────────


class TestOrderManagement:
    def test_cancel_order(self, broker, mock_ib):
        broker._connected = True
        trade = _make_trade(order_id=50, status="Submitted")
        broker._trade_map["50"] = trade

        result = broker.cancel_order("50")
        assert result.success is True
        mock_ib.cancelOrder.assert_called_once_with(trade.order)

    def test_cancel_unknown_order(self, broker, mock_ib):
        broker._connected = True
        result = broker.cancel_order("99999")
        assert result.success is False
        assert "no tracked trade" in result.message.lower()

    def test_cancel_not_connected(self, broker, mock_ib):
        mock_ib.isConnected.return_value = False
        result = broker.cancel_order("50")
        assert result.success is False

    def test_get_order_status(self, broker, mock_ib):
        trade = _make_trade(order_id=50, status="Filled", filled=10, remaining=0, avg_price=450.5)
        broker._trade_map["50"] = trade

        status = broker.get_order_status("50")
        assert status["status"] == "Filled"
        assert status["filled"] == 10
        assert status["remaining"] == 0
        assert status["avg_fill_price"] == 450.5

    def test_get_order_status_unknown(self, broker, mock_ib):
        status = broker.get_order_status("99999")
        assert status["status"] == "UNKNOWN"

    def test_get_open_orders(self, broker, mock_ib):
        broker._connected = True
        trade = _make_trade(order_id=60, status="Submitted", filled=0, remaining=10)
        trade.contract = SimpleNamespace(symbol="SPY", conId=756733)
        mock_ib.openTrades.return_value = [trade]

        orders = broker.get_open_orders()
        assert len(orders) == 1
        assert orders[0]["symbol"] == "SPY"
        assert orders[0]["status"] == "Submitted"


# ─── Health check ────────────────────────────────────────────────────────────


class TestHealthCheck:
    def test_health_connected(self, broker, mock_ib):
        broker._connected = True
        health = broker.health_check()
        assert health["connected"] is True
        assert health["broker"] == "ibkr"
        assert health["account"] == "DU1234567"
        assert health["server_time"] is not None
        assert health["error"] is None

    def test_health_disconnected(self, broker, mock_ib):
        mock_ib.isConnected.return_value = False
        health = broker.health_check()
        assert health["connected"] is False
        assert health["error"] == "Not connected"

    def test_health_error(self, broker, mock_ib):
        broker._connected = True
        mock_ib.reqCurrentTime.side_effect = RuntimeError("Timeout")
        health = broker.health_check()
        assert health["error"] == "Timeout"


# ─── Integration: connect → place → status → cancel ─────────────────────────


class TestIntegrationFlow:
    """End-to-end flow with mocked transport."""

    def test_full_order_lifecycle(self, broker, mock_ib):
        # 1. Connect
        broker.connect()
        assert broker._connected is True

        # 2. Check account
        mock_ib.accountValues.return_value = _make_account_values()
        mock_ib.positions.return_value = []
        info = broker.get_account_info()
        assert info.equity == 100000.0

        # 3. Place order
        trade = _make_trade(order_id=100, status="Submitted", filled=0, remaining=10)
        mock_ib.qualifyContracts.return_value = [SimpleNamespace(symbol="SPY")]
        mock_ib.placeOrder.return_value = trade
        result = broker.place_long("SPY", 10, "ibs")
        assert result.success is True
        assert result.order_id == "100"

        # 4. Check status — still working
        status = broker.get_order_status("100")
        assert status["status"] == "Submitted"
        assert status["filled"] == 0

        # 5. Simulate fill
        trade.orderStatus.status = "Filled"
        trade.orderStatus.filled = 10
        trade.orderStatus.remaining = 0
        trade.orderStatus.avgFillPrice = 452.30
        status = broker.get_order_status("100")
        assert status["status"] == "Filled"
        assert status["filled"] == 10
        assert status["avg_fill_price"] == 452.30

    def test_order_cancel_lifecycle(self, broker, mock_ib):
        # Connect
        broker.connect()

        # Place limit order
        trade = _make_trade(order_id=101, status="Submitted", filled=0, remaining=5)
        mock_ib.qualifyContracts.return_value = [SimpleNamespace(symbol="QQQ")]
        mock_ib.placeOrder.return_value = trade
        result = broker.place_limit_order("QQQ", 5, "BUY", 380.0, "ibs")
        assert result.success is True

        # Cancel
        cancel_result = broker.cancel_order("101")
        assert cancel_result.success is True

        # Simulate cancelled status
        trade.orderStatus.status = "Cancelled"
        status = broker.get_order_status("101")
        assert status["status"] == "Cancelled"

    def test_connect_place_disconnect(self, broker, mock_ib):
        # Connect
        broker.connect()

        # Place order
        trade = _make_trade(order_id=102, status="Filled", filled=10, remaining=0, avg_price=450.0)
        mock_ib.qualifyContracts.return_value = [SimpleNamespace(symbol="SPY")]
        mock_ib.placeOrder.return_value = trade
        result = broker.place_long("SPY", 10, "ibs")
        assert result.success is True

        # Disconnect
        broker.disconnect()
        assert broker._connected is False

        # Should fail after disconnect
        mock_ib.isConnected.return_value = False
        result2 = broker.place_long("SPY", 10, "ibs")
        assert result2.success is False


# ─── Capability policy integration ──────────────────────────────────────────


class TestCapabilityPolicyIntegration:
    """Test that IBKR adapter works with A-001 capability policy."""

    def test_ibkr_passes_isa_route(self):
        from execution.policy.capability_policy import (
            validate_route_capabilities,
            RouteAccountType,
            StrategyRequirements,
        )

        b = IBKRBroker(ib_client=MagicMock())
        result = validate_route_capabilities(
            broker=b,
            account_type=RouteAccountType.ISA,
            requirements=StrategyRequirements(requires_spot_etf=True),
        )
        assert result.allowed is True

    def test_ibkr_passes_gia_short(self):
        from execution.policy.capability_policy import (
            validate_route_capabilities,
            RouteAccountType,
            StrategyRequirements,
        )

        b = IBKRBroker(ib_client=MagicMock())
        result = validate_route_capabilities(
            broker=b,
            account_type=RouteAccountType.GIA,
            requirements=StrategyRequirements(requires_short=True),
        )
        assert result.allowed is True

    def test_ibkr_rejects_spreadbet_route(self):
        from execution.policy.capability_policy import (
            validate_route_capabilities,
            RouteAccountType,
            StrategyRequirements,
        )

        b = IBKRBroker(ib_client=MagicMock())
        result = validate_route_capabilities(
            broker=b,
            account_type=RouteAccountType.SPREADBET,
            requirements=StrategyRequirements(requires_spreadbet=True),
        )
        assert result.allowed is False
        assert "supports_spreadbet" in result.missing_capabilities

    def test_ibkr_passes_paper_route(self):
        from execution.policy.capability_policy import (
            validate_route_capabilities,
            RouteAccountType,
            StrategyRequirements,
        )

        b = IBKRBroker(ib_client=MagicMock())
        result = validate_route_capabilities(
            broker=b,
            account_type=RouteAccountType.PAPER,
            requirements=StrategyRequirements(requires_paper=True),
        )
        assert result.allowed is True

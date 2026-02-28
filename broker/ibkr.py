"""
Interactive Brokers adapter — ib_async (paper and live).

Supports ISA + GIA accounts for stocks, ETFs, options, futures, FX, bonds.
Uses ib_async library (successor to ib_insync) for TWS/IB Gateway connectivity.

Phase A scope: paper trading MVP — connect, account summary, positions,
market/limit orders for liquid ETFs, cancel, order status.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from broker.base import (
    BaseBroker,
    BrokerCapabilities,
    OrderResult,
    Position,
    AccountInfo,
)
import config

logger = logging.getLogger(__name__)

# Lazy import ib_async — allow the module to load even when the
# library is not installed (e.g. in test environments using mocks).
try:
    from ib_async import (
        IB,
        Stock as _Stock,
        MarketOrder as _MarketOrder,
        LimitOrder as _LimitOrder,
        Trade,
        Contract,
    )

    _IB_ASYNC_AVAILABLE = True
except ImportError:
    _IB_ASYNC_AVAILABLE = False

    # Lightweight stand-ins so the adapter can run against a mock IB client
    # without the real library installed.
    class _Stock:  # type: ignore[no-redef]
        def __init__(self, symbol: str, exchange: str, currency: str):
            self.symbol = symbol
            self.exchange = exchange
            self.currency = currency
            self.secType = "STK"
            self.conId = 0

    class _MarketOrder:  # type: ignore[no-redef]
        def __init__(self, action: str, totalQuantity: float):
            self.action = action
            self.totalQuantity = totalQuantity
            self.orderType = "MKT"

    class _LimitOrder:  # type: ignore[no-redef]
        def __init__(self, action: str, totalQuantity: float, lmtPrice: float):
            self.action = action
            self.totalQuantity = totalQuantity
            self.lmtPrice = lmtPrice
            self.orderType = "LMT"


class IBKRBroker(BaseBroker):
    """Interactive Brokers adapter via ib_async (TWS / IB Gateway)."""

    capabilities = BrokerCapabilities(
        supports_spreadbet=False,
        supports_cfd=False,
        supports_spot_etf=True,
        supports_options=True,
        supports_futures=True,
        supports_short=True,
        supports_paper=True,
        supports_live=True,
    )

    def __init__(
        self,
        host: str = None,
        port: int = None,
        client_id: int = None,
        account: str = None,
        *,
        ib_client: object = None,
    ):
        """
        Initialise IBKR adapter.

        Parameters
        ----------
        host : str
            TWS/Gateway hostname (default from config or 127.0.0.1).
        port : int
            TWS/Gateway port (default from config, 7497 for paper, 7496 for live).
        client_id : int
            Unique client identifier (default from config or 1).
        account : str
            Target account ID (default from config).
        ib_client : object
            Optional pre-configured IB client (for testing / dependency injection).
        """
        self.host = host or getattr(config, "IBKR_HOST", "127.0.0.1")
        self.port = port or getattr(config, "IBKR_PORT", 7497)
        self.client_id = client_id or getattr(config, "IBKR_CLIENT_ID", 1)
        self.account = account or getattr(config, "IBKR_ACCOUNT", "")
        self.currency = getattr(config, "IBKR_CURRENCY", "USD")

        # IB client — injected or created on connect
        if ib_client is not None:
            self._ib = ib_client
        elif _IB_ASYNC_AVAILABLE:
            self._ib = IB()
        else:
            self._ib = None

        # Internal tracking
        self._connected = False
        # Map order_id (str) → Trade object for status lookups
        self._trade_map: dict[str, object] = {}

    # ─── Connection ──────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Connect to TWS / IB Gateway. Returns True if successful."""
        if self._ib is None:
            logger.error("ib_async not available — cannot connect to IBKR")
            return False

        try:
            self._ib.connect(
                host=self.host,
                port=self.port,
                clientId=self.client_id,
                timeout=10,
                readonly=False,
                account=self.account,
            )
            self._connected = self._ib.isConnected()

            if self._connected:
                accounts = self._ib.managedAccounts()
                if not self.account and accounts:
                    self.account = accounts[0]
                mode = "PAPER" if self.port in (7497, 4002) else "LIVE"
                logger.info(
                    f"IBKR {mode} connected. Account: {self.account}. "
                    f"Host: {self.host}:{self.port}"
                )
            else:
                logger.error("IBKR connect returned but isConnected() is False")

            return self._connected

        except Exception as e:
            logger.error(f"IBKR connection failed: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Disconnect from TWS / IB Gateway."""
        if self._ib is not None and self._connected:
            try:
                self._ib.disconnect()
            except Exception as e:
                logger.warning(f"IBKR disconnect error (non-fatal): {e}")
            finally:
                self._connected = False
                logger.info("IBKR disconnected")

    def is_connected(self) -> bool:
        """Check current connection health."""
        if self._ib is None:
            return False
        try:
            return self._ib.isConnected()
        except Exception:
            return False

    # ─── Account info ────────────────────────────────────────────────────

    def get_account_info(self) -> AccountInfo:
        """Get account balance, equity, and P&L from IBKR."""
        if not self.is_connected():
            logger.error("IBKR not connected — cannot get account info")
            return AccountInfo(
                balance=0.0, equity=0.0, unrealised_pnl=0.0,
                open_positions=0, currency=self.currency,
            )

        values = self._ib.accountValues(account=self.account)
        positions = self._ib.positions(account=self.account)

        # Parse account values by tag
        parsed = {}
        for v in values:
            if v.currency in (self.currency, "BASE", ""):
                parsed[v.tag] = v.value

        net_liq = float(parsed.get("NetLiquidation", 0))
        cash = float(parsed.get("TotalCashValue", 0))
        unrealised = float(parsed.get("UnrealizedPnL", 0))

        return AccountInfo(
            balance=cash,
            equity=net_liq,
            unrealised_pnl=unrealised,
            open_positions=len(positions),
            currency=self.currency,
        )

    # ─── Positions ───────────────────────────────────────────────────────

    def get_positions(self) -> list[Position]:
        """Get all open positions from IBKR."""
        if not self.is_connected():
            logger.error("IBKR not connected — cannot get positions")
            return []

        ib_positions = self._ib.positions(account=self.account)
        result = []

        for p in ib_positions:
            direction = "long" if p.position > 0 else "short"
            result.append(
                Position(
                    ticker=p.contract.symbol,
                    direction=direction,
                    size=abs(p.position),
                    entry_price=p.avgCost / abs(p.position) if p.position != 0 else p.avgCost,
                    entry_time=datetime.now(),  # IBKR doesn't give entry time via positions()
                    strategy="",  # Strategy attribution handled by ledger (A-005)
                    unrealised_pnl=0.0,  # Will be enriched by portfolio P&L tracking
                    deal_id=str(p.contract.conId),
                )
            )

        return result

    def get_position(self, ticker: str, strategy: str) -> Optional[Position]:
        """Get position for a specific ticker. Strategy matching deferred to ledger."""
        positions = self.get_positions()
        for p in positions:
            if p.ticker == ticker:
                return p
        return None

    # ─── Order placement ─────────────────────────────────────────────────

    def _qualify_contract(self, ticker: str, exchange: str = "SMART") -> object:
        """Create and qualify an equity/ETF contract."""
        contract = _Stock(ticker, exchange, self.currency)
        qualified = self._ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(f"Could not qualify contract for {ticker} on {exchange}")
        return contract

    def place_long(
        self,
        ticker: str,
        stake_per_point: float,
        strategy: str,
    ) -> OrderResult:
        """Buy shares/ETF units on IBKR. stake_per_point is treated as quantity."""
        return self._place_order(
            ticker=ticker,
            quantity=stake_per_point,
            action="BUY",
            strategy=strategy,
        )

    def place_short(
        self,
        ticker: str,
        stake_per_point: float,
        strategy: str,
    ) -> OrderResult:
        """Short sell shares/ETF units on IBKR. stake_per_point is treated as quantity."""
        return self._place_order(
            ticker=ticker,
            quantity=stake_per_point,
            action="SELL",
            strategy=strategy,
        )

    def close_position(self, ticker: str, strategy: str) -> OrderResult:
        """Close an open position by placing the opposite order."""
        pos = self.get_position(ticker, strategy)
        if pos is None:
            return OrderResult(
                success=False,
                message=f"No position found for {ticker}",
            )

        action = "SELL" if pos.direction == "long" else "BUY"
        return self._place_order(
            ticker=ticker,
            quantity=pos.size,
            action=action,
            strategy=strategy,
        )

    def place_limit_order(
        self,
        ticker: str,
        quantity: float,
        action: str,
        limit_price: float,
        strategy: str = "",
    ) -> OrderResult:
        """Place a limit order (BUY or SELL) for an ETF/stock."""
        return self._place_order(
            ticker=ticker,
            quantity=quantity,
            action=action,
            strategy=strategy,
            order_type="LMT",
            limit_price=limit_price,
        )

    def _place_order(
        self,
        ticker: str,
        quantity: float,
        action: str,
        strategy: str = "",
        order_type: str = "MKT",
        limit_price: float = 0.0,
    ) -> OrderResult:
        """
        Internal order placement.

        Parameters
        ----------
        ticker : str
            Instrument symbol (e.g. 'SPY', 'QQQ', 'VUSA').
        quantity : float
            Number of shares/units.
        action : str
            'BUY' or 'SELL'.
        strategy : str
            Strategy identifier for audit trail.
        order_type : str
            'MKT' for market, 'LMT' for limit.
        limit_price : float
            Limit price (required for LMT orders).
        """
        if not self.is_connected():
            return OrderResult(
                success=False,
                message="IBKR not connected",
            )

        try:
            contract = self._qualify_contract(ticker)

            if order_type == "LMT":
                order = _LimitOrder(action, quantity, limit_price)
            else:
                order = _MarketOrder(action, quantity)

            trade = self._ib.placeOrder(contract, order)

            # Store trade for status tracking
            order_id = str(trade.order.orderId)
            self._trade_map[order_id] = trade

            logger.info(
                f"IBKR {order_type} {action}: {ticker} x{quantity} "
                f"[{strategy}] order_id={order_id}"
            )

            return OrderResult(
                success=True,
                order_id=order_id,
                fill_price=trade.orderStatus.avgFillPrice,
                fill_qty=trade.orderStatus.filled,
                message=f"Order submitted: {trade.orderStatus.status}",
                timestamp=datetime.now(),
            )

        except Exception as e:
            error_msg = f"IBKR order failed: {e}"
            logger.error(error_msg)
            return OrderResult(success=False, message=error_msg)

    # ─── Order management ────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an active order by order ID."""
        if not self.is_connected():
            return OrderResult(success=False, message="IBKR not connected")

        trade = self._trade_map.get(order_id)
        if trade is None:
            return OrderResult(
                success=False,
                message=f"No tracked trade for order_id={order_id}",
            )

        try:
            self._ib.cancelOrder(trade.order)
            logger.info(f"IBKR cancel requested for order_id={order_id}")
            return OrderResult(
                success=True,
                order_id=order_id,
                message="Cancel requested",
                timestamp=datetime.now(),
            )
        except Exception as e:
            error_msg = f"IBKR cancel failed: {e}"
            logger.error(error_msg)
            return OrderResult(success=False, order_id=order_id, message=error_msg)

    def get_order_status(self, order_id: str) -> dict:
        """
        Get current status of a tracked order.

        Returns dict with keys: order_id, status, filled, remaining,
        avg_fill_price, last_fill_price.
        """
        trade = self._trade_map.get(order_id)
        if trade is None:
            return {
                "order_id": order_id,
                "status": "UNKNOWN",
                "message": "Not tracked by this session",
            }

        os = trade.orderStatus
        return {
            "order_id": order_id,
            "status": os.status,
            "filled": os.filled,
            "remaining": os.remaining,
            "avg_fill_price": os.avgFillPrice,
            "last_fill_price": os.lastFillPrice,
        }

    def get_open_orders(self) -> list[dict]:
        """Get all active (unfilled) orders."""
        if not self.is_connected():
            return []

        open_trades = self._ib.openTrades()
        result = []
        for t in open_trades:
            result.append({
                "order_id": str(t.order.orderId),
                "symbol": t.contract.symbol,
                "action": t.order.action,
                "quantity": t.order.totalQuantity,
                "order_type": t.order.orderType,
                "status": t.orderStatus.status,
                "filled": t.orderStatus.filled,
                "remaining": t.orderStatus.remaining,
            })
        return result

    # ─── Health check ────────────────────────────────────────────────────

    def health_check(self) -> dict:
        """
        Return connection health status for control plane monitoring.

        Returns dict with: connected, host, port, account, server_time, error.
        """
        result = {
            "broker": "ibkr",
            "connected": False,
            "host": self.host,
            "port": self.port,
            "account": self.account,
            "server_time": None,
            "error": None,
        }

        if not self.is_connected():
            result["error"] = "Not connected"
            return result

        try:
            result["connected"] = True
            server_time = self._ib.reqCurrentTime()
            result["server_time"] = server_time.isoformat() if server_time else None
        except Exception as e:
            result["error"] = str(e)

        return result

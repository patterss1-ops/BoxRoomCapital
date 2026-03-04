"""
IG Markets broker — raw REST API implementation.
Uses direct HTTP calls (proven working in ping test) instead of trading-ig library.

API key from: https://labs.ig.com/
"""
import logging
import time
import requests
from datetime import datetime
from typing import Optional

from broker.base import (
    BaseBroker,
    BrokerCapabilities,
    OrderResult,
    Position,
    AccountInfo,
    OptionMarket,
    SpreadOrderResult,
)
import config

logger = logging.getLogger(__name__)


class IGBroker(BaseBroker):
    """IG Markets spread betting broker via REST API."""

    capabilities = BrokerCapabilities(
        supports_spreadbet=True,
        supports_cfd=True,
        supports_options=True,
        supports_short=True,
        supports_live=True,
    )

    def __init__(self, is_demo: bool = True):
        self.is_demo = is_demo
        self.base_url = (
            "https://demo-api.ig.com/gateway/deal" if is_demo
            else "https://api.ig.com/gateway/deal"
        )
        self.session: Optional[requests.Session] = None

        # Internal tracking: deal_id → (ticker, strategy)
        self._deal_map: dict[str, tuple[str, str]] = {}
        # EPICs that returned 403/404 — skip on future attempts
        self._blocked_epics: set = set()

    # ─── Connection ──────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Authenticate with IG API using V2 session."""
        try:
            self.session = requests.Session()
            self.session.headers.update({
                "Content-Type": "application/json; charset=UTF-8",
                "Accept": "application/json; charset=UTF-8",
                "X-IG-API-KEY": config.IG_API_KEY,
            })

            r = self.session.post(
                f"{self.base_url}/session",
                json={"identifier": config.IG_USERNAME, "password": config.IG_PASSWORD},
                headers={**self.session.headers, "Version": "2"},
            )

            if r.status_code != 200:
                logger.error(f"IG auth failed: {r.status_code} — {r.text[:200]}")
                return False

            self.session.headers.update({
                "CST": r.headers["CST"],
                "X-SECURITY-TOKEN": r.headers["X-SECURITY-TOKEN"],
            })

            auth = r.json()
            current_acc = auth.get("currentAccountId", "")

            # Switch to spread bet account if needed
            if config.IG_ACC_NUMBER and current_acc != config.IG_ACC_NUMBER:
                sw = self.session.put(
                    f"{self.base_url}/session",
                    json={"accountId": config.IG_ACC_NUMBER, "defaultAccount": "false"},
                    headers={**self.session.headers, "Version": "1"},
                )
                if sw.status_code == 200:
                    logger.info(f"Switched to account {config.IG_ACC_NUMBER}")
                else:
                    logger.warning(f"Account switch failed: {sw.status_code}")

            mode = "DEMO" if self.is_demo else "LIVE"
            logger.info(f"IG {mode} connected. Account: {config.IG_ACC_NUMBER or current_acc}")
            return True

        except Exception as e:
            logger.error(f"IG login error: {e}")
            return False

    def is_connected(self) -> bool:
        """Return True if session tokens are set (authenticated)."""
        if not self.session:
            return False
        headers = self.session.headers
        return bool(headers.get("CST") and headers.get("X-SECURITY-TOKEN"))

    def disconnect(self):
        """Let IG session expire naturally — explicit logout kills the web session."""
        if self.session:
            logger.info("IG session left to expire (preserves web session)")
            self.session = None

    def _headers(self, version: str = "1") -> dict:
        """Get headers with specified API version."""
        if not self.session:
            return {}
        return {**self.session.headers, "Version": version}

    # ─── Account info ────────────────────────────────────────────────────

    def get_account_info(self) -> AccountInfo:
        """Get account balance and margin info."""
        if not self.session:
            return AccountInfo(balance=0, equity=0, unrealised_pnl=0, open_positions=0)

        try:
            r = self.session.get(f"{self.base_url}/accounts", headers=self._headers("1"))
            if r.status_code != 200:
                logger.error(f"Account fetch failed: {r.status_code}")
                return AccountInfo(balance=0, equity=0, unrealised_pnl=0, open_positions=0)

            for acc in r.json().get("accounts", []):
                if acc.get("accountId") == config.IG_ACC_NUMBER or acc.get("accountType") == "SPREADBET":
                    bal = acc.get("balance", {})
                    balance = float(bal.get("balance", 0))
                    pnl = float(bal.get("profitLoss", 0))
                    return AccountInfo(
                        balance=balance,
                        equity=balance + pnl,
                        unrealised_pnl=pnl,
                        open_positions=0,
                        currency=str(acc.get("currency", "GBP")),
                    )

        except Exception as e:
            logger.error(f"Error fetching account info: {e}")

        return AccountInfo(balance=0, equity=0, unrealised_pnl=0, open_positions=0)

    # ─── Positions ───────────────────────────────────────────────────────

    def get_positions(self) -> list[Position]:
        """Get all open positions from IG."""
        if not self.session:
            return []

        try:
            r = self.session.get(f"{self.base_url}/positions", headers=self._headers("2"))
            if r.status_code != 200:
                logger.error(f"Positions fetch failed: {r.status_code}")
                return []

            positions = []
            for p in r.json().get("positions", []):
                mkt = p.get("market", {})
                pos = p.get("position", {})
                deal_id = pos.get("dealId", "")
                epic = mkt.get("epic", "")
                direction = "long" if pos.get("direction") == "BUY" else "short"

                # Look up ticker and strategy from our deal map
                ticker, strategy = self._deal_map.get(deal_id, (epic, "unknown"))

                positions.append(Position(
                    ticker=ticker,
                    direction=direction,
                    size=float(pos.get("size", pos.get("dealSize", 0))),
                    entry_price=float(pos.get("openLevel", pos.get("level", 0))),
                    entry_time=datetime.now(),
                    strategy=strategy,
                    unrealised_pnl=float(pos.get("profit", 0) or 0),
                    deal_id=deal_id,
                ))
            return positions

        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    def get_position(self, ticker: str, strategy: str) -> Optional[Position]:
        """Get position for a specific ticker+strategy."""
        for p in self.get_positions():
            if p.ticker == ticker and p.strategy == strategy:
                return p
        return None

    # ─── Market info ─────────────────────────────────────────────────────

    def get_market_info(self, epic: str) -> Optional[dict]:
        """Get market details including min deal size and stop distance."""
        if not self.session:
            return None
        if epic in self._blocked_epics:
            return None
        try:
            r = self.session.get(f"{self.base_url}/markets/{epic}", headers=self._headers("3"))
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 403:
                logger.warning(f"EPIC {epic} blocked (403 — no exchange access). Skipping this market.")
                self._blocked_epics.add(epic)
            elif r.status_code == 404:
                logger.warning(f"EPIC {epic} not found (404). Check config.py.")
                self._blocked_epics.add(epic)
        except Exception as e:
            logger.error(f"Market info error for {epic}: {e}")
        return None

    def get_epic(self, ticker: str) -> Optional[str]:
        """Get the IG EPIC code for a ticker from config."""
        market = config.MARKET_MAP.get(ticker)
        if not market:
            return None
        epic = market["epic"]
        if epic in self._blocked_epics:
            return None  # Don't return blocked EPICs
        return epic

    def verify_markets(self) -> dict[str, bool]:
        """
        Verify all configured EPICs are accessible on this account.
        Tries alternatives from EPIC_ALTERNATIVES if primary fails.
        Returns dict of {ticker: accessible}.
        """
        if not self.session:
            return {}

        results = {}
        checked_epics = set()

        for ticker, info in config.MARKET_MAP.items():
            epic = info["epic"]
            if epic in checked_epics:
                results[ticker] = epic not in self._blocked_epics
                continue

            checked_epics.add(epic)
            mkt = self.get_market_info(epic)

            if mkt:
                results[ticker] = True
                inst = mkt.get("instrument", {})
                snap = mkt.get("snapshot", {})
                logger.info(f"  OK   {ticker:<14} {epic:<35} {inst.get('name', '?'):<30} status={snap.get('marketStatus', '?')}")
                config.MARKET_MAP[ticker]["verified"] = True
            else:
                # Try alternatives
                found = False
                alternatives = config.EPIC_ALTERNATIVES.get(epic, []) if hasattr(config, 'EPIC_ALTERNATIVES') else []
                for alt_epic in alternatives:
                    if alt_epic in self._blocked_epics:
                        continue
                    alt_mkt = self.get_market_info(alt_epic)
                    if alt_mkt:
                        inst = alt_mkt.get("instrument", {})
                        snap = alt_mkt.get("snapshot", {})
                        logger.info(f"  SWAP {ticker:<14} {epic} → {alt_epic:<35} {inst.get('name', '?')}")
                        config.MARKET_MAP[ticker]["epic"] = alt_epic
                        config.MARKET_MAP[ticker]["verified"] = True
                        results[ticker] = True
                        found = True
                        break

                if not found:
                    logger.warning(f"  FAIL {ticker:<14} {epic:<35} — not accessible, will skip")
                    results[ticker] = False

            time.sleep(0.3)  # Rate limit

        return results

    # ─── Order placement ─────────────────────────────────────────────────

    def place_long(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        """Open a long spread bet."""
        return self._place_order(ticker, "BUY", stake_per_point, strategy)

    def place_short(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        """Open a short spread bet."""
        return self._place_order(ticker, "SELL", stake_per_point, strategy)

    def _place_order(self, ticker: str, direction: str, stake: float, strategy: str) -> OrderResult:
        """Place a spread bet order via raw REST API."""
        if not self.session:
            return OrderResult(success=False, message="Not connected")

        epic = self.get_epic(ticker)
        if not epic:
            return OrderResult(success=False, message=f"No accessible EPIC for {ticker} (blocked or missing)")

        market_cfg = config.MARKET_MAP.get(ticker, {})
        currency = market_cfg.get("currency", "GBP")

        try:
            logger.info(f"Placing {direction} order: {ticker} ({epic}) @ £{stake}/pt [{strategy}]")

            # Get min stop distance and expiry info for this market
            stop_distance = None
            expiry = "DFB"  # Default for daily funded bets

            mkt_info = self.get_market_info(epic)
            if mkt_info:
                rules = mkt_info.get("dealingRules", {})
                min_stop = rules.get("minNormalStopOrLimitDistance", {}).get("value")
                if min_stop:
                    stop_distance = str(float(min_stop) * 2)  # 2x min for safety

                # For futures/monthly contracts, use the expiry from market info
                inst = mkt_info.get("instrument", {})
                mkt_expiry = inst.get("expiry", "")
                if mkt_expiry and mkt_expiry != "-" and mkt_expiry != "DFB":
                    expiry = mkt_expiry
                    logger.info(f"  Using futures expiry: {expiry}")

            order = {
                "epic": epic,
                "expiry": expiry,
                "direction": direction,
                "size": str(stake),
                "orderType": "MARKET",
                "currencyCode": currency,
                "forceOpen": True,
                "guaranteedStop": False,
                "stopDistance": stop_distance,
                "limitDistance": None,
            }

            r = self.session.post(
                f"{self.base_url}/positions/otc",
                json=order,
                headers=self._headers("2"),
            )

            if r.status_code == 403:
                logger.warning(f"Order blocked (403): {ticker} ({epic}) — no exchange access. Blocking EPIC.")
                self._blocked_epics.add(epic)
                return OrderResult(success=False, message=f"403: no exchange access for {epic}")
            elif r.status_code != 200:
                logger.error(f"Order HTTP error: {r.status_code} — {r.text[:200]}")
                return OrderResult(success=False, message=f"HTTP {r.status_code}: {r.text[:100]}")

            deal_ref = r.json().get("dealReference", "")
            if not deal_ref:
                return OrderResult(success=False, message="No deal reference returned")

            # Confirm the deal
            time.sleep(1)
            return self._confirm_deal(deal_ref, ticker, strategy, stake)

        except Exception as e:
            logger.error(f"Order error: {e}")
            return OrderResult(success=False, message=str(e))

    def _confirm_deal(self, deal_ref: str, ticker: str, strategy: str, size: float) -> OrderResult:
        """Confirm a deal and return the result."""
        try:
            r = self.session.get(
                f"{self.base_url}/confirms/{deal_ref}",
                headers=self._headers("1"),
            )

            if r.status_code == 200:
                conf = r.json()
                deal_status = conf.get("dealStatus", "")
                deal_id = conf.get("dealId", deal_ref)
                fill_level = float(conf.get("level", 0) or 0)
                reason = conf.get("reason", "")

                if deal_status == "ACCEPTED":
                    self._deal_map[deal_id] = (ticker, strategy)
                    logger.info(f"Order filled: {deal_id} @ {fill_level}")
                    return OrderResult(
                        success=True,
                        order_id=deal_id,
                        fill_price=fill_level,
                        fill_qty=size,
                        timestamp=datetime.now(),
                    )
                else:
                    logger.error(f"Order rejected: {reason}")
                    return OrderResult(success=False, message=f"Rejected: {reason}")

            # Confirm endpoint failed — check positions directly
            logger.warning(f"Confirm returned {r.status_code}, checking positions...")
            time.sleep(1)
            positions = self.get_positions()
            if positions:
                # Assume the last position is ours
                return OrderResult(
                    success=True,
                    order_id=deal_ref,
                    fill_qty=size,
                    timestamp=datetime.now(),
                    message="Placed (confirm unavailable, position found)",
                )

            return OrderResult(success=False, message=f"Confirm failed: {r.status_code}")

        except Exception as e:
            logger.error(f"Confirm error: {e}")
            return OrderResult(success=False, message=str(e))

    # ─── Options trading ──────────────────────────────────────────────────

    def search_option_markets(self, search_term: str) -> list[OptionMarket]:
        """
        Search IG for option markets matching search_term.
        E.g. search_term="US 500 PUT" to find S&P put options.
        Returns list of OptionMarket with bid/offer/strike info.
        """
        if not self.session:
            return []

        try:
            r = self.session.get(
                f"{self.base_url}/markets",
                params={"searchTerm": search_term},
                headers=self._headers("1"),
            )
            if r.status_code != 200:
                logger.error(f"Option search failed: {r.status_code}")
                return []

            results = []
            for mkt in r.json().get("markets", []):
                epic = mkt.get("epic", "")
                if not epic or epic in self._blocked_epics:
                    continue

                # Extract strike from instrument name (e.g. "US 500 5400 Put 07-MAR-26")
                name = mkt.get("instrumentName", "")
                bid = float(mkt.get("bid", 0) or 0)
                offer = float(mkt.get("offer", 0) or 0)
                mid = (bid + offer) / 2 if bid and offer else 0

                # Determine option type from name or EPIC
                opt_type = "PUT" if "put" in name.lower() or ".P." in epic.upper() else "CALL"

                # Parse strike from name — IG names are like "US 500 5400 Put 07-MAR-26"
                strike = 0.0
                parts = name.split()
                for part in parts:
                    try:
                        val = float(part.replace(",", ""))
                        if val > 100:  # Likely a strike price, not a market number
                            strike = val
                    except ValueError:
                        continue

                spread_pct = ((offer - bid) / mid * 100) if mid > 0 else 999

                results.append(OptionMarket(
                    epic=epic,
                    strike=strike,
                    option_type=opt_type,
                    expiry=mkt.get("expiry", ""),
                    bid=bid,
                    offer=offer,
                    mid=mid,
                    spread_pct=spread_pct,
                    instrument_name=name,
                ))

            return results

        except Exception as e:
            logger.error(f"Option search error: {e}")
            return []

    def get_option_price(self, epic: str) -> Optional[OptionMarket]:
        """Get current price for a specific option EPIC."""
        if not self.session or epic in self._blocked_epics:
            return None

        try:
            info = self.get_market_info(epic)
            if not info:
                return None

            snap = info.get("snapshot", {})
            inst = info.get("instrument", {})
            bid = float(snap.get("bid", 0) or 0)
            offer = float(snap.get("offer", 0) or 0)
            mid = (bid + offer) / 2 if bid and offer else 0
            spread_pct = ((offer - bid) / mid * 100) if mid > 0 else 999

            name = inst.get("name", "")
            opt_type = "PUT" if "put" in name.lower() else "CALL"

            # Parse strike from name
            strike = 0.0
            parts = name.split()
            for part in parts:
                try:
                    val = float(part.replace(",", ""))
                    if val > 100:
                        strike = val
                except ValueError:
                    continue

            return OptionMarket(
                epic=epic,
                strike=strike,
                option_type=opt_type,
                expiry=inst.get("expiry", ""),
                bid=bid,
                offer=offer,
                mid=mid,
                spread_pct=spread_pct,
                instrument_name=name,
            )

        except Exception as e:
            logger.error(f"Option price error for {epic}: {e}")
            return None

    def validate_option_leg(self, epic: str, size: float) -> dict:
        """Validate option leg tradability and minimum deal size."""
        info = self.get_market_info(epic)
        if not info:
            return {"ok": False, "code": "NO_MARKET_INFO", "message": f"No market info for {epic}"}

        snapshot = info.get("snapshot", {})
        status = str(snapshot.get("marketStatus", "UNKNOWN")).upper()
        allowed = {"TRADEABLE", "OPEN"}
        if status not in allowed:
            return {
                "ok": False,
                "code": "MARKET_NOT_TRADEABLE",
                "message": f"{epic} marketStatus={status}",
            }

        rules = info.get("dealingRules", {})
        min_size = 0.0
        min_size_obj = rules.get("minDealSize", {})
        if isinstance(min_size_obj, dict):
            min_size = float(min_size_obj.get("value", 0) or 0)
        elif min_size_obj:
            min_size = float(min_size_obj)

        if min_size > 0 and float(size) < min_size:
            return {
                "ok": False,
                "code": "SIZE_BELOW_MIN",
                "message": f"{epic} size={size} < minDealSize={min_size}",
                "min_size": min_size,
            }

        return {
            "ok": True,
            "code": "OK",
            "message": f"{epic} tradeable",
            "market_status": status,
            "min_size": min_size,
        }

    def place_option_spread(
        self,
        short_epic: str,
        long_epic: str,
        size: float,
        ticker: str,
        strategy: str,
        correlation_id: str = "",
    ) -> SpreadOrderResult:
        """
        Place a credit spread: SELL short_epic (collect premium), BUY long_epic (hedge).
        Both legs placed sequentially. If the first leg fills but the second fails,
        we immediately close the first leg to avoid naked exposure.
        """
        if not self.session:
            return SpreadOrderResult(success=False, message="Not connected")

        logger.info(f"Placing credit spread: SELL {short_epic}, BUY {long_epic} @ £{size}/pt [{ticker}:{strategy}]")

        # ─── Leg 1: Sell the short option (collect premium) ───────────────
        short_ref = f"{correlation_id}-S" if correlation_id else ""
        short_result = self._place_option_leg(
            short_epic, "SELL", size, ticker, strategy, deal_reference=short_ref
        )
        if not short_result.success:
            return SpreadOrderResult(
                success=False,
                message=f"Short leg failed: {short_result.message}",
            )

        time.sleep(0.5)  # Brief pause between legs

        # ─── Leg 2: Buy the long option (hedge) ──────────────────────────
        long_ref = f"{correlation_id}-L" if correlation_id else ""
        long_result = self._place_option_leg(
            long_epic, "BUY", size, ticker, strategy, deal_reference=long_ref
        )
        if not long_result.success:
            # CRITICAL: Close the short leg to avoid naked exposure
            logger.error(f"Long leg failed — closing short leg {short_result.order_id} to avoid naked risk")
            rollback_ref = f"{correlation_id}-RB" if correlation_id else ""
            self._close_option_leg(short_result.order_id, "BUY", size, deal_reference=rollback_ref)
            return SpreadOrderResult(
                success=False,
                message=f"Long leg failed ({long_result.message}), short leg reversed",
            )

        net_premium = short_result.fill_price - long_result.fill_price

        logger.info(
            f"Credit spread filled: short={short_result.order_id} @ {short_result.fill_price}, "
            f"long={long_result.order_id} @ {long_result.fill_price}, "
            f"net premium={net_premium:.2f}"
        )

        return SpreadOrderResult(
            success=True,
            short_deal_id=short_result.order_id,
            long_deal_id=long_result.order_id,
            short_fill_price=short_result.fill_price,
            long_fill_price=long_result.fill_price,
            net_premium=net_premium,
            size=size,
            timestamp=datetime.now(),
        )

    def close_option_spread(
        self,
        short_deal_id: str,
        long_deal_id: str,
        size: float,
        correlation_id: str = "",
    ) -> SpreadOrderResult:
        """Close both legs of an option spread (buy back short, sell long)."""
        if not self.session:
            return SpreadOrderResult(success=False, message="Not connected")

        logger.info(f"Closing spread: buy back {short_deal_id}, sell {long_deal_id}")

        # Close short leg (buy it back)
        short_ref = f"{correlation_id}-CS" if correlation_id else ""
        short_close = self._close_option_leg(short_deal_id, "BUY", size, deal_reference=short_ref)
        if not short_close.success:
            logger.error(f"Failed to close short leg: {short_close.message}")
            return SpreadOrderResult(success=False, message=f"Short close failed: {short_close.message}")

        time.sleep(0.5)

        # Close long leg (sell it)
        long_ref = f"{correlation_id}-CL" if correlation_id else ""
        long_close = self._close_option_leg(long_deal_id, "SELL", size, deal_reference=long_ref)
        if not long_close.success:
            logger.error(f"Failed to close long leg: {long_close.message}")
            # Short leg is already closed — log the partial state
            return SpreadOrderResult(
                success=False,
                short_deal_id=short_deal_id,
                message=f"Short closed but long close failed: {long_close.message}",
            )

        net_cost = short_close.fill_price - long_close.fill_price
        logger.info(f"Spread closed: net cost={net_cost:.2f}")

        return SpreadOrderResult(
            success=True,
            short_deal_id=short_deal_id,
            long_deal_id=long_deal_id,
            short_fill_price=short_close.fill_price,
            long_fill_price=long_close.fill_price,
            net_premium=net_cost,
            size=size,
            timestamp=datetime.now(),
        )

    def _place_option_leg(self, epic: str, direction: str, size: float,
                          ticker: str, strategy: str, deal_reference: str = "") -> OrderResult:
        """Place a single option leg order."""
        if epic in self._blocked_epics:
            return OrderResult(success=False, message=f"EPIC {epic} is blocked")

        try:
            # Get market info for expiry
            expiry = "DFB"
            mkt_info = self.get_market_info(epic)
            if mkt_info:
                inst = mkt_info.get("instrument", {})
                mkt_expiry = inst.get("expiry", "")
                if mkt_expiry and mkt_expiry != "-":
                    expiry = mkt_expiry

            order = {
                "epic": epic,
                "expiry": expiry,
                "direction": direction,
                "size": str(size),
                "orderType": "MARKET",
                "currencyCode": "GBP",
                "forceOpen": True,
                "guaranteedStop": False,
                "stopDistance": None,
                "limitDistance": None,
            }
            if deal_reference:
                order["dealReference"] = deal_reference

            r = self.session.post(
                f"{self.base_url}/positions/otc",
                json=order,
                headers=self._headers("2"),
            )

            if r.status_code == 403:
                self._blocked_epics.add(epic)
                return OrderResult(success=False, message=f"403: no access for {epic}")
            elif r.status_code != 200:
                return OrderResult(success=False, message=f"HTTP {r.status_code}: {r.text[:100]}")

            deal_ref = r.json().get("dealReference", "")
            if not deal_ref:
                return OrderResult(success=False, message="No deal reference")

            time.sleep(1)
            return self._confirm_deal(deal_ref, ticker, strategy, size)

        except Exception as e:
            logger.error(f"Option leg order error: {e}")
            return OrderResult(success=False, message=str(e))

    def _close_option_leg(self, deal_id: str, direction: str, size: float,
                          deal_reference: str = "") -> OrderResult:
        """Close a single option leg by deal ID."""
        try:
            close_payload = {
                "dealId": deal_id,
                "direction": direction,
                "size": str(size),
                "orderType": "MARKET",
            }
            if deal_reference:
                close_payload["dealReference"] = deal_reference

            r = self.session.post(
                f"{self.base_url}/positions/otc",
                json=close_payload,
                headers={**self._headers("1"), "_method": "DELETE"},
            )

            if r.status_code != 200:
                return OrderResult(success=False, message=f"Close HTTP {r.status_code}")

            close_ref = r.json().get("dealReference", "")
            if not close_ref:
                return OrderResult(success=False, message="No close deal reference")

            time.sleep(1)
            result = self._confirm_deal(close_ref, "", "", size)

            if result.success:
                self._deal_map.pop(deal_id, None)

            return result

        except Exception as e:
            logger.error(f"Close option leg error: {e}")
            return OrderResult(success=False, message=str(e))

    # ─── Close position ──────────────────────────────────────────────────

    def close_position(self, ticker: str, strategy: str) -> OrderResult:
        """Close an open position using _method DELETE header (proven working)."""
        if not self.session:
            return OrderResult(success=False, message="Not connected")

        # Find the deal ID
        deal_id = None
        close_direction = "SELL"
        close_size = config.PORTFOLIO["default_stake_per_point"]

        # First check our internal map
        for did, (t, s) in self._deal_map.items():
            if t == ticker and s == strategy:
                deal_id = did
                break

        # If not in map, search IG's open positions
        if not deal_id:
            epic = self.get_epic(ticker)
            try:
                r = self.session.get(f"{self.base_url}/positions", headers=self._headers("2"))
                if r.status_code == 200:
                    for p in r.json().get("positions", []):
                        mkt = p.get("market", {})
                        pos = p.get("position", {})
                        if mkt.get("epic") == epic:
                            deal_id = pos.get("dealId", "")
                            close_direction = "SELL" if pos.get("direction") == "BUY" else "BUY"
                            close_size = float(pos.get("size", close_size))
                            break
            except Exception:
                pass

        if not deal_id:
            return OrderResult(success=False, message=f"No deal found for {ticker}:{strategy}")

        # Get direction and size from our tracking if available
        pos = self.get_position(ticker, strategy)
        if pos:
            close_direction = "SELL" if pos.direction == "long" else "BUY"
            close_size = pos.size

        try:
            logger.info(f"Closing position: {ticker} deal={deal_id} [{strategy}]")

            # Use _method DELETE header — this is how IG's close endpoint works
            close_payload = {
                "dealId": deal_id,
                "direction": close_direction,
                "size": str(close_size),
                "orderType": "MARKET",
            }

            r = self.session.post(
                f"{self.base_url}/positions/otc",
                json=close_payload,
                headers={**self._headers("1"), "_method": "DELETE"},
            )

            if r.status_code != 200:
                logger.error(f"Close HTTP error: {r.status_code} — {r.text[:200]}")
                return OrderResult(success=False, message=f"Close HTTP {r.status_code}")

            close_ref = r.json().get("dealReference", "")
            if not close_ref:
                return OrderResult(success=False, message="No close deal reference")

            # Confirm close
            time.sleep(1)
            result = self._confirm_deal(close_ref, ticker, strategy, close_size)

            if result.success:
                self._deal_map.pop(deal_id, None)
                logger.info(f"Position closed: {deal_id}")

            return result

        except Exception as e:
            logger.error(f"Close error: {e}")
            return OrderResult(success=False, message=str(e))

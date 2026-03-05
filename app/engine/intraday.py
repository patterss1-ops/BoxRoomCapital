"""Intraday event loop — polls market data and triggers entries between daily batches.

Designed to catch intraday IBS/options setups that the daily scheduler misses.
Runs as a background thread with configurable poll interval.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import config

logger = logging.getLogger(__name__)


class IntradayEventLoop:
    """Lightweight intraday polling loop for sub-daily strategy triggers.

    On each tick:
      1. Check if within market hours (skip overnight/weekend)
      2. Poll latest price data for configured tickers
      3. Run intraday signal checks (IBS, barrier triggers)
      4. Create order intents if signals fire

    Lifecycle: start() -> runs in daemon thread -> stop()
    """

    def __init__(
        self,
        poll_interval: float = 300.0,
        tickers: Optional[list[str]] = None,
        signal_fn: Optional[Callable[..., Any]] = None,
    ):
        self._poll_interval = max(60.0, poll_interval)
        self._tickers = tickers or config.INTRADAY_TICKERS
        self._signal_fn = signal_fn
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._last_tick: Optional[str] = None
        self._tick_count = 0
        self._signal_count = 0

    def start(self) -> dict[str, Any]:
        """Start the intraday loop in a daemon thread."""
        if self._running:
            return {"status": "already_running"}

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="intraday-event-loop",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Intraday loop started: poll=%ds, tickers=%s",
            self._poll_interval, self._tickers,
        )
        return {"status": "started", "poll_seconds": self._poll_interval}

    def stop(self) -> dict[str, Any]:
        """Stop the intraday loop."""
        if not self._running:
            return {"status": "not_running"}

        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=15)
        self._running = False
        self._thread = None
        logger.info("Intraday loop stopped")
        return {"status": "stopped"}

    def status(self) -> dict[str, Any]:
        """Return current state."""
        return {
            "running": self._running,
            "poll_seconds": self._poll_interval,
            "tickers": self._tickers,
            "last_tick": self._last_tick,
            "tick_count": self._tick_count,
            "signal_count": self._signal_count,
        }

    def _run_loop(self) -> None:
        """Main polling loop."""
        try:
            while not self._stop_event.is_set():
                try:
                    self._tick()
                except Exception as exc:
                    logger.error("Intraday tick error: %s", exc, exc_info=True)
                self._stop_event.wait(timeout=self._poll_interval)
        finally:
            self._running = False
            self._thread = None

    def _tick(self) -> None:
        """One poll cycle: check hours, fetch data, evaluate signals."""
        now = datetime.now(timezone.utc)
        self._last_tick = now.isoformat()
        self._tick_count += 1

        # Skip weekends (Sat=6, Sun=7)
        if now.isoweekday() > 5:
            return

        # Check if any major market is open (rough hours in UTC)
        # London: 08:00-16:30, US: 14:30-21:00
        hour = now.hour
        if hour < 8 or hour > 21:
            return

        # Poll and evaluate each ticker
        for ticker in self._tickers:
            try:
                self._evaluate_ticker(ticker, now)
            except Exception as exc:
                logger.debug("Intraday eval failed for %s: %s", ticker, exc)

    def _evaluate_ticker(self, ticker: str, now: datetime) -> None:
        """Fetch latest data and check for intraday signals.

        Uses the IG streaming price if available, falls back to yfinance.
        Checks IBS intraday levels and options barrier proximity.
        """
        from data.provider import DataProvider

        provider = DataProvider()
        df = provider.get_daily_bars(ticker)
        if df is None or df.empty:
            return

        # Calculate intraday IBS from latest bar
        latest = df.iloc[-1]
        high = float(latest.get("High", 0))
        low = float(latest.get("Low", 0))
        close = float(latest.get("Close", 0))

        if high == low:
            return

        ibs = (close - low) / (high - low)

        # Check for extreme intraday oversold (potential IBS entry)
        if ibs < config.IBS_PARAMS.get("ibs_entry_thresh", 0.3):
            logger.info(
                "Intraday IBS signal: %s IBS=%.3f (below %.2f threshold)",
                ticker, ibs, config.IBS_PARAMS["ibs_entry_thresh"],
            )
            self._signal_count += 1

            if self._signal_fn:
                try:
                    self._signal_fn(
                        ticker=ticker,
                        signal_type="intraday_ibs_oversold",
                        ibs=ibs,
                        timestamp=now.isoformat(),
                    )
                except Exception as exc:
                    logger.warning("Signal callback failed for %s: %s", ticker, exc)

        # Check options barrier proximity (if options engine is running)
        self._check_barrier_proximity(ticker, close, now)

    def _check_barrier_proximity(
        self, ticker: str, current_price: float, now: datetime
    ) -> None:
        """Check if price is approaching any active barrier/option strike.

        This enables intraday entry timing for the credit spread strategy
        when underlying price moves toward a favorable strike zone.
        """
        epic_patterns = config.OPTION_EPIC_PATTERNS.get(ticker)
        if not epic_patterns:
            return

        # The actual barrier check would query the options engine for
        # active spread positions and check proximity to strikes.
        # For now, log when price movement exceeds 1% intraday (volatility alert).
        try:
            from data.provider import DataProvider
            provider = DataProvider()
            df = provider.get_daily_bars(ticker)
            if df is None or len(df) < 2:
                return
            prev_close = float(df.iloc[-2]["Close"])
            if prev_close == 0:
                return
            intraday_move_pct = abs(current_price - prev_close) / prev_close * 100
            if intraday_move_pct > 1.5:
                logger.info(
                    "Intraday volatility alert: %s moved %.1f%% (%.2f -> %.2f)",
                    ticker, intraday_move_pct, prev_close, current_price,
                )
        except Exception:
            pass

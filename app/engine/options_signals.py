"""Signal generation and handling for OptionsBot."""
from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import config
from strategies.ibs_credit_spreads import (
    generate_signal, CreditSpreadSignal,
)
from data.trade_db import log_event

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class OptionsSignalsMixin:

    def _run_all_signals(self):
        from app.engine.options_bot import EU_TICKERS, US_TICKERS
        tickers = [t for t in config.LIVE_TRADING_TICKERS
                   if t in EU_TICKERS + US_TICKERS]
        if not tickers:
            tickers = config.LIVE_TRADING_TICKERS
        self._run_signals(tickers)

    def _run_signals(self, tickers: list[str]):
        vix_df = self.data.get_vix()
        vix = float(vix_df["Close"].iloc[-1]) if not vix_df.empty else 20.0

        scan_results = []

        for ticker in tickers:
            if ticker not in config.LIVE_TRADING_TICKERS:
                continue

            try:
                df = self.data.get_daily_bars(ticker)
                if df.empty or len(df) < 210:
                    logger.warning(f"  {ticker}: insufficient data")
                    scan_results.append(f"{ticker}: insufficient data")
                    continue

                bar = {
                    "open": float(df["Open"].iloc[-1]),
                    "high": float(df["High"].iloc[-1]),
                    "low": float(df["Low"].iloc[-1]),
                    "close": float(df["Close"].iloc[-1]),
                    "date": str(df.index[-1].date()),
                }
                prev_bars = [
                    {"close": float(df["Close"].iloc[i])}
                    for i in range(-20, -1)
                ]

                ema200 = float(df["Close"].ewm(span=200, adjust=False).mean().iloc[-1])

                existing = self._get_open_spread(ticker)

                sig = generate_signal(
                    bar=bar,
                    prev_bars=prev_bars,
                    position=existing,
                    params=self.strategy_params,
                    vix=vix,
                    ema200=ema200,
                )

                # Compute IBS for logging
                bar_range = bar["high"] - bar["low"]
                ibs = (bar["close"] - bar["low"]) / bar_range if bar_range > 0 else 0.5

                scan_results.append(
                    f"{ticker}: {sig.action} (IBS={ibs:.2f}, close={bar['close']:.1f}, "
                    f"EMA200={ema200:.1f}, VIX={vix:.1f}) — {sig.reason}"
                )

                self._handle_signal(ticker, sig, bar["close"], vix)

            except Exception as e:
                logger.error(f"  {ticker} error: {e}", exc_info=True)
                scan_results.append(f"{ticker}: ERROR — {e}")
                log_event("ERROR", f"{ticker} — Signal error: {e}",
                          ticker=ticker, strategy="IBS Credit Spreads")

        # Always log scan summary as a visible event
        summary = "; ".join(scan_results) if scan_results else "No tickers scanned"
        log_event(
            "SCAN",
            f"Signal scan: {len(tickers)} tickers, VIX={vix:.1f}",
            detail=summary,
        )

    def _handle_signal(self, ticker: str, sig: CreditSpreadSignal,
                       current_price: float, vix: float):
        if sig.action == "skip":
            logger.debug(f"  {ticker}: {sig.reason}")
            return

        if sig.action == "hold":
            logger.debug(f"  {ticker}: {sig.reason}")
            return

        if sig.action == "close":
            spread = self._get_open_spread_dict(ticker)
            if not spread:
                logger.warning(f"  {ticker}: close signal but no open spread")
                return

            logger.info(f"  {ticker}: CLOSE — {sig.reason}")
            self._close_spread(spread, sig.reason)
            return

        if sig.action in ("open_put_spread", "open_call_spread"):
            dedup_key = f"{ticker}:{date.today()}"
            if dedup_key in self._today_signals:
                logger.debug(f"  {ticker}: already signalled today, skipping")
                return
            self._today_signals.add(dedup_key)

            logger.info(f"  {ticker}: {sig.action.upper()} — {sig.reason}")
            logger.info(f"    Short: {sig.short_strike}, Long: {sig.long_strike}")

            self._enter_spread(ticker, sig, current_price)
            return

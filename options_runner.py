"""
Options Credit Spread Auto-Trading Bot

Runs the IBS Credit Spread strategy on IG Markets with real money.
Supports shadow mode (log signals, don't trade) and live mode.

Usage:
    python3 options_runner.py                    # shadow mode (default)
    python3 options_runner.py --mode live        # real money
    python3 options_runner.py --mode shadow      # explicit shadow

The bot:
  1. Monitors IBS signals on configured markets (EU close + US close)
  2. When signal fires: finds option EPICs on IG, calculates spread strikes
  3. Sizes the trade via safety controller
  4. In shadow mode: logs what it would do + sends Telegram alert
  5. In live mode: places 2-leg order on IG + sends Telegram alert
  6. Monitors open spreads for expiry / early exit
  7. Heartbeat every 2 hours so you know it's alive
"""
import argparse
import logging
import time

import config
from utils.logger import setup_logging

from app.engine.options_bot import OptionsBot

from app.engine.options_bot import (
    UK,
    SCHEDULE,
    EU_TICKERS,
    US_TICKERS,
    POSITION_CHECK_INTERVAL,
    HEARTBEAT_INTERVAL,
    ORDER_ACTION_MAX_ATTEMPTS,
)

from data.trade_db import (
    log_event, log_trade, log_shadow_trade,
    upsert_option_position, close_option_position,
    get_open_option_positions, get_order_actions_by_statuses,
    create_order_action, update_order_action,
    load_strategy_state, save_strategy_state, log_control_action,
    get_active_strategy_parameter_set,
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Options Credit Spread Auto-Trader")
    parser.add_argument("--mode", choices=["shadow", "live"],
                        default=config.TRADING_MODE,
                        help=f"Trading mode (default: {config.TRADING_MODE})")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one signal scan and exit (for API/manual trigger use).",
    )
    args = parser.parse_args()

    setup_logging()

    if args.mode == "live":
        logger.warning("=" * 60)
        logger.warning("  LIVE MODE — REAL MONEY WILL BE TRADED")
        logger.warning("  Press Ctrl+C within 5 seconds to abort")
        logger.warning("=" * 60)
        time.sleep(5)

    bot = OptionsBot(mode=args.mode)
    bot.start(once=args.once)


if __name__ == "__main__":
    main()

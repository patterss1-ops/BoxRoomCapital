"""
Logging setup for the trading bot.
"""
import logging
import sys
from datetime import datetime

import config


def setup_logging():
    """Configure logging to both file and console."""
    root_logger = logging.getLogger()
    if getattr(root_logger, "_trading_bot_logging_configured", False):
        return

    root_logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console.setFormatter(console_fmt)
    root_logger.addHandler(console)

    # File handler
    file_handler = logging.FileHandler(config.LOG_FILE)
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    root_logger.addHandler(file_handler)
    root_logger._trading_bot_logging_configured = True

    logging.info(f"Logging initialised. Level={config.LOG_LEVEL}, File={config.LOG_FILE}")

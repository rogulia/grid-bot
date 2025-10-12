"""Logging configuration for the trading bot"""

import logging
import os
from datetime import datetime
from pathlib import Path
from src.utils.timezone import now_helsinki, format_helsinki


class HelsinkiFormatter(logging.Formatter):
    """Custom formatter that uses Helsinki timezone for all timestamps"""

    def formatTime(self, record, datefmt=None):
        """Override formatTime to use Helsinki timezone"""
        if datefmt:
            return format_helsinki(fmt=datefmt)
        else:
            # Default format with time
            return format_helsinki(fmt='%Y-%m-%d %H:%M:%S')


def setup_logger(name: str = "sol-trader", log_level: str = "INFO", log_prefix: str = "") -> logging.Logger:
    """
    Setup and configure logger with file and console handlers

    Args:
        name: Logger name
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_prefix: Prefix for log files (e.g., "test_" for backtesting)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    # Create logs directory
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    # Date for log files (Helsinki timezone)
    today = format_helsinki(fmt="%Y-%m-%d")

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = HelsinkiFormatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)

    # File handler - main log (with prefix for backtesting)
    log_filename = f"{log_prefix}bot_{today}.log"
    file_handler = logging.FileHandler(
        logs_dir / log_filename,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = HelsinkiFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)

    # Error handler - separate file for errors (with prefix and date)
    error_filename = f"{log_prefix}errors_{today}.log"
    error_handler = logging.FileHandler(
        logs_dir / error_filename,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)

    return logger


def log_trade(logger: logging.Logger, side: str, price: float, qty: float,
              reason: str, dry_run: bool = True, account_prefix: str = ""):
    """
    Log a trade execution

    Args:
        logger: Logger instance
        side: Buy or Sell
        price: Execution price
        qty: Quantity
        reason: Trade reason
        dry_run: If True, marks as DRY RUN
        account_prefix: Account ID prefix (e.g., "001_")
    """
    mode = "[DRY RUN]" if dry_run else "[LIVE]"
    logger.info(f"{mode} TRADE: {side} {qty} @ ${price:.4f} - Reason: {reason}")

    # Also write to dedicated trades log (Helsinki timezone, with account prefix)
    today = format_helsinki(fmt="%Y-%m-%d")
    log_file = f"logs/{account_prefix}trades_{today}.log"
    with open(log_file, "a", encoding='utf-8') as f:
        timestamp = format_helsinki()
        f.write(f"{timestamp} | {mode} | {side} | {qty} | ${price:.4f} | {reason}\n")


def log_position_state(logger: logging.Logger, long_positions: list, short_positions: list,
                       long_pnl: float, short_pnl: float, current_price: float, account_prefix: str = ""):
    """
    Log current position state

    Args:
        logger: Logger instance
        long_positions: List of long positions
        short_positions: List of short positions
        long_pnl: Long side PnL
        short_pnl: Short side PnL
        current_price: Current market price
        account_prefix: Account ID prefix (e.g., "001_")
    """
    logger.info(
        f"Position State - Price: ${current_price:.4f} | "
        f"LONG: {len(long_positions)} positions (PnL: ${long_pnl:.2f}) | "
        f"SHORT: {len(short_positions)} positions (PnL: ${short_pnl:.2f}) | "
        f"Total PnL: ${long_pnl + short_pnl:.2f}"
    )

    # Write to dedicated position log (Helsinki timezone, with account prefix)
    today = format_helsinki(fmt="%Y-%m-%d")
    log_file = f"logs/{account_prefix}positions_{today}.log"
    with open(log_file, "a", encoding='utf-8') as f:
        timestamp = format_helsinki()
        f.write(
            f"{timestamp} | Price: ${current_price:.4f} | "
            f"LONG: {len(long_positions)} (${long_pnl:.2f}) | "
            f"SHORT: {len(short_positions)} (${short_pnl:.2f}) | "
            f"Total: ${long_pnl + short_pnl:.2f}\n"
        )

"""
Logging utility for the Finance App.

Provides file-based logging with automatic rotation to prevent unbounded growth.
Logs are written to logs/app.log with automatic rotation at 1MB.

Usage:
    from logger import log
    log.info("Something happened")
    log.error("Something went wrong", exc_info=True)
    log.debug("Debug details")
"""

import logging
from logging.handlers import RotatingFileHandler
import os

# Create logs directory if it doesn't exist
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, 'app.log')
MAX_BYTES = 1 * 1024 * 1024  # 1MB per file
BACKUP_COUNT = 3  # Keep 3 backup files (app.log.1, app.log.2, app.log.3)

# Create logger
log = logging.getLogger('financeapp')
log.setLevel(logging.DEBUG)

# Prevent duplicate handlers if module is reloaded
if not log.handlers:
    # File handler with rotation
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT
    )
    file_handler.setLevel(logging.DEBUG)

    # Console handler for errors only
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)

    # Format: timestamp - level - message
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    log.addHandler(file_handler)
    log.addHandler(console_handler)


def log_request(endpoint, params=None, status=None, duration=None):
    """Log an API request with optional details."""
    msg = f"API {endpoint}"
    if params:
        msg += f" params={params}"
    if status:
        msg += f" status={status}"
    if duration:
        msg += f" duration={duration:.2f}s"
    log.info(msg)


def log_error(message, exception=None):
    """Log an error with optional exception details."""
    if exception:
        log.error(f"{message}: {exception}", exc_info=True)
    else:
        log.error(message)


def log_yahoo_fetch(tickers, success_count, fail_count, duration):
    """Log Yahoo Finance fetch results."""
    log.info(f"Yahoo fetch: {len(tickers)} tickers, {success_count} success, {fail_count} failed, {duration:.2f}s")


def log_screener_progress(current, total, ticker, status):
    """Log screener progress."""
    pct = (current / total * 100) if total > 0 else 0
    log.debug(f"Screener: {current}/{total} ({pct:.1f}%) - {ticker} - {status}")


def log_sec_update(ticker, status, details=None):
    """Log SEC data update."""
    msg = f"SEC update {ticker}: {status}"
    if details:
        msg += f" - {details}"
    log.info(msg)


def tail_log(lines=50):
    """Return the last N lines of the log file."""
    if not os.path.exists(LOG_FILE):
        return "No log file exists yet"

    with open(LOG_FILE, 'r') as f:
        all_lines = f.readlines()
        return ''.join(all_lines[-lines:])


def clear_log():
    """Clear the log file."""
    if os.path.exists(LOG_FILE):
        open(LOG_FILE, 'w').close()
        log.info("Log file cleared")

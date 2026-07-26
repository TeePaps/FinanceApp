"""
Shared utility functions for the Finance App.
"""

import math
from datetime import datetime, timedelta


def _market_bounds(now=None):
    """Return (now, market_open, market_close) as tz-aware datetimes for today."""
    import pytz
    import config

    tz = pytz.timezone(config.MARKET_TIMEZONE)
    now = now or datetime.now(tz)

    start_h, start_m = map(int, config.MARKET_HOURS_START.split(':'))
    end_h, end_m = map(int, config.MARKET_HOURS_END.split(':'))

    open_at = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    close_at = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    return now, open_at, close_at


def is_market_open(now=None) -> bool:
    """Check if the US stock market is currently open (weekday + session hours)."""
    now, open_at, close_at = _market_bounds(now)
    if now.weekday() >= 5:
        return False
    return open_at <= now <= close_at


def last_market_close(now=None) -> datetime:
    """Naive local datetime of the most recent market close.

    Used to decide whether a cached price can still be considered current:
    once the session has ended, prices cannot change again until the next
    open, so anything fetched after the last close is still the latest price
    no matter how many TTLs have elapsed since.
    """
    now, _open_at, close_at = _market_bounds(now)

    # Walk back to the most recent weekday whose close has already passed.
    candidate = close_at
    if now < close_at:
        candidate = close_at - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate = candidate - timedelta(days=1)

    # DB timestamps are naive local wall-clock (datetime.now()), so convert the
    # market-timezone close into local time before dropping the tzinfo.
    return candidate.astimezone().replace(tzinfo=None)


def sanitize_for_json(obj):
    """Replace NaN and Inf values with None for JSON compatibility."""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj

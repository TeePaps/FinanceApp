"""Background scheduler for automatic data refresh.

Provides automatic price refresh during US market hours.
Can be enabled/disabled via config.yaml settings.
"""

import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
import pytz

import config
from services import screener as screener_service
from services.activity_log import activity_log

_scheduler = None


def is_market_open():
    """Check if US stock market is currently open."""
    tz = pytz.timezone(config.MARKET_TIMEZONE)
    now = datetime.now(tz)

    # Weekend check
    if now.weekday() >= 5:
        return False

    # Parse market hours
    start_h, start_m = map(int, config.MARKET_HOURS_START.split(':'))
    end_h, end_m = map(int, config.MARKET_HOURS_END.split(':'))

    market_open = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    market_close = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

    return market_open <= now <= market_close


def auto_refresh_prices():
    """Automatic price refresh job - runs only during market hours."""
    if not is_market_open():
        # Don't log when market is closed - too noisy
        return

    if screener_service.is_running():
        activity_log.log('warning', 'scheduler', '[AUTO-REFRESH SKIPPED] Manual update already in progress')
        return

    activity_log.log('info', 'scheduler', '[AUTO-REFRESH] Starting automatic price refresh...')

    # Run in background thread (same pattern as manual refresh)
    thread = threading.Thread(target=screener_service.run_quick_price_update, args=('all',))
    thread.daemon = True
    thread.start()


def init_scheduler(app=None):
    """Initialize the background scheduler."""
    global _scheduler

    if not config.SCHEDULER_ENABLED:
        activity_log.log('info', 'scheduler', 'Background scheduler disabled in config')
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        auto_refresh_prices,
        'interval',
        minutes=config.PRICE_REFRESH_INTERVAL,
        id='auto_price_refresh',
        replace_existing=True
    )
    _scheduler.start()

    activity_log.log('info', 'scheduler',
        f'Background scheduler started - refresh every {config.PRICE_REFRESH_INTERVAL}m during market hours')


def get_status():
    """Get scheduler status for API."""
    global _scheduler

    if not _scheduler:
        return {
            'enabled': False,
            'running': False,
            'next_run': None,
            'interval_minutes': config.PRICE_REFRESH_INTERVAL,
            'market_open': is_market_open()
        }

    job = _scheduler.get_job('auto_price_refresh')
    next_run = job.next_run_time if job else None

    return {
        'enabled': config.SCHEDULER_ENABLED,
        'running': _scheduler.running,
        'next_run': next_run.isoformat() if next_run else None,
        'interval_minutes': config.PRICE_REFRESH_INTERVAL,
        'market_open': is_market_open()
    }


def toggle(enabled=None):
    """Enable/disable the scheduler."""
    global _scheduler

    if _scheduler is None:
        return {'error': 'Scheduler not initialized'}

    if enabled is None:
        # Toggle current state
        if _scheduler.running:
            _scheduler.pause()
        else:
            _scheduler.resume()
    elif enabled:
        _scheduler.resume()
    else:
        _scheduler.pause()

    return get_status()


def shutdown():
    """Shutdown the scheduler gracefully."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None

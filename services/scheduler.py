"""Background scheduler for automatic data refresh.

Provides automatic price refresh during US market hours.
Can be enabled/disabled via config.yaml settings.
"""

import os
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_RUNNING, STATE_PAUSED

import config
from services import screener as screener_service
from services.activity_log import activity_log

_scheduler = None

# Held open for the process lifetime so the OS releases it automatically on
# exit. A second process that fails to take it must not schedule anything.
_lock_handle = None


def _acquire_singleton_lock():
    """Take an exclusive cross-process lock so only one scheduler ever runs.

    Two app processes (a stray server, or a reloader parent/child pair) would
    otherwise each fire the refresh job, doubling external API calls and
    writing public.db concurrently. Returns True if this process owns the lock.
    """
    global _lock_handle

    try:
        import fcntl
    except ImportError:
        # Non-POSIX platform: fall back to letting the scheduler start.
        return True

    lock_path = os.path.join(config.USER_DATA_DIR, 'scheduler.lock')
    try:
        os.makedirs(config.USER_DATA_DIR, exist_ok=True)
        handle = open(lock_path, 'w')
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        return False

    handle.write(str(os.getpid()))
    handle.flush()
    _lock_handle = handle
    return True


def is_market_open():
    """Check if US stock market is currently open.

    Delegates to services.utils so the scheduler and the price cache agree on
    what "market hours" means - the cache uses the same window to decide that
    an after-hours price cannot have changed.
    """
    from services.utils import is_market_open as _is_open
    return _is_open()


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

    if not _acquire_singleton_lock():
        activity_log.log('warning', 'scheduler',
            'Another process already owns the scheduler - not starting a second one')
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

    # APScheduler's `.running` is `state != STATE_STOPPED`, so it stays True
    # while PAUSED — report the true actively-running state instead, and hide
    # a stale next_run when paused.
    is_active = _scheduler.state == STATE_RUNNING
    return {
        'enabled': config.SCHEDULER_ENABLED,
        'running': is_active,
        'next_run': next_run.isoformat() if (next_run and is_active) else None,
        'interval_minutes': config.PRICE_REFRESH_INTERVAL,
        'market_open': is_market_open()
    }


def toggle(enabled=None):
    """Enable/disable the scheduler."""
    global _scheduler

    if _scheduler is None:
        return {'error': 'Scheduler not initialized'}

    if enabled is None:
        # Toggle current state. Check state explicitly — `.running` stays True
        # while paused (state != STATE_STOPPED), so the old check could never
        # resume a paused scheduler.
        if _scheduler.state == STATE_PAUSED:
            _scheduler.resume()
        else:
            _scheduler.pause()
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

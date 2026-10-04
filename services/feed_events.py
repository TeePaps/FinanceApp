"""
Durable feed events.

The activity log is a 100-entry in-memory ring buffer. This module keeps the
part of it that external monitoring cares about - per-call provider outcomes
and circuit-breaker transitions - in the public database's `feed_events` table,
and answers "how are the providers doing?" from it (see routes/feeds.py).

Events are buffered and written in batches by a daemon thread: a screener run
makes thousands of provider calls and the app opens one SQLite connection per
DB call, so one transaction per event is not an option. Telemetry must never
break a fetch, so database errors are swallowed after a single warning.

Producers:
- activity_log entries, via the sink init() registers (persist=False and debug
  entries are skipped by the activity log itself);
- record(), for outcomes that have no live-feed line of their own;
- record_check(), which coalesces fetch_checks upserts per (ticker, kind).

Usage:
    from services import feed_events

    feed_events.init()                       # once, at app startup
    feed_events.record('success', 'sec_edgar', 'AAPL EPS', 'AAPL',
                       kind='eps', outcome=feed_events.OUTCOME_SUCCESS, duration_ms=840)
    outcome = feed_events.classify_error(exc)
"""

import atexit
import math
import re
import threading
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union

from services.activity_log import activity_log

# Outcome vocabulary: the result of one provider call.
OUTCOME_SUCCESS = 'success'
OUTCOME_FAILURE = 'failure'
OUTCOME_TIMEOUT = 'timeout'
OUTCOME_RATE_LIMITED = 'rate_limited'
OUTCOME_EMPTY = 'empty'        # the call worked but returned nothing usable
OUTCOME_NO_DATA = 'no_data'    # the provider definitively has nothing for this ticker
OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_FAILURE, OUTCOME_TIMEOUT,
            OUTCOME_RATE_LIMITED, OUTCOME_EMPTY, OUTCOME_NO_DATA)

# Outcomes that say nothing bad about the provider; everything else is a failure.
_HEALTHY_OUTCOMES = frozenset((OUTCOME_SUCCESS, OUTCOME_NO_DATA))

FLUSH_INTERVAL_SECONDS = 2.0
FLUSH_BATCH_SIZE = 500        # flush early once this many events are waiting
MAX_BUFFERED_EVENTS = 5000    # oldest events are dropped beyond this (DB down)
PRUNE_INTERVAL_SECONDS = 24 * 60 * 60

_RATE_LIMIT_PATTERN = re.compile(r'\b429\b|too many requests|rate.?limit', re.IGNORECASE)
_TIMEOUT_PATTERN = re.compile(r'time[d]?[ -]?out', re.IGNORECASE)

_lock = threading.Lock()          # guards the buffers below
_flush_lock = threading.Lock()    # serialises flushes (thread, atexit, tests)
_events: List[Dict] = []
_checks: Dict[Tuple[str, str], bool] = {}   # (ticker, kind) -> ok, newest wins
_wake = threading.Event()
_init_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_warned = False


def classify_error(error: Union[str, BaseException, None]) -> str:
    """Map an error message or exception to an outcome (never 'success')."""
    text = f"{type(error).__name__} {error}" if isinstance(error, BaseException) else str(error or '')
    if _RATE_LIMIT_PATTERN.search(text):    # also matches yfinance's YFRateLimitError
        return OUTCOME_RATE_LIMITED
    if _TIMEOUT_PATTERN.search(text):
        return OUTCOME_TIMEOUT
    return OUTCOME_FAILURE


def record(level: str, source: str, message: str, ticker: Optional[str] = None, *,
           kind: Optional[str] = None, outcome: Optional[str] = None,
           duration_ms: Optional[int] = None) -> None:
    """Queue an event for the durable log only (not the live activity feed)."""
    _enqueue({
        'ts': datetime.now().isoformat(),
        'level': level,
        'source': source,
        'ticker': ticker,
        'kind': kind,
        'outcome': outcome,
        'message': message,
        'duration_ms': duration_ms,
    })


def record_check(kind: str, ticker: str, ok: bool) -> None:
    """Queue a fetch_checks upsert; repeats for one (ticker, kind) coalesce."""
    with _lock:
        _checks[(ticker.upper(), kind)] = bool(ok)


def _enqueue(event: Dict) -> None:
    with _lock:
        _events.append(event)
        if len(_events) > MAX_BUFFERED_EVENTS:
            del _events[:len(_events) - MAX_BUFFERED_EVENTS]
        full = len(_events) >= FLUSH_BATCH_SIZE
    if full:
        _wake.set()


def flush() -> None:
    """Write everything buffered, in one transaction per table."""
    import database as db

    with _flush_lock:
        with _lock:
            events, checks = _events[:], dict(_checks)
            _events.clear()
            _checks.clear()
        if not events and not checks:
            return

        by_kind_ok: Dict[Tuple[str, bool], List[str]] = {}
        for (ticker, kind), ok in checks.items():
            by_kind_ok.setdefault((kind, ok), []).append(ticker)

        # A failed batch is dropped rather than re-queued: the buffer must stay
        # bounded while the database is unavailable.
        try:
            db.insert_feed_events(events)
            for (kind, ok), tickers in by_kind_ok.items():
                db.record_fetch_checks(kind, tickers, ok)
        except Exception as e:
            _warn(f"could not write {len(events)} events: {e}")


def prune() -> None:
    """Delete events older than the configured retention."""
    import database as db
    try:
        db.prune_feed_events(retention_days())
    except Exception as e:
        _warn(f"could not prune old events: {e}")


def retention_days() -> int:
    from config import FEED_EVENTS_RETENTION_DAYS
    return FEED_EVENTS_RETENTION_DAYS


def init() -> None:
    """Start the durable writer. Idempotent; call once at app startup."""
    global _thread
    import database as db

    with _init_lock:
        if _thread is not None:
            return
        try:
            db.init_public_database()    # the table must exist before the first flush
        except Exception as e:
            _warn(f"could not create the feed_events table: {e}")
        activity_log.add_sink(_enqueue)
        atexit.register(flush)
        _thread = threading.Thread(target=_run, name='feed-events-writer', daemon=True)
        _thread.start()


def _run() -> None:
    prune()
    next_prune = time.monotonic() + PRUNE_INTERVAL_SECONDS
    while True:
        _wake.wait(FLUSH_INTERVAL_SECONDS)
        _wake.clear()
        flush()
        if time.monotonic() >= next_prune:
            next_prune += PRUNE_INTERVAL_SECONDS
            prune()


def _warn(message: str) -> None:
    """Print the first telemetry failure; later ones stay quiet."""
    global _warned
    if not _warned:
        _warned = True
        print(f"[FeedEvents] {message} (further errors suppressed)")


# --- Stats ---

def stats(hours: float) -> Dict:
    """Per-source event, outcome and latency summary for the last `hours`."""
    import database as db

    now = datetime.now()
    since = (now - timedelta(hours=hours)).isoformat()

    sources: Dict[str, Dict] = {}

    def source_entry(name: str) -> Dict:
        return sources.setdefault(name, {
            'events': 0, 'by_level': {}, 'last_event': None,
            'last_success': None, 'last_failure': None,
        })

    for row in db.get_feed_event_counts(since):
        entry = source_entry(row['source'])
        entry['events'] += row['events']
        entry['by_level'][row['level']] = row['events']
        entry['last_event'] = max(entry['last_event'] or '', row['last_ts'])

    calls_by_source: Dict[str, List[Dict]] = {}
    calls_by_kind: Dict[str, Dict[str, List[Dict]]] = {}
    for call in db.get_feed_outcome_events(since):
        entry = source_entry(call['source'])
        calls_by_source.setdefault(call['source'], []).append(call)
        calls_by_kind.setdefault(call['source'], {}).setdefault(call['kind'], []).append(call)
        if call['outcome'] == OUTCOME_SUCCESS:
            entry['last_success'] = max(entry['last_success'] or '', call['ts'])
        elif call['outcome'] not in _HEALTHY_OUTCOMES:
            entry['last_failure'] = max(entry['last_failure'] or '', call['ts'])

    for name, entry in sources.items():
        calls = calls_by_source.get(name, [])
        counts = Counter(c['outcome'] for c in calls)
        entry.update(_summarise(calls))
        entry['outcomes'] = {o: counts.get(o, 0) for o in OUTCOMES}
        entry['by_kind'] = {kind: _summarise(rows)
                            for kind, rows in calls_by_kind.get(name, {}).items()}

    return {
        'hours': hours,
        'since': since,
        'generated_at': now.isoformat(),
        'retention_days': retention_days(),
        'total_events': sum(s['events'] for s in sources.values()),
        'sources': sources,
    }


def _summarise(calls: List[Dict]) -> Dict:
    """calls, failure_rate and duration percentiles for a list of outcome events."""
    total = len(calls)
    failed = sum(1 for c in calls if c['outcome'] not in _HEALTHY_OUTCOMES)
    durations = sorted(c['duration_ms'] for c in calls if c['duration_ms'] is not None)
    return {
        'calls': total,
        'failure_rate': round(failed / total, 3) if total else None,
        'duration_ms': {
            'count': len(durations),
            'p50': _percentile(durations, 50),
            'p95': _percentile(durations, 95),
            'max': durations[-1] if durations else None,
        },
    }


def _percentile(sorted_values: List[int], pct: int) -> Optional[int]:
    """Nearest-rank percentile of an ascending list; None when empty."""
    if not sorted_values:
        return None
    return sorted_values[max(0, math.ceil(pct / 100 * len(sorted_values)) - 1)]

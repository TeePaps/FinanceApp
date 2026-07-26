"""
Screener service for background stock screening tasks.

Provides:
- Full screener update (prices + EPS for all stocks)
- Quick price-only update
- Smart selective update (new tickers + stale valuations)
- Global refresh across all indexes
- Progress tracking
- Provider activity logging
"""

import os
import time
import json
import threading
import math
import functools
from datetime import datetime, timedelta

import database as db
import data_manager
# Note: get_index_data, get_all_unique_tickers imports are done lazily inside functions
# to avoid circular imports (database -> services.indexes -> services -> screener -> data_manager)
from config import (
    PE_RATIO_MULTIPLIER, FAILURE_THRESHOLD,
    SCREENER_DIVIDEND_BACKOFF, SCREENER_TICKER_PAUSE, SCREENER_PRICE_DELAY,
    STALENESS_DIVIDEND_FRESH_DAYS, DIVIDEND_FULL_SWEEP_DAYS,
    FIFTY_TWO_WEEK_REFRESH_DAYS, FIFTY_TWO_WEEK_RETRY_DAYS,
    COMPANY_NAME_RETRY_DAYS
)
from logger import log, log_error
from services.providers import get_orchestrator
from services.valuation import (
    get_validated_eps, calculate_valuation, compute_estimated_value,
    get_split_adjusted_eps_history, average_split_adjusted_eps,
)
from services.indexes import (
    VALID_INDICES, INDIVIDUAL_INDICES, INDEX_NAMES,
    fetch_index_tickers
)
from services.activity_log import activity_log
from services.utils import sanitize_for_json

# =============================================================================
# MODULE STATE
# =============================================================================

_running = False
_current_index = 'all'
_progress = {
    'current': 0,
    'total': 0,
    'ticker': '',
    'status': 'idle',
    'phase': '',
    'index': 'all',
    'index_name': 'All'
}


# =============================================================================
# STATE ACCESS FUNCTIONS
# =============================================================================

def is_running():
    """Check if screener is currently running."""
    return _running


def get_progress():
    """Get current screener progress."""
    return _progress.copy()


def stop():
    """Stop the running screener."""
    global _running
    _running = False
    _progress['status'] = 'cancelled'


def get_current_index():
    """Get the current index being processed."""
    return _current_index


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _clears_running_flag(fn):
    """
    Guarantee the module _running flag is released when a run_* function
    exits — INCLUDING unhandled exceptions. These run in daemon threads;
    without this, one sqlite error or provider TypeError left _running=True
    forever, and every later screener/refresh request was rejected with
    "already running" until the app was restarted.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        global _running
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            log_error(f"{fn.__name__} crashed", e)
            _progress['status'] = 'error'
            _progress['ticker'] = f"Error: {str(e)[:80]}"
            try:
                activity_log.log("error", "screener", f"{fn.__name__} crashed: {str(e)[:80]}")
            except Exception:
                pass
        finally:
            _running = False
    return wrapper


def _reconcile_delistings(all_tickers, priced_tickers):
    """
    Delisting bookkeeping — call ONLY after every price source for the run
    (history batch + real-time override) has been consulted.

    Tickers that priced get their strike count and delisted flag cleared;
    tickers that produced nothing anywhere get one strike and are delisted
    only after FAILURE_THRESHOLD consecutive missing runs. The old code
    delisted immediately after the (flaky) yfinance history batch, BEFORE
    the real-time fetch could recover the ticker — and nothing ever reset
    delisted=0, so live tickers silently vanished from every future run.

    If NOTHING priced, the whole pass looks like a provider outage — record
    no failures at all.
    """
    priced = [t for t in all_tickers if t in priced_tickers]
    failed = [t for t in all_tickers if t not in priced_tickers]

    if not priced:
        if all_tickers:
            activity_log.log(
                "warning", "screener",
                f"No prices for any of {len(all_tickers)} tickers — "
                "treating as provider outage, recording no delistings"
            )
        return

    db.record_price_successes(priced)
    if failed:
        newly = db.record_price_failures(failed, threshold=FAILURE_THRESHOLD)
        if newly:
            activity_log.log(
                "warning", "screener",
                f"Delisted after {FAILURE_THRESHOLD} consecutive missing runs: "
                f"{', '.join(newly[:10])}{'…' if len(newly) > 10 else ''}"
            )


def _needs_history_refresh():
    """Should this run re-download the 3-month price history batch?

    The 1m/3m change percentages derived from that history move once per
    trading day, but the scheduled quick update runs every 15 minutes during
    market hours - so the whole universe's 3-month OHLC was being downloaded
    ~26 times a day to recompute values that could not have changed. Refresh
    only when the stored history predates the most recent market close.
    """
    from services.utils import last_market_close

    stamp = db.get_metadata('last_history_update')
    if not stamp:
        return True
    try:
        return datetime.fromisoformat(stamp) < last_market_close()
    except (ValueError, TypeError):
        return True


def _repair_company_names(tickers, existing_valuations, orchestrator, enabled=True):
    """Resolve display names for tickers still stored as their own symbol.

    Returns {ticker: resolved_name}. This used to run inline in the quick
    update's build loop, firing a per-ticker .info fetch every 15 minutes for
    every ticker whose name would not resolve - the same failures retried ~26
    times a day forever. Now it runs at most once per trading day and backs off
    tickers that recently failed.
    """
    if not enabled:
        return {}

    candidates = [
        t for t in tickers
        if (existing_valuations.get(t) or {}).get('company_name', t) == t
    ]
    if not candidates:
        return {}

    checks = db.get_fetch_checks('company_name', candidates)
    retry_cutoff = datetime.now() - timedelta(days=COMPANY_NAME_RETRY_DAYS)

    pending = []
    for ticker in candidates:
        stamp = (checks.get(ticker) or {}).get('checked_at')
        if stamp:
            try:
                if datetime.fromisoformat(stamp) > retry_cutoff:
                    continue
            except (ValueError, TypeError):
                pass
        pending.append(ticker)

    if not pending:
        return {}

    activity_log.log("info", "screener",
                     f"Resolving {len(pending)} missing company names...")

    resolved, failed = {}, []
    for ticker in pending:
        if not _running:
            break
        try:
            info_result = orchestrator.fetch_stock_info(ticker)
            name = info_result.data.company_name if (
                info_result.success and info_result.data) else None
            if name and name != ticker:
                resolved[ticker] = name
            else:
                failed.append(ticker)
        except Exception:
            failed.append(ticker)

    db.record_fetch_checks('company_name', list(resolved.keys()), ok=True)
    db.record_fetch_checks('company_name', failed, ok=False)
    return resolved


# Persist dividends every N tickers so an interrupted phase keeps its work.
DIVIDEND_FLUSH_EVERY = 50

# Run the SEC frames bulk pre-pass when at least this many tickers have no
# stored EPS. Below it, the per-company path is cheaper than ~16 frame requests.
EPS_FRAMES_PREPASS_MIN_MISSING = 25


def _eps_frames_prepass(tickers):
    """Fill EPS gaps in bulk from the SEC frames API before the per-ticker loop.

    A cold start otherwise downloads one multi-megabyte companyfacts document
    per company, serialized behind the SEC rate limit. Frames answer the same
    question for every filer at once.
    """
    try:
        existing = db.get_existing_eps_years(tickers)
        missing = [t for t in tickers if not existing.get(t)]
        if len(missing) < EPS_FRAMES_PREPASS_MIN_MISSING:
            return

        activity_log.log("info", "screener",
                         f"Phase 1a: bulk EPS via SEC frames for {len(missing)} tickers without data...")

        def _frames_progress(done, total):
            _progress['ticker'] = f'Bulk SEC EPS (frames) {done}/{total}...'

        import sec_data
        stats = sec_data.refresh_eps_from_frames(
            tickers=missing, progress_callback=_frames_progress)
        activity_log.log(
            "success", "screener",
            f"✓ Phase 1a: {stats['rows_written']} EPS rows for "
            f"{stats['tickers_touched']} tickers in {stats['requests']} requests")
    except Exception as e:
        log_error("SEC frames pre-pass failed", e)


def _flush_dividends(dividend_data):
    """Persist accumulated dividend results with a column-scoped write.

    Only the dividend columns are touched, so this cannot disturb prices, EPS,
    or fair values that other phases own. Safe to call repeatedly - already
    flushed tickers are simply rewritten with the same values.
    """
    if not dividend_data:
        return
    try:
        data_manager.bulk_update_valuations({
            ticker: {
                'annual_dividend': payload['annual_dividend'],
            }
            for ticker, payload in dividend_data.items()
        })
    except Exception as e:
        log_error("Dividend flush failed", e)


def _dividend_full_sweep_due():
    """Is a forced full-universe dividend refresh due?

    Selective refresh keeps ordinary runs cheap, but a periodic unconditional
    sweep preserves the original safety property: a dividend that was silently
    wrong (rather than merely old) still gets corrected on a known cadence.
    """
    stamp = db.get_metadata('last_dividend_full_sweep')
    if not stamp:
        return True
    try:
        return datetime.now() - datetime.fromisoformat(stamp) >= timedelta(
            days=DIVIDEND_FULL_SWEEP_DAYS)
    except (ValueError, TypeError):
        return True


def _tickers_needing_dividends(tickers, existing_valuations):
    """Select tickers whose dividend data has aged past the freshness window.

    Dividends change roughly quarterly, so refetching all ~500 of them on every
    full run was pure waste. A ticker is refetched when it has never been
    stamped, when its own stamp is older than STALENESS_DIVIDEND_FRESH_DAYS, or
    when the periodic full sweep is due.
    """
    if _dividend_full_sweep_due():
        db.set_metadata('last_dividend_full_sweep', datetime.now().isoformat())
        activity_log.log("info", "screener",
                         "Phase 2: periodic full dividend sweep - refreshing all tickers")
        return list(tickers)

    cutoff = datetime.now() - timedelta(days=STALENESS_DIVIDEND_FRESH_DAYS)
    needing = []
    for ticker in tickers:
        stamp = (existing_valuations.get(ticker) or {}).get('dividend_updated')
        if not stamp:
            needing.append(ticker)
            continue
        try:
            if datetime.fromisoformat(stamp) < cutoff:
                needing.append(ticker)
        except (ValueError, TypeError):
            needing.append(ticker)
    return needing


def save_index_data(index_name, data):
    """Save index tickers to database."""
    if index_name not in VALID_INDICES or index_name == 'all':
        return
    tickers = data.get('tickers', [])
    if tickers:
        db.refresh_index_membership(index_name, tickers)


def load_excluded_tickers():
    """Load excluded tickers from database."""
    return set(db.get_excluded_tickers(threshold=FAILURE_THRESHOLD))


def record_ticker_failures(failed_tickers, successful_tickers):
    """Record ticker failures and clear successes.

    One transaction for the whole set: the per-ticker version cost an upsert
    plus a read for every failure and a delete for every success, i.e. one to
    two connections per ticker across the entire universe on every run.
    """
    return db.record_ticker_failures_bulk(
        failed_tickers, successful_tickers, threshold=FAILURE_THRESHOLD)


# calculate_valuation() now imported from services.valuation
# All valuation calculation is centralized in services/valuation.py

# =============================================================================
# MAIN SCREENER FUNCTIONS
# =============================================================================

@_clears_running_flag
def run_screener(index_name='all'):
    """
    Full screener update with 4 phases:
    1. SEC EPS data
    2. Dividends
    3. Prices
    4. Build valuations
    """
    global _running, _progress, _current_index
    import numpy as np
    from data_manager import get_index_data  # Lazy import to avoid circular dependency

    log.info(f"=== SCREENER STARTED for index '{index_name}' ===")
    start_time = time.time()

    _running = True
    _current_index = index_name

    # Sync index membership
    log.info("Syncing index membership...")
    if index_name != 'all':
        current_tickers = fetch_index_tickers(index_name)
        if current_tickers:
            result = db.refresh_index_membership(index_name, current_tickers)
            log.info(f"[Index] Synced {index_name}: {result['total']} current")
    else:
        for idx in INDIVIDUAL_INDICES:
            current_tickers = fetch_index_tickers(idx)
            if current_tickers:
                result = db.refresh_index_membership(idx, current_tickers)
                log.info(f"[Index] Synced {idx}: {result['total']} current")
        orphan_result = db.remove_orphan_valuations()
        if orphan_result['orphans_found'] > 0:
            log.info(f"[Orphans] Removed {orphan_result['orphans_found']} orphan valuations")

    data = get_index_data(index_name)
    tickers = data['tickers']
    existing_valuations = data.get('valuations', {})
    index_display_name = data.get('short_name', index_name)

    log.info(f"Screener: {len(tickers)} tickers to process")

    _progress = {
        'current': 0, 'total': len(tickers),
        'ticker': 'Starting...',
        'status': 'running', 'phase': 'eps',
        'index': index_name, 'index_name': index_display_name
    }

    # Phase 1: SEC EPS Data
    log.info("Screener Phase 1: Loading SEC EPS data...")
    activity_log.log("info", "screener", f"Phase 1: Loading SEC EPS data for {len(tickers)} tickers...")
    phase1_start = time.time()
    _progress['phase'] = 'eps'
    _progress['ticker'] = 'Loading SEC EPS data...'

    eps_results = {}
    sec_hits = 0
    sec_failures = 0
    existing_hits = 0
    orchestrator = get_orchestrator()

    # Bulk pre-pass: when a lot of tickers have no stored EPS at all (a cold
    # start, or a newly enabled index), fill them from the SEC frames API -
    # ~2 requests per year for the ENTIRE universe instead of one multi-MB
    # companyfacts download per company. Whatever frames can't supply still
    # falls through to the per-company path below.
    _eps_frames_prepass(tickers)

    # Preload eps_history and split_history for the whole run in two queries.
    # get_split_adjusted_eps_history() otherwise opens two connections and runs
    # two queries per call, and this phase calls it up to twice per ticker.
    # The eps map is refreshed for tickers whose SEC fetch writes new rows.
    eps_history_map = db.get_eps_history_bulk(tickers)
    splits_map = db.get_splits_bulk(tickers)

    for i, t in enumerate(tickers):
        if i % 50 == 0:
            _progress['current'] = i
            _progress['ticker'] = f'Loading SEC EPS... ({sec_hits} SEC, {existing_hits} cached)'
            if i > 0 and i % 200 == 0:
                log.info(f"Phase 1 progress: {i}/{len(tickers)} - SEC: {sec_hits}, cached: {existing_hits}, no data: {sec_failures}")

        sec_result = orchestrator.fetch_eps(t)
        if sec_result.success and sec_result.data and sec_result.data.eps_history:
            # Read split-adjusted history from the eps_history table — the fresh
            # SEC fetch just persisted the latest raw values, so this picks them up
            # and applies split-adjustment in one pass (e.g. BKNG's 25:1 split).
            # A live fetch just rewrote this ticker's rows, so re-read them
            # rather than trusting the preloaded map for this one ticker.
            if not sec_result.cached:
                eps_history_map[t] = db.get_eps_history(t)
            adjusted = get_split_adjusted_eps_history(
                t, eps_history_map=eps_history_map, splits_map=splits_map)
            if not adjusted:
                # Provider returned data but the table wasn't populated for some
                # reason — fall back to the raw fetched list (un-adjusted).
                adjusted = [dict(e) for e in sec_result.data.eps_history]
            eps_avg, years_used = average_split_adjusted_eps(adjusted)
            if eps_avg is not None:
                eps_results[t] = {
                    'ticker': t,
                    'company_name': sec_result.data.company_name or t,
                    'eps_avg': round(eps_avg, 2),
                    'eps_years': years_used,
                    'eps_source': sec_result.source or 'sec',
                    'has_enough_years': years_used >= 8,
                    'annual_dividend': existing_valuations.get(t, {}).get('annual_dividend', 0),
                }
                sec_hits += 1
                continue

        # Fallback 1: SEC EPS cached in eps_history table from a prior successful fetch.
        # Prevents a transient SEC outage from overwriting good SEC data with stale
        # yfinance values via the existing-valuations fallback below.
        cached_history = get_split_adjusted_eps_history(
            t, eps_history_map=eps_history_map, splits_map=splits_map)
        cached_avg, cached_years = average_split_adjusted_eps(cached_history)
        if cached_avg is not None:
            eps_results[t] = {
                'ticker': t,
                'company_name': existing_valuations.get(t, {}).get('company_name') or t,
                'eps_avg': round(cached_avg, 2),
                'eps_years': cached_years,
                'eps_source': 'sec_cache',
                'has_enough_years': cached_years >= 8,
                'annual_dividend': existing_valuations.get(t, {}).get('annual_dividend', 0),
            }
            sec_hits += 1
            continue

        # Fallback 2: previous valuation row (last resort, may be stale yfinance)
        existing = existing_valuations.get(t, {})
        if existing.get('eps_avg') is not None:
            eps_results[t] = existing
            existing_hits += 1
        else:
            sec_failures += 1

    _progress['current'] = len(tickers)
    phase1_time = time.time() - phase1_start
    log.info(f"Screener Phase 1 complete: {phase1_time:.1f}s - SEC: {sec_hits}, cached: {existing_hits}, no data: {sec_failures}")
    activity_log.log("success", "screener", f"✓ Phase 1: SEC EPS ({sec_hits} new, {existing_hits} cached, {sec_failures} missing)")

    if not _running:
        _progress['status'] = 'cancelled'
        _running = False
        return

    # Phase 2: Dividends — refresh those whose per-ticker stamp has aged out.
    #
    # This phase used to refetch EVERY ticker on EVERY full run because nothing
    # recorded per-ticker dividend freshness; only a global metadata key
    # existed. That was deliberate: an earlier cache-skip let stale dividends
    # linger and corrupted fair values (e.g. PGR cached $4.90 vs actual
    # $13.90). The dividend_updated column now makes selective refresh safe -
    # a row is only skipped if IT was refreshed within the freshness window,
    # not because some other ticker was. A periodic full sweep still runs (see
    # _dividend_full_sweep_due) so nothing can drift indefinitely.
    tickers_needing_dividends = _tickers_needing_dividends(tickers, existing_valuations)
    dividend_data = {}

    skipped_dividends = len(tickers) - len(tickers_needing_dividends)
    if skipped_dividends > 0:
        activity_log.log("info", "screener",
                         f"Phase 2: {skipped_dividends} dividends still fresh - skipping")

    if tickers_needing_dividends:
        log.info(f"Screener Phase 2: Fetching dividends for {len(tickers_needing_dividends)} tickers...")
        activity_log.log("info", "screener", f"Phase 2: Fetching dividends for {len(tickers_needing_dividends)} tickers...")
        _progress['phase'] = 'dividends'
        _progress['total'] = len(tickers_needing_dividends)
        _progress['current'] = 0

        dividend_count = 0
        backoff_delay = SCREENER_DIVIDEND_BACKOFF

        for i, ticker in enumerate(tickers_needing_dividends):
            if not _running:
                break
            if i % 50 == 0:
                _progress['current'] = i
                _progress['ticker'] = f'Fetching dividends... {i}/{len(tickers_needing_dividends)}'
                if i > 0:  # Don't duplicate the initial "Phase 2" message
                    activity_log.log("info", "screener", f"Dividends: {i}/{len(tickers_needing_dividends)} ({dividend_count} found so far)")

            try:
                orchestrator = get_orchestrator()
                result = orchestrator.fetch_dividends(ticker)
                if result.success and result.data:
                    dividend_data_obj = result.data
                    annual_dividend = dividend_data_obj.annual_dividend
                    if annual_dividend > 0:
                        payments = dividend_data_obj.payments
                        last_payment = payments[-1] if payments else None
                        dividend_data[ticker] = {
                            'annual_dividend': round(annual_dividend, 2),
                            'last_dividend': round(last_payment['amount'], 4) if last_payment else 0,
                            'last_dividend_date': last_payment['date'] if last_payment else ''
                        }
                        dividend_count += 1
                    else:
                        # Successful fetch of 0. Same policy as
                        # calculate_valuation's flakiness guard: distrust a
                        # fresh 0 when the cache has a non-zero dividend
                        # (yfinance intermittently returns empty payment
                        # lists), but record an authoritative 0 when the
                        # cache agrees there's no dividend — otherwise a
                        # company that ELIMINATED its dividend kept the old
                        # value inflating fair value forever, the exact
                        # corruption this phase exists to prevent.
                        cached_div = existing_valuations.get(ticker, {}).get('annual_dividend') or 0
                        if cached_div > 0:
                            log.warning(f"[{ticker}] fresh dividend fetch returned 0; "
                                        f"keeping cached ${cached_div:.2f} (flaky-fetch guard)")
                        else:
                            dividend_data[ticker] = {
                                'annual_dividend': 0,
                                'last_dividend': 0,
                                'last_dividend_date': ''
                            }
            except Exception:
                pass
            # No screener-side sleep here: the orchestrator already paces this
            # provider (yfinance dividend rate_limit). The old fixed 0.3s slept
            # unconditionally - including after cache skips, immediate failures
            # and circuit-open skips - stacking on top of that limiter.

            # Flush periodically so a cancel or crash mid-phase keeps the
            # dividends already fetched instead of discarding tens of minutes
            # of network work (nothing was persisted until Phase 4).
            if dividend_data and (i + 1) % DIVIDEND_FLUSH_EVERY == 0:
                _flush_dividends(dividend_data)

        if dividend_data:
            _flush_dividends(dividend_data)

        _progress['current'] = len(tickers_needing_dividends)
        log.info(f"Screener Phase 2 complete: found dividends for {dividend_count} tickers")
        activity_log.log("success", "screener", f"✓ Phase 2: Dividends fetched ({dividend_count} found)")

    if not _running:
        _progress['status'] = 'cancelled'
        _running = False
        return

    # Phase 2b: Stock Splits
    # Persist split history so the Analyze page and Recommendations can show
    # a Split Warning when a split falls within the EPS averaging window.
    # Skips tickers whose split data was fetched recently (split_cache_days).
    from services.providers import get_config as get_provider_config
    split_cache_days = get_provider_config().split_cache_days
    split_freshness_cutoff = (datetime.now() - timedelta(days=split_cache_days)).isoformat()

    # One query for the whole universe instead of two connections per ticker,
    # and it consults split_checks so tickers that have simply never split
    # count as checked rather than being re-fetched every run.
    last_checked_map = db.get_split_last_checked_bulk(tickers)

    def _needs_split_refresh(ticker):
        last = last_checked_map.get(ticker)
        return not last or last < split_freshness_cutoff

    tickers_needing_splits = [t for t in tickers if _needs_split_refresh(t)]

    if tickers_needing_splits:
        log.info(f"Screener Phase 2b: Fetching splits for {len(tickers_needing_splits)} tickers...")
        activity_log.log("info", "screener", f"Phase 2b: Fetching splits for {len(tickers_needing_splits)} tickers...")
        _progress['phase'] = 'splits'
        _progress['total'] = len(tickers_needing_splits)
        _progress['current'] = 0

        splits_found = 0
        orchestrator = get_orchestrator()
        checked_tickers = []

        for i, ticker in enumerate(tickers_needing_splits):
            if not _running:
                break
            if i % 50 == 0:
                _progress['current'] = i
                _progress['ticker'] = f'Fetching splits... {i}/{len(tickers_needing_splits)}'

            try:
                result = orchestrator.fetch_splits(ticker)
                if result.success and result.data:
                    if result.data.splits:
                        db.upsert_splits(ticker, result.data.splits, source=result.source or 'unknown')
                        splits_found += 1
                    # Remember the negative answer too. Without this, the ~30%
                    # of the universe that has never split left no trace and
                    # was re-fetched on every run despite the 7-day policy.
                    checked_tickers.append(ticker)
            except Exception:
                pass

        if checked_tickers:
            db.record_split_checks(checked_tickers)

        _progress['current'] = len(tickers_needing_splits)
        log.info(f"Screener Phase 2b complete: persisted splits for {splits_found} tickers")
        activity_log.log("success", "screener", f"✓ Phase 2b: Splits persisted ({splits_found} tickers had splits)")

        # Track split refresh for staleness dashboards
        db.set_metadata('last_split_update', datetime.now().isoformat())

    if not _running:
        _progress['status'] = 'cancelled'
        _running = False
        return

    # Phase 3: Prices
    log.info("Screener Phase 3: Batch downloading prices...")
    _progress['phase'] = 'prices'
    _progress['ticker'] = 'Batch downloading prices...'
    _progress['total'] = len(tickers)
    _progress['current'] = 0

    history_results = {}
    info_cache = {}

    try:
        activity_log.log("info", "screener", f"Phase 3: Fetching 3mo history for {len(tickers)} tickers...")
        orchestrator = get_orchestrator()
        history_results = orchestrator.fetch_price_history_batch(tickers, period='3mo')
        activity_log.log("success", "screener", f"✓ 3mo history: {len(history_results)} tickers downloaded")

        # NOTE: no delisting here — the real-time price override below is a
        # second chance; _reconcile_delistings runs after it (finding: a
        # transient batch miss permanently delisted live tickers).

        for t in tickers:
            existing = existing_valuations.get(t, {})
            if existing.get('fifty_two_week_high'):
                info_cache[t] = {
                    'fiftyTwoWeekHigh': existing.get('fifty_two_week_high', 0),
                    'fiftyTwoWeekLow': existing.get('fifty_two_week_low', 0),
                    'shortName': existing.get('company_name', t)
                }

        _progress['current'] = len(tickers)
        log.info(f"Screener Phase 3 complete")
    except Exception as e:
        log_error(f"Screener Phase 3 failed", e)
        history_results = {}

    if not _running:
        _progress['status'] = 'cancelled'
        _running = False
        return

    # Phase 4: Build valuations
    log.info("Screener Phase 4: Building valuations...")
    activity_log.log("info", "screener", f"Phase 4: Building valuations...")
    _progress['phase'] = 'combining'
    _progress['ticker'] = 'Building valuations...'

    valuations_batch = {}
    now_iso = datetime.now().isoformat()

    current_prices_dict = {}
    price_change_3m_dict = {}
    price_change_1m_dict = {}
    price_sources_dict = {}

    for ticker, result in history_results.items():
        if result.success and result.data:
            hist_data = result.data
            current_prices_dict[ticker] = hist_data.current_price
            if hist_data.change_3m_pct is not None:
                price_change_3m_dict[ticker] = hist_data.change_3m_pct
            if hist_data.change_1m_pct is not None:
                price_change_1m_dict[ticker] = hist_data.change_1m_pct

    # Override with real-time prices. Include currently-delisted tickers in
    # the fetch — pricing successfully is the recovery path that clears a
    # wrong delisted flag (nothing else ever retries them, because
    # get_active_index_tickers hides delisted tickers from ticker lists).
    retry_delisted = []
    try:
        retry_delisted = [t for t in db.get_delisted_tickers() if t not in set(tickers)]
    except Exception:
        pass
    try:
        orchestrator = get_orchestrator()
        provider_prices, provider_sources = orchestrator.fetch_prices(
            tickers + retry_delisted, skip_cache=True, return_sources=True)
        for ticker, price in provider_prices.items():
            if price and price > 0:
                current_prices_dict[ticker] = float(price)
                price_sources_dict[ticker] = provider_sources.get(ticker)
    except Exception:
        pass

    # All price sources consulted — now do the delisting bookkeeping
    _reconcile_delistings(tickers + retry_delisted,
                          {t for t, p in current_prices_dict.items() if p and p > 0})

    for i, ticker in enumerate(tickers):
        if i % 500 == 0:
            _progress['current'] = i + 1

        current_price = current_prices_dict.get(ticker)
        if current_price is None:
            continue
        if isinstance(current_price, float) and (math.isnan(current_price) or math.isinf(current_price)):
            continue

        price_change_3m = price_change_3m_dict.get(ticker)
        price_change_1m = price_change_1m_dict.get(ticker)

        if price_change_3m is not None and math.isnan(price_change_3m):
            price_change_3m = None
        if price_change_1m is not None and math.isnan(price_change_1m):
            price_change_1m = None

        info = info_cache.get(ticker, {})
        fifty_two_week_high = info.get('fiftyTwoWeekHigh', 0)
        fifty_two_week_low = info.get('fiftyTwoWeekLow', 0)
        company_name = info.get('shortName', ticker)

        off_high_pct = None
        if fifty_two_week_high and current_price:
            off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

        # Get EPS info: first from Phase 1 results, then database fallback, then existing valuations
        eps_info = eps_results.get(ticker)
        eps_avg = eps_info.get('eps_avg') if eps_info else None

        # Database fallback: if Phase 1 didn't get EPS, check eps_history table
        # (split-adjusted so post-split tickers like BKNG get correct averages).
        if not eps_avg:
            eps_history = get_split_adjusted_eps_history(ticker)
            calculated_avg, years_used = average_split_adjusted_eps(eps_history)
            if calculated_avg is not None:
                eps_info = {
                    'eps_avg': round(calculated_avg, 2),
                    'eps_years': years_used,
                    'eps_source': 'sec_cache',
                    'company_name': eps_info.get('company_name') if eps_info else None
                }
                eps_avg = eps_info['eps_avg']

        # Final fallback to existing valuations
        if not eps_avg:
            eps_info = existing_valuations.get(ticker, {})
            eps_avg = eps_info.get('eps_avg')

        div_info = dividend_data.get(ticker, {})
        if 'annual_dividend' in div_info:
            # Fresh fetch succeeded this run — authoritative, INCLUDING 0
            # (an `or` chain here made a fetched 0 fall through to the
            # stale cached value).
            annual_dividend = div_info['annual_dividend']
        else:
            # Fetch failed — better a cached dividend than none
            annual_dividend = (
                eps_info.get('annual_dividend') or
                existing_valuations.get(ticker, {}).get('annual_dividend') or
                0
            )

        if eps_info.get('company_name'):
            company_name = eps_info['company_name']

        estimated_value, price_vs_value = compute_estimated_value(
            eps_avg, annual_dividend, current_price
        )

        in_selloff = False
        selloff_severity = 'none'
        if off_high_pct and off_high_pct < -30:
            in_selloff = True
            selloff_severity = 'severe'
        elif off_high_pct and off_high_pct < -20:
            in_selloff = True
            selloff_severity = 'moderate'
        elif price_change_3m and price_change_3m < -15:
            in_selloff = True
            selloff_severity = 'recent'

        valuation = {
            'ticker': ticker,
            'company_name': company_name,
            'current_price': round(current_price, 2),
            'price_source': price_sources_dict.get(ticker),
            'eps_avg': round(eps_avg, 2) if eps_avg is not None else None,
            'eps_years': eps_info.get('eps_years', 0),
            'eps_source': eps_info.get('eps_source', 'unknown'),
            'has_enough_years': eps_info.get('has_enough_years', False),
            'annual_dividend': round(annual_dividend, 2) if annual_dividend else 0,
            'last_dividend': eps_info.get('last_dividend'),
            'last_dividend_date': eps_info.get('last_dividend_date'),
            'estimated_value': round(estimated_value, 2) if estimated_value else None,
            'price_vs_value': round(price_vs_value, 1) if price_vs_value is not None else None,
            'fifty_two_week_high': round(fifty_two_week_high, 2) if fifty_two_week_high else None,
            'fifty_two_week_low': round(fifty_two_week_low, 2) if fifty_two_week_low else None,
            'off_high_pct': round(off_high_pct, 1) if off_high_pct is not None else None,
            'price_change_1m': round(price_change_1m, 1) if price_change_1m is not None else None,
            'price_change_3m': round(price_change_3m, 1) if price_change_3m is not None else None,
            'in_selloff': in_selloff,
            'selloff_severity': selloff_severity,
            'updated': now_iso
        }

        data['valuations'][ticker] = valuation
        valuations_batch[ticker] = valuation

    _progress['current'] = len(tickers)

    if valuations_batch:
        data_manager.bulk_update_valuations(valuations_batch)
        log.info(f"Screener: Saved {len(valuations_batch)} valuations")

        ticker_status_updates = {}
        for ticker, val in valuations_batch.items():
            sec_status = ('available'
                          if str(val.get('eps_source') or '').startswith('sec')
                          else 'unavailable')
            ticker_status_updates[ticker] = {
                'sec_status': sec_status,
                'valuation_updated': now_iso,
                'company_name': val.get('company_name')
            }
        data_manager.bulk_update_ticker_status(ticker_status_updates)

    if index_name != 'all':
        save_index_data(index_name, data)

    total_duration = time.time() - start_time
    activity_log.log("success", "screener", f"✓ Complete: {len(valuations_batch)} valuations in {total_duration:.1f}s")
    log.info(f"=== SCREENER COMPLETE for '{index_name}': {len(valuations_batch)} valuations in {total_duration:.1f}s ===")

    # Update staleness metadata
    now_iso = datetime.now().isoformat()
    db.set_metadata('last_price_update', now_iso)
    db.set_metadata('last_dividend_update', now_iso)

    # Phase: Fetch 52-week data for tickers that need it
    if _running:
        _fetch_52_week_data(tickers)

    # Phase 5: Star Ratings (6-criterion scoring)
    if _running and valuations_batch:
        log.info("Screener Phase 5: Calculating star ratings...")
        activity_log.log("info", "screener", f"Phase 5: Calculating star ratings for {len(valuations_batch)} tickers...")
        _progress['phase'] = 'stars'
        _progress['ticker'] = 'Calculating star ratings...'
        _progress['total'] = len(valuations_batch)
        _progress['current'] = 0

        def _stars_progress(current, ticker):
            _progress['current'] = current
            _progress['ticker'] = f'Stars: {ticker}'

        try:
            from services.stars import calculate_all_star_ratings
            rated = calculate_all_star_ratings(
                list(valuations_batch.keys()),
                progress_callback=_stars_progress,
            )
            activity_log.log("success", "screener", f"✓ Phase 5: Star ratings ({rated} tickers)")
        except Exception as e:
            log_error("Screener Phase 5 (stars) failed", e)
            activity_log.log("error", "screener", f"Star rating phase failed: {str(e)[:80]}")

    _progress['status'] = 'complete'
    _running = False


@_clears_running_flag
def run_quick_price_update(index_name='all'):
    """Fast update - batch download prices only, reuse cached EPS data."""
    global _running, _progress, _current_index
    import numpy as np
    import pandas as pd
    from data_manager import get_index_data  # Lazy import to avoid circular dependency

    log.info(f"=== QUICK PRICE UPDATE STARTED for '{index_name}' ===")
    start_time = time.time()

    _running = True
    _current_index = index_name
    data = get_index_data(index_name)
    tickers_raw = data['tickers']
    existing_valuations = data.get('valuations', {})
    index_display_name = data.get('short_name', index_name)

    excluded = load_excluded_tickers()
    if excluded:
        tickers = [t for t in tickers_raw if t not in excluded]
    else:
        tickers = tickers_raw

    _progress = {
        'current': 0, 'total': len(tickers),
        'ticker': 'Downloading prices...',
        'status': 'running', 'phase': 'prices',
        'index': index_name, 'index_name': index_display_name
    }

    activity_log.log("info", "screener", f"Quick Update: {len(tickers)} tickers ({index_display_name})")

    updated_count = 0
    try:
        orchestrator = get_orchestrator()

        # The 3-month history is only needed once per trading day (see
        # _needs_history_refresh). Intraday runs do the cheap real-time pass
        # only and keep the day's stored 1m/3m percentages.
        fetched_history = _needs_history_refresh()
        history_results = {}

        if fetched_history:
            activity_log.log("info", "screener", f"Phase 1: Fetching 3mo history...")
            history_results = orchestrator.fetch_price_history_batch(tickers, period='3mo')
            activity_log.log("success", "screener", f"✓ 3mo history: {len(history_results)} tickers downloaded")
            if history_results:
                db.set_metadata('last_history_update', datetime.now().isoformat())
        else:
            activity_log.log("info", "screener",
                             "Phase 1: 3mo history already current for this session - skipped")

        if fetched_history and not history_results:
            _progress['status'] = 'complete'
            _running = False
            return

        # Delisting decisions deferred to _reconcile_delistings after the
        # real-time price phase below (transient batch miss ≠ delisted).

        _progress['current'] = len(tickers)
        _progress['phase'] = 'combining'

        current_prices_dict = {}
        price_change_3m_dict = {}
        price_change_1m_dict = {}

        for ticker, result in history_results.items():
            if result.success and result.data:
                hist_data = result.data
                current_prices_dict[ticker] = hist_data.current_price
                if hist_data.change_3m_pct is not None:
                    price_change_3m_dict[ticker] = hist_data.change_3m_pct
                if hist_data.change_1m_pct is not None:
                    price_change_1m_dict[ticker] = hist_data.change_1m_pct

        current_prices = pd.Series(current_prices_dict)
        price_change_3m = pd.Series(price_change_3m_dict)
        price_change_1m = pd.Series(price_change_1m_dict)

        price_sources_dict = {}
        activity_log.log("info", "screener", f"Phase 2: Fetching real-time prices...")
        try:
            provider_prices, provider_sources = orchestrator.fetch_prices(tickers, skip_cache=True, return_sources=True)
            realtime_count = 0
            for ticker, price in provider_prices.items():
                if price and price > 0:
                    current_prices_dict[ticker] = float(price)
                    price_sources_dict[ticker] = provider_sources.get(ticker)
                    realtime_count += 1
            current_prices = pd.Series(current_prices_dict)
            activity_log.log("success", "screener", f"✓ Real-time prices: {realtime_count} tickers updated")
        except Exception as e:
            activity_log.log("warning", "screener", f"Real-time prices failed: {str(e)[:50]}")

        # All price sources consulted — delisting bookkeeping.
        # Only when the history batch actually ran: it is the broader of the two
        # sources, so scoring strikes on a real-time-only pass would delist any
        # ticker the real-time providers don't cover after three 15-minute runs.
        if fetched_history:
            _reconcile_delistings(tickers,
                                  {t for t, p in current_prices_dict.items() if p and p > 0})

        # Name repair is a once-a-day maintenance chore, not per-cycle work:
        # tie it to the same daily gate as the history batch.
        repaired_names = _repair_company_names(
            tickers, existing_valuations, orchestrator, enabled=fetched_history)

        activity_log.log("info", "screener", f"Phase 3: Building valuations...")
        valuations_batch = {}
        updated_count = 0

        for i, ticker in enumerate(tickers):
            if not _running:
                _progress['status'] = 'cancelled'
                break

            if i % 200 == 0:
                _progress['current'] = i
                _progress['ticker'] = f'Building valuations... {i}/{len(tickers)}'

            try:
                if ticker not in current_prices.index or pd.isna(current_prices[ticker]):
                    continue

                current_price = float(current_prices[ticker])
                existing = existing_valuations.get(ticker, {})
                eps_avg = existing.get('eps_avg')
                eps_years = existing.get('eps_years')
                eps_source = existing.get('eps_source')
                annual_dividend = existing.get('annual_dividend', 0)
                fifty_two_week_high = existing.get('fifty_two_week_high')
                company_name = existing.get('company_name', ticker)

                # If no EPS data in existing valuation, check SEC cache
                # (split-adjusted + 8-year window, same as the full screener)
                if eps_avg is None:
                    cached_history = get_split_adjusted_eps_history(ticker)
                    cached_avg, cached_years = average_split_adjusted_eps(cached_history)
                    if cached_avg is not None:
                        eps_avg = cached_avg
                        eps_years = cached_years
                        eps_source = 'sec_cache'
                        sec_data = db.get_sec_company(ticker)
                        sec_company_name = sec_data.get('company_name') if sec_data else None
                        if sec_company_name and sec_company_name != ticker:
                            company_name = sec_company_name

                # Names resolved above by _repair_company_names (rate-limited
                # and backed off), rather than a live .info fetch per ticker
                # per 15-minute cycle.
                company_name = repaired_names.get(ticker, company_name)

                off_high_pct = None
                if fifty_two_week_high and fifty_two_week_high > 0:
                    off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

                estimated_value, price_vs_value = compute_estimated_value(
                    eps_avg, annual_dividend, current_price
                )

                # Fall back to the stored percentages when this run skipped the
                # history batch, so an intraday pass never blanks them out.
                pc_3m = (price_change_3m.get(ticker) if ticker in price_change_3m.index
                         else existing.get('price_change_3m'))
                pc_1m = (price_change_1m.get(ticker) if ticker in price_change_1m.index
                         else existing.get('price_change_1m'))

                in_selloff = False
                selloff_severity = 'none'
                if off_high_pct is not None and off_high_pct < -30:
                    in_selloff = True
                    selloff_severity = 'severe'
                elif off_high_pct is not None and off_high_pct < -20:
                    in_selloff = True
                    selloff_severity = 'moderate'
                elif pc_3m is not None and not pd.isna(pc_3m) and pc_3m < -15:
                    in_selloff = True
                    selloff_severity = 'recent'

                valuations_batch[ticker] = {
                    **existing,
                    'ticker': ticker,
                    'company_name': company_name,
                    'current_price': round(current_price, 2),
                    'price_source': price_sources_dict.get(ticker),
                    'eps_avg': round(eps_avg, 2) if eps_avg is not None else None,
                    'eps_years': eps_years,
                    'eps_source': eps_source,
                    'estimated_value': round(estimated_value, 2) if estimated_value else None,
                    'price_vs_value': round(price_vs_value, 1) if price_vs_value is not None else None,
                    'off_high_pct': round(off_high_pct, 1) if off_high_pct is not None else None,
                    'price_change_1m': round(float(pc_1m), 1) if pc_1m is not None and not pd.isna(pc_1m) else None,
                    'price_change_3m': round(float(pc_3m), 1) if pc_3m is not None and not pd.isna(pc_3m) else None,
                    'in_selloff': in_selloff,
                    'selloff_severity': selloff_severity,
                    'updated': datetime.now().isoformat()
                }
                updated_count += 1
            except Exception:
                continue

        _progress['current'] = len(tickers)

        if valuations_batch:
            data_manager.bulk_update_valuations(valuations_batch)

    except Exception as e:
        log_error(f"Quick Update failed", e)

    total_duration = time.time() - start_time
    activity_log.log("success", "screener", f"✓ Complete: {updated_count} valuations updated in {total_duration:.1f}s")
    log.info(f"=== QUICK PRICE UPDATE COMPLETE for '{index_name}': {updated_count} updated in {total_duration:.1f}s ===")

    # Update staleness metadata (prices only for quick update)
    db.set_metadata('last_price_update', datetime.now().isoformat())

    # Phase: Fetch 52-week data for tickers that need it
    if _running:
        _fetch_52_week_data(tickers)

    _progress['status'] = 'complete'
    _running = False


@_clears_running_flag
def run_smart_update(index_name='all'):
    """Smart update - prioritizes missing tickers, then updates prices for existing ones."""
    global _running, _progress, _current_index
    import pandas as pd
    from data_manager import get_index_data  # Lazy import to avoid circular dependency

    log.info(f"=== SMART UPDATE STARTED for '{index_name}' ===")
    start_time = time.time()

    _running = True
    _current_index = index_name
    data = get_index_data(index_name)
    tickers = data['tickers']
    existing_valuations = set(data.get('valuations', {}).keys())
    index_display_name = data.get('short_name', index_name)

    missing_tickers = [t for t in tickers if t not in existing_valuations]
    existing_tickers = [t for t in tickers if t in existing_valuations]

    total_work = len(missing_tickers) + len(existing_tickers)
    _progress = {
        'current': 0,
        'total': total_work,
        'ticker': '',
        'status': 'running',
        'index': index_name,
        'index_name': index_display_name,
        'phase': 'missing'
    }

    activity_log.log("info", "screener", f"Smart Update: {len(missing_tickers)} new + {len(existing_tickers)} existing tickers")

    # Phase 1: Fetch full valuations for missing tickers.
    # Prices come from ONE batched call for the whole missing set; only the
    # genuinely per-ticker work (EPS/dividends/splits inside calculate_valuation)
    # stays in the loop. Previously every new ticker cost ~7 serial network
    # calls plus a fixed 0.5s sleep.
    if missing_tickers:
        activity_log.log("info", "screener", f"Phase 1: Fetching full data for {len(missing_tickers)} new tickers...")
        try:
            # Warms the price cache so calculate_valuation's price lookup is a
            # cache hit rather than a per-ticker provider round trip.
            get_orchestrator().fetch_prices(missing_tickers, skip_cache=True)
        except Exception as e:
            activity_log.log("warning", "screener",
                             f"Batch price warm-up failed: {str(e)[:50]}")

    # Rows this run actually modifies, so the final write touches only those.
    modified_valuations = {}

    new_successes, new_failures = [], []
    for i, ticker in enumerate(missing_tickers):
        if not _running:
            _progress['status'] = 'cancelled'
            break

        _progress['current'] = i + 1
        _progress['ticker'] = f"[NEW] {ticker}"
        _progress['phase'] = 'missing'

        # Log progress every 25 tickers
        if i > 0 and i % 25 == 0:
            activity_log.log("info", "screener", f"New tickers: {i}/{len(missing_tickers)}")

        valuation = calculate_valuation(ticker)
        if valuation and valuation.get('current_price', 0) > 0:
            data['valuations'][ticker] = valuation
            modified_valuations[ticker] = valuation
            new_successes.append(ticker)
            if (i + 1) % 100 == 0:
                save_index_data(index_name, data)
        else:
            # One strike — a single failed fetch must not permanently
            # delist a brand-new ticker (nothing ever retried them).
            new_failures.append(ticker)

        # No fixed sleep: the orchestrator's per-provider rate limiter already
        # paces every network call calculate_valuation makes.

    # Batched delist bookkeeping instead of one to two connections per ticker.
    if new_successes:
        db.record_price_successes(new_successes)
    if new_failures:
        db.record_price_failures(new_failures, threshold=FAILURE_THRESHOLD)

    if missing_tickers:
        activity_log.log("success", "screener", f"✓ Phase 1: New tickers processed")

    # Phase 2: Quick price update for existing tickers
    if _running and existing_tickers:
        _progress['phase'] = 'prices'
        _progress['ticker'] = 'Batch downloading prices...'

        try:
            activity_log.log("info", "screener", f"Fetching 3mo history for {len(existing_tickers)} existing tickers...")
            orchestrator = get_orchestrator()
            history_results = orchestrator.fetch_price_history_batch(existing_tickers, period='3mo')
            activity_log.log("success", "screener", f"✓ 3mo history: {len(history_results)} tickers downloaded")

            # Delisting decisions deferred to _reconcile_delistings after the
            # real-time price phase below (transient batch miss ≠ delisted).

            if history_results:
                current_prices_dict = {}
                price_change_3m_dict = {}
                price_change_1m_dict = {}

                for ticker, result in history_results.items():
                    if result.success and result.data:
                        hist_data = result.data
                        current_prices_dict[ticker] = hist_data.current_price
                        if hist_data.change_3m_pct is not None:
                            price_change_3m_dict[ticker] = hist_data.change_3m_pct
                        if hist_data.change_1m_pct is not None:
                            price_change_1m_dict[ticker] = hist_data.change_1m_pct

                current_prices = pd.Series(current_prices_dict)
                price_change_3m = pd.Series(price_change_3m_dict)
                price_change_1m = pd.Series(price_change_1m_dict)

                price_sources_dict = {}
                try:
                    provider_prices, provider_sources = orchestrator.fetch_prices(existing_tickers, skip_cache=True, return_sources=True)
                    for ticker, price in provider_prices.items():
                        if price and price > 0:
                            current_prices_dict[ticker] = float(price)
                            price_sources_dict[ticker] = provider_sources.get(ticker)
                    current_prices = pd.Series(current_prices_dict)
                except Exception:
                    pass

                # All price sources consulted — delisting bookkeeping
                _reconcile_delistings(existing_tickers,
                                      {t for t, p in current_prices_dict.items() if p and p > 0})

                for i, ticker in enumerate(existing_tickers):
                    if not _running:
                        _progress['status'] = 'cancelled'
                        break

                    _progress['current'] = len(missing_tickers) + i + 1
                    _progress['ticker'] = ticker

                    try:
                        if ticker not in current_prices.index or pd.isna(current_prices[ticker]):
                            continue

                        current_price = float(current_prices[ticker])
                        existing = data.get('valuations', {}).get(ticker, {})
                        eps_avg = existing.get('eps_avg')
                        annual_dividend = existing.get('annual_dividend', 0)
                        fifty_two_week_high = existing.get('fifty_two_week_high')

                        off_high_pct = None
                        if fifty_two_week_high and fifty_two_week_high > 0:
                            off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

                        estimated_value, price_vs_value = compute_estimated_value(
                            eps_avg, annual_dividend, current_price
                        )

                        pc_3m = price_change_3m.get(ticker) if ticker in price_change_3m.index else None
                        pc_1m = price_change_1m.get(ticker) if ticker in price_change_1m.index else None

                        in_selloff = False
                        selloff_severity = 'none'
                        if off_high_pct is not None and off_high_pct < -30:
                            in_selloff = True
                            selloff_severity = 'severe'
                        elif off_high_pct is not None and off_high_pct < -20:
                            in_selloff = True
                            selloff_severity = 'moderate'
                        elif pc_3m is not None and not pd.isna(pc_3m) and pc_3m < -15:
                            in_selloff = True
                            selloff_severity = 'recent'

                        row = {
                            **existing,
                            'ticker': ticker,
                            'current_price': round(current_price, 2),
                            'price_source': price_sources_dict.get(ticker),
                            'estimated_value': round(estimated_value, 2) if estimated_value else None,
                            'price_vs_value': round(price_vs_value, 1) if price_vs_value is not None else None,
                            'off_high_pct': round(off_high_pct, 1) if off_high_pct is not None else None,
                            'price_change_1m': round(float(pc_1m), 1) if pc_1m is not None and not pd.isna(pc_1m) else None,
                            'price_change_3m': round(float(pc_3m), 1) if pc_3m is not None and not pd.isna(pc_3m) else None,
                            'in_selloff': in_selloff,
                            'selloff_severity': selloff_severity,
                            'updated': datetime.now().isoformat()
                        }
                        data['valuations'][ticker] = row
                        modified_valuations[ticker] = row
                    except Exception:
                        continue

        except Exception as e:
            try:
                # NOTE: do not re-import activity_log here — a function-level
                # import binds it as a local for the WHOLE function, making every
                # earlier activity_log.log() call raise UnboundLocalError.
                activity_log.log("error", "screener", f"Smart Update price phase error: {str(e)[:50]}")
            except Exception:
                pass

    # Write ONLY the rows this run actually changed. Passing the whole loaded
    # dict re-upserted every row in the index - stamping `updated` on thousands
    # of untouched tickers, which both wasted the write and falsified per-row
    # freshness for every staleness check that reads it.
    if modified_valuations:
        data_manager.bulk_update_valuations(modified_valuations)

    if index_name != 'all':
        save_index_data(index_name, data)

    total_duration = time.time() - start_time
    activity_log.log("success", "screener",
                     f"✓ Smart Update complete in {total_duration:.1f}s "
                     f"({len(modified_valuations)} rows written)")
    log.info(f"=== SMART UPDATE COMPLETE for '{index_name}' in {total_duration:.1f}s ===")

    # Update staleness metadata
    db.set_metadata('last_price_update', datetime.now().isoformat())

    # Phase: Fetch 52-week data for tickers that need it
    if _running:
        _fetch_52_week_data(tickers)

    _progress['status'] = 'complete'
    _running = False


@_clears_running_flag
def run_global_refresh():
    """Global refresh across all indexes."""
    global _running, _progress
    import numpy as np
    from data_manager import get_all_unique_tickers, get_index_data  # Lazy import

    _running = True

    all_tickers_raw = get_all_unique_tickers()
    excluded = load_excluded_tickers()
    excluded_count = 0
    if excluded:
        all_tickers = [t for t in all_tickers_raw if t not in excluded]
        excluded_count = len(all_tickers_raw) - len(all_tickers)
    else:
        all_tickers = all_tickers_raw

    total_tickers = len(all_tickers)
    existing_valuations = data_manager.load_valuations().get('valuations', {})

    _progress = {
        'current': 0,
        'total': total_tickers,
        'ticker': 'Starting...',
        'status': 'running',
        'index': 'all',
        'index_name': 'All Indexes',
        'phase': 'prices'
    }

    activity_log.log("info", "screener", f"Downloading prices for {total_tickers} tickers...")

    current_prices_dict = {}
    price_change_1m_dict = {}
    price_change_3m_dict = {}
    price_sources_dict = {}

    try:
        activity_log.log("info", "screener", f"Fetching 3mo history for {total_tickers} tickers...")
        orchestrator = get_orchestrator()
        history_results = orchestrator.fetch_price_history_batch(all_tickers, period='3mo')
        activity_log.log("success", "screener", f"✓ 3mo history: {len(history_results)} tickers downloaded")

        if not _running:
            _progress['status'] = 'cancelled'
            return

        _progress['phase'] = 'calculating'
        _progress['current'] = total_tickers

        for ticker, result in history_results.items():
            if result.success and result.data:
                hist_data = result.data
                current_prices_dict[ticker] = hist_data.current_price
                if hist_data.change_3m_pct is not None:
                    price_change_3m_dict[ticker] = hist_data.change_3m_pct
                if hist_data.change_1m_pct is not None:
                    price_change_1m_dict[ticker] = hist_data.change_1m_pct

        try:
            provider_prices, provider_sources = orchestrator.fetch_prices(all_tickers, skip_cache=True, return_sources=True)
            for ticker, price in provider_prices.items():
                if price and price > 0:
                    current_prices_dict[ticker] = float(price)
                    price_sources_dict[ticker] = provider_sources.get(ticker)
        except Exception:
            pass

    except Exception as e:
        activity_log.log("error", "screener", f"Error fetching price data: {str(e)[:50]}")

    # Retry failed tickers
    failed_tickers = [t for t in all_tickers if t not in current_prices_dict or
                      (isinstance(current_prices_dict.get(t), float) and np.isnan(current_prices_dict.get(t)))]

    if failed_tickers:
        _progress['phase'] = 'retrying'
        activity_log.log("info", "screener", f"Retrying {len(failed_tickers)} failed tickers...")
        retry_count = 0
        orchestrator = get_orchestrator()
        for i, ticker in enumerate(failed_tickers):
            if not _running:
                break
            if i % 50 == 0:
                _progress['current'] = i
                _progress['ticker'] = f'Retrying... {i}/{len(failed_tickers)}'
                if i > 0:
                    activity_log.log("info", "screener", f"Retrying: {i}/{len(failed_tickers)} ({retry_count} recovered)")

            try:
                result = orchestrator.fetch_price(ticker, skip_cache=True)
                if result.success and result.data:
                    current_prices_dict[ticker] = float(result.data)
                    retry_count += 1
                time.sleep(SCREENER_PRICE_DELAY)
            except Exception:
                pass

        activity_log.log("success", "screener", f"✓ Retry complete: {retry_count} recovered")

    # Build valuations
    _progress['phase'] = 'valuations'
    ticker_valuations = {}
    now_iso = datetime.now().isoformat()

    skip_reasons = {'no_price': [], 'success': [], 'success_no_eps': []}
    orchestrator = get_orchestrator()

    for i, ticker in enumerate(all_tickers):
        if not _running:
            _progress['status'] = 'cancelled'
            return

        if i % 100 == 0:
            _progress['current'] = i
            _progress['ticker'] = f'Building valuations... {i}/{total_tickers}'

        current_price = current_prices_dict.get(ticker)
        if current_price is None or (isinstance(current_price, float) and np.isnan(current_price)):
            skip_reasons['no_price'].append(ticker)
            continue

        price_change_3m = price_change_3m_dict.get(ticker)
        price_change_1m = price_change_1m_dict.get(ticker)
        if price_change_3m is not None and np.isnan(price_change_3m):
            price_change_3m = None
        if price_change_1m is not None and np.isnan(price_change_1m):
            price_change_1m = None

        eps_avg = None
        eps_years = 0
        eps_source = 'none'
        company_name = ticker

        sec_result = orchestrator.fetch_eps(ticker)
        if sec_result.success and sec_result.data and sec_result.data.eps_history:
            # Read back split-adjusted history (the fresh fetch just persisted
            # raw values) so global refresh can't reintroduce pre-split EPS
            # averages (e.g. BKNG 25:1). Fall back to the raw list if the
            # table wasn't populated.
            adjusted = get_split_adjusted_eps_history(ticker)
            if not adjusted:
                adjusted = [dict(e) for e in sec_result.data.eps_history]
            calc_avg, years_used = average_split_adjusted_eps(adjusted)
            if calc_avg is not None:
                eps_avg = calc_avg
                eps_years = years_used
                eps_source = 'sec'
                company_name = sec_result.data.company_name or ticker

        existing = existing_valuations.get(ticker, {})
        if eps_avg is None and existing.get('eps_avg'):
            eps_avg = existing['eps_avg']
            eps_years = existing.get('eps_years', 0)
            eps_source = existing.get('eps_source', 'cached')

        if existing.get('company_name'):
            company_name = existing['company_name']

        fifty_two_week_high = existing.get('fifty_two_week_high', 0)
        fifty_two_week_low = existing.get('fifty_two_week_low', 0)
        annual_dividend = existing.get('annual_dividend', 0)

        estimated_value, price_vs_value = compute_estimated_value(
            eps_avg, annual_dividend, current_price
        )

        off_high_pct = None
        if fifty_two_week_high and current_price:
            off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

        in_selloff = False
        selloff_severity = 'none'
        if off_high_pct and off_high_pct < -30:
            in_selloff = True
            selloff_severity = 'severe'
        elif off_high_pct and off_high_pct < -20:
            in_selloff = True
            selloff_severity = 'moderate'
        elif price_change_3m and price_change_3m < -15:
            in_selloff = True
            selloff_severity = 'recent'

        ticker_valuations[ticker] = {
            'ticker': ticker,
            'company_name': company_name,
            'current_price': round(current_price, 2) if current_price else None,
            'price_source': price_sources_dict.get(ticker),
            'eps_avg': round(eps_avg, 2) if eps_avg is not None else None,
            'eps_years': eps_years,
            'eps_source': eps_source,
            'has_enough_years': eps_years >= 8,
            'annual_dividend': round(annual_dividend, 2) if annual_dividend else 0,
            'estimated_value': round(estimated_value, 2) if estimated_value else None,
            'price_vs_value': round(price_vs_value, 1) if price_vs_value is not None else None,
            'fifty_two_week_high': fifty_two_week_high,
            'fifty_two_week_low': fifty_two_week_low,
            'off_high_pct': round(off_high_pct, 1) if off_high_pct is not None else None,
            'price_change_1m': round(price_change_1m, 1) if price_change_1m is not None else None,
            'price_change_3m': round(price_change_3m, 1) if price_change_3m is not None else None,
            'in_selloff': in_selloff,
            'selloff_severity': selloff_severity,
            'updated': now_iso
        }

        if eps_avg is not None:
            skip_reasons['success'].append(ticker)
        else:
            skip_reasons['success_no_eps'].append(ticker)

    _progress['current'] = total_tickers
    _progress['phase'] = 'saving'

    data_manager.bulk_update_valuations(ticker_valuations)

    ticker_status_updates = {}
    for ticker, val in ticker_valuations.items():
        sec_status = ('available'
                          if str(val.get('eps_source') or '').startswith('sec')
                          else 'unavailable')
        ticker_status_updates[ticker] = {
            'sec_status': sec_status,
            'valuation_updated': now_iso,
            'company_name': val.get('company_name')
        }
    data_manager.bulk_update_ticker_status(ticker_status_updates)

    successful_tickers = skip_reasons['success'] + skip_reasons['success_no_eps']
    if skip_reasons['no_price'] or successful_tickers:
        record_ticker_failures(skip_reasons['no_price'], successful_tickers)

    skip_summary = {
        'last_refresh': now_iso,
        'total_tickers': total_tickers,
        'excluded_count': excluded_count,
        'no_price_data': len(skip_reasons['no_price']),
        'no_eps_data': len(skip_reasons['success_no_eps']),
        'full_data': len(skip_reasons['success']),
    }
    try:
        db.set_metadata('refresh_summary', json.dumps(skip_summary))
    except Exception:
        pass

    # Update staleness metadata
    db.set_metadata('last_price_update', now_iso)

    # Phase: Fetch 52-week data for tickers that need it
    if _running:
        _fetch_52_week_data(all_tickers)

    _progress['status'] = 'complete'
    _progress['ticker'] = f'Done - {len(ticker_valuations)} valuations updated'
    _running = False
    activity_log.log("success", "screener", f"Refresh complete - {len(ticker_valuations)} valuations saved")


# =============================================================================
# 52-WEEK DATA FETCH PHASE
# =============================================================================

def _fetch_52_week_data(tickers, progress_callback=None):
    """
    Fetch 52-week high/low data for tickers that are missing it.
    Runs with rate limiting to avoid API limits.

    Args:
        tickers: List of ticker symbols to fetch
        progress_callback: Optional function to update progress

    Returns:
        Dict of {ticker: {'fifty_two_week_high': float, 'fifty_two_week_low': float}}
    """
    global _running, _progress

    orchestrator = get_orchestrator()
    results = {}

    # Get existing valuations to check which tickers need 52-week data
    existing_valuations = data_manager.load_valuations().get('valuations', {})

    # Select tickers to fetch. Previously this took every ticker with no stored
    # high - which meant tickers that had FAILED were retried on every run
    # (including every 15-minute scheduled refresh) with no backoff, while
    # tickers that succeeded were never refreshed again even years later.
    checks = db.get_fetch_checks('52w', tickers)
    now = datetime.now()
    refresh_cutoff = now - timedelta(days=FIFTY_TWO_WEEK_REFRESH_DAYS)
    retry_cutoff = now - timedelta(days=FIFTY_TWO_WEEK_RETRY_DAYS)

    def _checked_at(ticker):
        stamp = (checks.get(ticker) or {}).get('checked_at')
        if not stamp:
            return None
        try:
            return datetime.fromisoformat(stamp)
        except (ValueError, TypeError):
            return None

    tickers_needing_data = []
    for ticker in tickers:
        existing = existing_valuations.get(ticker, {})
        has_value = bool(existing.get('fifty_two_week_high'))
        checked_at = _checked_at(ticker)
        last_ok = (checks.get(ticker) or {}).get('ok')

        if has_value:
            # Refresh on a cadence: a 52-week high genuinely moves over time.
            if checked_at is None or checked_at < refresh_cutoff:
                tickers_needing_data.append(ticker)
        else:
            # No value yet. Back off if we tried recently and it failed.
            if checked_at is not None and not last_ok and checked_at > retry_cutoff:
                continue
            tickers_needing_data.append(ticker)

    if not tickers_needing_data:
        activity_log.log("info", "screener", "52-week data current for all tickers")
        return results

    activity_log.log("info", "screener", f"Fetching 52-week data for {len(tickers_needing_data)} tickers...")

    _progress['phase'] = '52-week'
    _progress['current'] = 0
    _progress['total'] = len(tickers_needing_data)

    attempted = []
    for i, ticker in enumerate(tickers_needing_data):
        if not _running:
            activity_log.log("info", "screener", "52-week fetch cancelled")
            break

        _progress['current'] = i + 1
        _progress['ticker'] = f"52-week: {ticker}"

        attempted.append(ticker)
        try:
            info_result = orchestrator.fetch_stock_info(ticker)
            if info_result.success and info_result.data:
                fifty_two_week_high = info_result.data.fifty_two_week_high
                fifty_two_week_low = info_result.data.fifty_two_week_low

                if fifty_two_week_high:
                    results[ticker] = {
                        'fifty_two_week_high': fifty_two_week_high,
                        'fifty_two_week_low': fifty_two_week_low
                    }
        except Exception as e:
            activity_log.log("warning", "screener", f"52-week fetch failed for {ticker}: {str(e)[:30]}")
            continue

    # Record what we attempted so successes refresh on a cadence and failures
    # back off, rather than every failing ticker being retried on every run.
    # (The orchestrator's provider rate limiter paces the loop above; the old
    # fixed 0.5s sleep per ticker stacked on top of it.)
    if attempted:
        succeeded = [t for t in attempted if t in results]
        failed = [t for t in attempted if t not in results]
        db.record_fetch_checks('52w', succeeded, ok=True)
        db.record_fetch_checks('52w', failed, ok=False)

    # Update database with 52-week data
    if results:
        now_iso = datetime.now().isoformat()
        updates = {}
        for ticker, data in results.items():
            existing = existing_valuations.get(ticker, {})
            current_price = existing.get('current_price', 0)
            fifty_two_week_high = data['fifty_two_week_high']

            # Calculate off_high_pct
            off_high_pct = None
            if fifty_two_week_high and current_price and fifty_two_week_high > 0:
                off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

            updates[ticker] = {
                **existing,
                'fifty_two_week_high': round(fifty_two_week_high, 2) if fifty_two_week_high else None,
                'fifty_two_week_low': round(data['fifty_two_week_low'], 2) if data.get('fifty_two_week_low') else None,
                'off_high_pct': round(off_high_pct, 1) if off_high_pct is not None else None,
                'updated': now_iso
            }

        data_manager.bulk_update_valuations(updates)
        activity_log.log("success", "screener", f"Updated 52-week data for {len(updates)} tickers")
    else:
        # All fetches came back empty (commonly yfinance throttling .info
        # right after a large batch download). Don't fail silently — the
        # missing data looks identical to "phase never ran" otherwise.
        activity_log.log(
            "warning", "screener",
            f"52-week fetch found no data for any of {len(tickers_needing_data)} tickers "
            "(provider may be rate-limiting; will retry next update)"
        )

    return results


# =============================================================================
# SINGLE TICKER REFRESH
# =============================================================================

def refresh_single_ticker(symbol):
    """
    Refresh all data for a single ticker.
    Returns dict with updated valuation or error.
    """
    from services.valuation import calculate_valuation
    from data_manager import save_single_valuation

    activity_log.log('info', 'screener', f'Refreshing single ticker: {symbol}')

    try:
        # Full valuation calculation (price + EPS + dividends)
        valuation = calculate_valuation(symbol)

        if not valuation:
            return {'success': False, 'error': f'Failed to get data for {symbol}'}

        if valuation.get('current_price', 0) <= 0:
            return {'success': False, 'error': f'No price data for {symbol}'}

        # Save to database
        save_single_valuation(symbol, valuation)

        activity_log.log('info', 'screener', f'Refreshed {symbol}: ${valuation.get("current_price", 0):.2f}')

        return {'success': True, 'data': valuation}

    except Exception as e:
        activity_log.log('error', 'screener', f'Error refreshing {symbol}: {e}')
        return {'success': False, 'error': str(e)}


# =============================================================================
# CONVENIENCE CLASS
# =============================================================================

class ScreenerService:
    """Service class for screener operations."""

    def start_screener(self, index_name='all'):
        """Start full screener update."""
        if is_running():
            return False
        thread = threading.Thread(target=run_screener, args=(index_name,))
        thread.daemon = True
        thread.start()
        return True

    def start_quick_update(self, index_name='all'):
        """Start quick price-only update."""
        if is_running():
            return False
        thread = threading.Thread(target=run_quick_price_update, args=(index_name,))
        thread.daemon = True
        thread.start()
        return True

    def start_smart_update(self, index_name='all'):
        """Start smart selective update."""
        if is_running():
            return False
        thread = threading.Thread(target=run_smart_update, args=(index_name,))
        thread.daemon = True
        thread.start()
        return True

    def start_global_refresh(self):
        """Start global refresh."""
        if is_running():
            return False
        thread = threading.Thread(target=run_global_refresh)
        thread.daemon = True
        thread.start()
        return True

    def stop(self):
        """Stop the running screener."""
        stop()

    def get_progress(self):
        """Get current progress."""
        return get_progress()

    def is_running(self):
        """Check if screener is running."""
        return is_running()

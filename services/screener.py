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
import tempfile
import fcntl
import math
from datetime import datetime, timedelta

import database as db
import data_manager
import sec_data
from config import (
    PE_RATIO_MULTIPLIER, FAILURE_THRESHOLD,
    SCREENER_DIVIDEND_BACKOFF, SCREENER_TICKER_PAUSE, SCREENER_PRICE_DELAY
)
from logger import log, log_error
from services.providers import get_orchestrator
from services.valuation import get_validated_eps
from services.indexes import (
    VALID_INDICES, INDIVIDUAL_INDICES, INDEX_NAMES,
    fetch_index_tickers
)

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

# Provider logging - using temp file for cross-process sharing (Flask debug reloader)
PROVIDER_LOG_FILE = os.path.join(tempfile.gettempdir(), 'finance_provider_logs.txt')
PROVIDER_LOG_MAX_LINES = 10


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
# PROVIDER LOGGING
# =============================================================================

def log_provider_activity(message: str):
    """Log provider activity for UI display (shared across Flask processes)."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(f"[ProviderLog] {log_entry}", flush=True)

    try:
        with open(PROVIDER_LOG_FILE, 'a+') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.seek(0)
            lines = f.readlines()
            lines.append(log_entry + '\n')
            lines = lines[-PROVIDER_LOG_MAX_LINES:]
            f.seek(0)
            f.truncate()
            f.writelines(lines)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        print(f"[ProviderLog] Error writing to log file: {e}", flush=True)


def get_provider_logs():
    """Read provider logs from shared file."""
    try:
        if os.path.exists(PROVIDER_LOG_FILE):
            with open(PROVIDER_LOG_FILE, 'r') as f:
                return [line.strip() for line in f.readlines() if line.strip()]
    except Exception:
        pass
    return []


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

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


def get_all_unique_tickers():
    """Get all unique tickers across all enabled indexes (deduplicated)."""
    all_tickers = set()
    enabled_indexes = db.get_enabled_indexes()
    for index_name in INDIVIDUAL_INDICES:
        if index_name in enabled_indexes:
            tickers = db.get_active_index_tickers(index_name)
            all_tickers.update(tickers)
    return sorted(list(all_tickers))


def get_index_data(index_name='all'):
    """Load index data from database."""
    if index_name not in VALID_INDICES:
        index_name = 'all'

    valuations_data = data_manager.load_valuations()
    all_valuations = valuations_data.get('valuations', {})
    last_updated = valuations_data.get('last_updated')

    if index_name == 'all':
        all_tickers = get_all_unique_tickers()
        return {
            'name': 'All Indexes',
            'short_name': 'All',
            'tickers': all_tickers,
            'valuations': all_valuations,
            'last_updated': last_updated
        }

    tickers = db.get_active_index_tickers(index_name)

    if not tickers:
        print(f"[Index] No tickers in database for {index_name}, fetching from web...")
        tickers = fetch_index_tickers(index_name)
        if tickers:
            db.refresh_index_membership(index_name, tickers)

    name, short_name = INDEX_NAMES.get(index_name, (index_name, index_name))
    index_tickers = set(tickers)
    filtered_valuations = {
        ticker: val for ticker, val in all_valuations.items()
        if ticker in index_tickers
    }

    return sanitize_for_json({
        'name': name,
        'short_name': short_name,
        'tickers': tickers,
        'valuations': filtered_valuations,
        'last_updated': last_updated
    })


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
    """Record ticker failures and clear successes."""
    newly_excluded = []
    for ticker in failed_tickers:
        db.record_ticker_failure(ticker)
        failure = db.get_ticker_failure(ticker)
        if failure and failure.get('failure_count', 0) >= FAILURE_THRESHOLD:
            newly_excluded.append(ticker)
    for ticker in successful_tickers:
        db.clear_ticker_failure(ticker)
    return newly_excluded


def get_all_ticker_indexes():
    """Get a mapping of all tickers to their enabled indexes."""
    result = {}
    enabled_indexes = db.get_enabled_indexes()
    for index_name in INDIVIDUAL_INDICES:
        if index_name in enabled_indexes:
            tickers = db.get_active_index_tickers(index_name)
            short_name = INDEX_NAMES.get(index_name, (index_name, index_name))[1]
            for ticker in tickers:
                if ticker not in result:
                    result[ticker] = []
                result[ticker].append(short_name)
    return result


def calculate_valuation(ticker):
    """Calculate valuation for a single ticker."""
    try:
        orchestrator = get_orchestrator()

        info_result = orchestrator.fetch_stock_info(ticker)
        if info_result.success and info_result.data:
            info_data = info_result.data
            company_name = info_data.company_name
            fifty_two_week_high = info_data.fifty_two_week_high or 0
            fifty_two_week_low = info_data.fifty_two_week_low or 0
        else:
            company_name = ticker
            fifty_two_week_high = 0
            fifty_two_week_low = 0

        price_result = orchestrator.fetch_price(ticker)
        current_price = price_result.data if price_result.success else 0
        price_source = price_result.source if price_result.success else 'none'

        off_high_pct = None
        if fifty_two_week_high and current_price:
            off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

        history_result = orchestrator.fetch_price_history(ticker, period='3mo')
        price_change_1m = None
        price_change_3m = None
        if history_result.success and history_result.data:
            price_data = history_result.data
            price_change_1m = price_data.change_1m_pct
            price_change_3m = price_data.change_3m_pct

        eps_data, eps_source, validation_info = get_validated_eps(ticker)

        if eps_source.startswith('sec'):
            sec_eps = sec_data.get_sec_eps(ticker)
            if sec_eps and sec_eps.get('company_name'):
                company_name = sec_eps['company_name']

        dividend_result = orchestrator.fetch_dividends(ticker)
        annual_dividend = 0
        last_dividend = None
        last_dividend_date = None

        if dividend_result.success and dividend_result.data:
            dividend_data_obj = dividend_result.data
            annual_dividend = dividend_data_obj.annual_dividend
            if dividend_data_obj.payments and len(dividend_data_obj.payments) > 0:
                last_payment = dividend_data_obj.payments[-1]
                last_dividend_date = last_payment['date']
                last_dividend = round(float(last_payment['amount']), 4)

        eps_avg = None
        estimated_value = None
        price_vs_value = None

        if len(eps_data) > 0 and current_price:
            eps_avg = sum(e['eps'] for e in eps_data) / len(eps_data)
            estimated_value = (eps_avg + annual_dividend) * 10
            price_vs_value = ((current_price - estimated_value) / estimated_value) * 100 if estimated_value > 0 else None

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

        return {
            'ticker': ticker,
            'company_name': company_name,
            'current_price': round(current_price, 2),
            'price_source': price_source,
            'eps_avg': round(eps_avg, 2) if eps_avg is not None else None,
            'eps_years': len(eps_data),
            'eps_source': eps_source,
            'has_enough_years': len(eps_data) >= 8,
            'annual_dividend': round(annual_dividend, 2),
            'last_dividend': last_dividend,
            'last_dividend_date': last_dividend_date,
            'estimated_value': round(estimated_value, 2) if estimated_value else None,
            'price_vs_value': round(price_vs_value, 1) if price_vs_value else None,
            'fifty_two_week_high': round(fifty_two_week_high, 2) if fifty_two_week_high else None,
            'fifty_two_week_low': round(fifty_two_week_low, 2) if fifty_two_week_low else None,
            'off_high_pct': round(off_high_pct, 1) if off_high_pct else None,
            'price_change_1m': round(price_change_1m, 1) if price_change_1m else None,
            'price_change_3m': round(price_change_3m, 1) if price_change_3m else None,
            'in_selloff': in_selloff,
            'selloff_severity': selloff_severity,
            'updated': datetime.now().isoformat()
        }
    except Exception as e:
        print(f"Error calculating valuation for {ticker}: {e}")
    return None


# =============================================================================
# MAIN SCREENER FUNCTIONS
# =============================================================================

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
    phase1_start = time.time()
    _progress['phase'] = 'eps'
    _progress['ticker'] = 'Loading SEC EPS data...'

    eps_results = {}
    sec_hits = 0

    for i, t in enumerate(tickers):
        if i % 100 == 0:
            _progress['current'] = i
            _progress['ticker'] = f'Loading SEC EPS... ({sec_hits} found)'

        sec_eps = sec_data.get_sec_eps(t)
        if sec_eps and sec_eps.get('eps_history'):
            eps_history = sec_eps['eps_history']
            if len(eps_history) > 0:
                eps_avg = sum(e['eps'] for e in eps_history) / len(eps_history)
                eps_results[t] = {
                    'ticker': t,
                    'company_name': sec_eps.get('company_name', t),
                    'eps_avg': round(eps_avg, 2),
                    'eps_years': len(eps_history),
                    'eps_source': 'sec',
                    'has_enough_years': len(eps_history) >= 8,
                    'annual_dividend': existing_valuations.get(t, {}).get('annual_dividend', 0),
                }
                sec_hits += 1
                continue

        existing = existing_valuations.get(t, {})
        if existing.get('eps_avg') is not None:
            eps_results[t] = existing

    _progress['current'] = len(tickers)
    log.info(f"Screener Phase 1 complete: {time.time() - phase1_start:.1f}s, SEC EPS: {sec_hits} found")

    if not _running:
        _progress['status'] = 'cancelled'
        _running = False
        return

    # Phase 2: Dividends
    four_months_ago = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')

    def needs_dividend_update(ticker):
        existing = existing_valuations.get(ticker, {})
        eps_info = eps_results.get(ticker, {})
        annual_div = existing.get('annual_dividend') or eps_info.get('annual_dividend')
        if not annual_div:
            return True
        last_date = existing.get('last_dividend_date') or eps_info.get('last_dividend_date')
        if last_date and last_date < four_months_ago:
            return True
        return False

    tickers_needing_dividends = [t for t in tickers if needs_dividend_update(t)]
    dividend_data = {}

    if tickers_needing_dividends:
        log.info(f"Screener Phase 2: Fetching dividends for {len(tickers_needing_dividends)} tickers...")
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
            except Exception:
                pass
            time.sleep(backoff_delay)

        _progress['current'] = len(tickers_needing_dividends)
        log.info(f"Screener Phase 2 complete: found dividends for {dividend_count} tickers")

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
        log_provider_activity(f"Fetching 3mo history for {len(tickers)} tickers...")
        orchestrator = get_orchestrator()
        history_results = orchestrator.fetch_price_history_batch(tickers, period='3mo')
        log_provider_activity(f"✓ 3mo history: {len(history_results)} tickers downloaded")

        failed_tickers = [t for t in tickers if t not in history_results]
        if failed_tickers:
            db.mark_tickers_delisted(failed_tickers)

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

    # Override with real-time prices
    try:
        orchestrator = get_orchestrator()
        provider_prices, provider_sources = orchestrator.fetch_prices(tickers, skip_cache=True, return_sources=True)
        for ticker, price in provider_prices.items():
            if price and price > 0:
                current_prices_dict[ticker] = float(price)
                price_sources_dict[ticker] = provider_sources.get(ticker)
    except Exception:
        pass

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

        eps_info = eps_results.get(ticker) or existing_valuations.get(ticker, {})
        eps_avg = eps_info.get('eps_avg')

        div_info = dividend_data.get(ticker, {})
        annual_dividend = (
            div_info.get('annual_dividend') or
            eps_info.get('annual_dividend') or
            existing_valuations.get(ticker, {}).get('annual_dividend') or
            0
        )

        if eps_info.get('company_name'):
            company_name = eps_info['company_name']

        estimated_value = None
        price_vs_value = None
        if eps_avg and eps_avg > 0:
            estimated_value = (eps_avg + annual_dividend) * 10
            if estimated_value > 0:
                price_vs_value = ((current_price - estimated_value) / estimated_value) * 100

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
            'off_high_pct': round(off_high_pct, 1) if off_high_pct else None,
            'price_change_1m': round(price_change_1m, 1) if price_change_1m else None,
            'price_change_3m': round(price_change_3m, 1) if price_change_3m else None,
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
            sec_status = 'available' if val.get('eps_source') == 'sec' else 'unavailable'
            ticker_status_updates[ticker] = {
                'sec_status': sec_status,
                'valuation_updated': now_iso,
                'company_name': val.get('company_name')
            }
        data_manager.bulk_update_ticker_status(ticker_status_updates)

    if index_name != 'all':
        save_index_data(index_name, data)

    total_duration = time.time() - start_time
    log.info(f"=== SCREENER COMPLETE for '{index_name}': {len(valuations_batch)} valuations in {total_duration:.1f}s ===")
    _progress['status'] = 'complete'
    _running = False


def run_quick_price_update(index_name='all'):
    """Fast update - batch download prices only, reuse cached EPS data."""
    global _running, _progress, _current_index
    import numpy as np
    import pandas as pd

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

    try:
        log_provider_activity(f"Fetching 3mo history for {len(tickers)} tickers...")
        orchestrator = get_orchestrator()
        history_results = orchestrator.fetch_price_history_batch(tickers, period='3mo')
        log_provider_activity(f"✓ 3mo history: {len(history_results)} tickers downloaded")

        if not history_results:
            _progress['status'] = 'complete'
            _running = False
            return

        failed_tickers = [t for t in tickers if t not in history_results]
        if failed_tickers:
            db.mark_tickers_delisted(failed_tickers)

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
        try:
            provider_prices, provider_sources = orchestrator.fetch_prices(tickers, skip_cache=True, return_sources=True)
            for ticker, price in provider_prices.items():
                if price and price > 0:
                    current_prices_dict[ticker] = float(price)
                    price_sources_dict[ticker] = provider_sources.get(ticker)
            current_prices = pd.Series(current_prices_dict)
        except Exception:
            pass

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
                annual_dividend = existing.get('annual_dividend', 0)
                fifty_two_week_high = existing.get('fifty_two_week_high')
                company_name = existing.get('company_name', ticker)

                off_high_pct = None
                if fifty_two_week_high and fifty_two_week_high > 0:
                    off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

                estimated_value = existing.get('estimated_value')
                price_vs_value = None
                if eps_avg and eps_avg > 0:
                    estimated_value = (eps_avg + annual_dividend) * 10
                    if estimated_value > 0:
                        price_vs_value = ((current_price - estimated_value) / estimated_value) * 100

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

                valuations_batch[ticker] = {
                    **existing,
                    'ticker': ticker,
                    'company_name': company_name,
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
                updated_count += 1
            except Exception:
                continue

        _progress['current'] = len(tickers)

        if valuations_batch:
            data_manager.bulk_update_valuations(valuations_batch)

    except Exception as e:
        log_error(f"Quick Update failed", e)

    total_duration = time.time() - start_time
    log.info(f"=== QUICK PRICE UPDATE COMPLETE for '{index_name}': {updated_count} updated in {total_duration:.1f}s ===")
    _progress['status'] = 'complete'
    _running = False


def run_smart_update(index_name='all'):
    """Smart update - prioritizes missing tickers, then updates prices for existing ones."""
    global _running, _progress, _current_index
    import pandas as pd

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

    # Phase 1: Fetch full valuations for missing tickers
    for i, ticker in enumerate(missing_tickers):
        if not _running:
            _progress['status'] = 'cancelled'
            break

        _progress['current'] = i + 1
        _progress['ticker'] = f"[NEW] {ticker}"
        _progress['phase'] = 'missing'

        valuation = calculate_valuation(ticker)
        if valuation and valuation.get('current_price', 0) > 0:
            data['valuations'][ticker] = valuation
            if (i + 1) % 10 == 0:
                save_index_data(index_name, data)
        else:
            db.mark_ticker_delisted(ticker)

        time.sleep(SCREENER_TICKER_PAUSE)

    # Phase 2: Quick price update for existing tickers
    if _running and existing_tickers:
        _progress['phase'] = 'prices'
        _progress['ticker'] = 'Batch downloading prices...'

        try:
            log_provider_activity(f"Fetching 3mo history for {len(existing_tickers)} existing tickers...")
            orchestrator = get_orchestrator()
            history_results = orchestrator.fetch_price_history_batch(existing_tickers, period='3mo')
            log_provider_activity(f"✓ 3mo history: {len(history_results)} tickers downloaded")

            failed_tickers = [t for t in existing_tickers if t not in history_results]
            if failed_tickers:
                db.mark_tickers_delisted(failed_tickers)

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

                        estimated_value = existing.get('estimated_value')
                        price_vs_value = None
                        if eps_avg and eps_avg > 0:
                            estimated_value = (eps_avg + annual_dividend) * 10
                            if estimated_value > 0:
                                price_vs_value = ((current_price - estimated_value) / estimated_value) * 100

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

                        data['valuations'][ticker] = {
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
                    except Exception:
                        continue

        except Exception as e:
            print(f"[Smart Update] Error in price phase: {e}")

    if data.get('valuations'):
        data_manager.bulk_update_valuations(data['valuations'])

    if index_name != 'all':
        save_index_data(index_name, data)

    total_duration = time.time() - start_time
    log.info(f"=== SMART UPDATE COMPLETE for '{index_name}' in {total_duration:.1f}s ===")
    _progress['status'] = 'complete'
    _running = False


def run_global_refresh():
    """Global refresh across all indexes."""
    global _running, _progress
    import numpy as np

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

    print(f"[Refresh] Downloading prices for {total_tickers} tickers...")

    current_prices_dict = {}
    price_change_1m_dict = {}
    price_change_3m_dict = {}
    price_sources_dict = {}

    try:
        log_provider_activity(f"Fetching 3mo history for {total_tickers} tickers...")
        orchestrator = get_orchestrator()
        history_results = orchestrator.fetch_price_history_batch(all_tickers, period='3mo')
        log_provider_activity(f"✓ 3mo history: {len(history_results)} tickers downloaded")

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
        print(f"[Refresh] Error fetching price data: {e}")

    # Retry failed tickers
    failed_tickers = [t for t in all_tickers if t not in current_prices_dict or
                      (isinstance(current_prices_dict.get(t), float) and np.isnan(current_prices_dict.get(t)))]

    if failed_tickers:
        _progress['phase'] = 'retrying'
        retry_count = 0
        orchestrator = get_orchestrator()
        for i, ticker in enumerate(failed_tickers):
            if not _running:
                break
            if i % 50 == 0:
                _progress['current'] = i
                _progress['ticker'] = f'Retrying... {i}/{len(failed_tickers)}'

            try:
                result = orchestrator.fetch_price(ticker, skip_cache=True)
                if result.success and result.data:
                    current_prices_dict[ticker] = float(result.data)
                    retry_count += 1
                time.sleep(SCREENER_PRICE_DELAY)
            except Exception:
                pass

    # Build valuations
    _progress['phase'] = 'valuations'
    ticker_valuations = {}
    now_iso = datetime.now().isoformat()

    skip_reasons = {'no_price': [], 'success': [], 'success_no_eps': []}

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

        sec_eps = sec_data.get_sec_eps(ticker)
        if sec_eps and sec_eps.get('eps_history'):
            eps_history = sec_eps['eps_history']
            if len(eps_history) > 0:
                eps_avg = sum(e['eps'] for e in eps_history) / len(eps_history)
                eps_years = len(eps_history)
                eps_source = 'sec'
                company_name = sec_eps.get('company_name', ticker)

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

        estimated_value = None
        price_vs_value = None
        if eps_avg and eps_avg > 0:
            estimated_value = (eps_avg + annual_dividend) * 10
            if current_price and estimated_value > 0:
                price_vs_value = ((current_price - estimated_value) / estimated_value) * 100

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
            'eps_avg': round(eps_avg, 2) if eps_avg else None,
            'eps_years': eps_years,
            'eps_source': eps_source,
            'has_enough_years': eps_years >= 8,
            'annual_dividend': round(annual_dividend, 2) if annual_dividend else 0,
            'estimated_value': round(estimated_value, 2) if estimated_value else None,
            'price_vs_value': round(price_vs_value, 1) if price_vs_value else None,
            'fifty_two_week_high': fifty_two_week_high,
            'fifty_two_week_low': fifty_two_week_low,
            'off_high_pct': round(off_high_pct, 1) if off_high_pct else None,
            'price_change_1m': round(price_change_1m, 1) if price_change_1m else None,
            'price_change_3m': round(price_change_3m, 1) if price_change_3m else None,
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
        sec_status = 'available' if val.get('eps_source') == 'sec' else 'unavailable'
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

    _progress['status'] = 'complete'
    _progress['ticker'] = f'Done - {len(ticker_valuations)} valuations updated'
    _running = False
    print(f"[Refresh] Complete - {len(ticker_valuations)} valuations saved")


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

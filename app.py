from flask import Flask, render_template, jsonify, request, Response
import os
import time
import json
from datetime import datetime, timedelta
import math
import threading
import sec_data
import data_manager
import database as db
from logger import log, log_yahoo_fetch, log_screener_progress, log_error
from services.providers import (
    init_providers, get_orchestrator, get_registry,
    get_config, update_config, get_provider_order, set_provider_order,
    get_secret, set_secret, has_secret,
    has_fmp_api_key, has_alpaca_credentials, get_alpaca_api_endpoint,
    validate_fmp_api_key, validate_alpaca_api_key,
    set_alpaca_credentials,
    get_disabled_providers, enable_provider, disable_provider,
    disconnect_ibkr
)
from services.valuation import get_validated_eps
from services.holdings import calculate_holdings, calculate_fifo_cost_basis, get_transactions, get_stocks
from services.recommendations import get_top_recommendations
from config import (
    DATA_DIR, USER_DATA_DIR, EXCLUDED_TICKERS_FILE, TICKER_FAILURES_FILE,
    STOCKS_FILE, TRANSACTIONS_FILE,
    PRICE_CACHE_DURATION, FAILURE_THRESHOLD,
    PE_RATIO_MULTIPLIER, RECOMMENDED_EPS_YEARS,
    YAHOO_BATCH_SIZE, YAHOO_BATCH_DELAY, YAHOO_SINGLE_DELAY,
    DIVIDEND_NO_DIVIDEND_PENALTY, DIVIDEND_POINTS_PER_PERCENT, DIVIDEND_MAX_POINTS,
    SELLOFF_SEVERE_BONUS, SELLOFF_MODERATE_BONUS, SELLOFF_RECENT_BONUS,
    SELLOFF_VOLUME_SEVERE, SELLOFF_VOLUME_HIGH, SELLOFF_VOLUME_MODERATE,
    SCORING_WEIGHTS, RECOMMENDATION_MIN_EPS_YEARS
)
from services.indexes import (
    VALID_INDICES, INDIVIDUAL_INDICES, INDEX_NAMES,
    fetch_index_tickers, IndexRegistry
)

app = Flask(__name__)

# Blueprint registration enabled - routes now use database and proper response formats
from routes import register_blueprints
register_blueprints(app)

# Flag to prevent multiple startup checks
startup_check_done = False

# Screener state - now managed by services/screener.py
# These are kept for backward compatibility with any code still referencing them
from services import screener as screener_service
from services.screener import (
    log_provider_activity, get_provider_logs,
    run_screener, run_quick_price_update, run_smart_update, run_global_refresh
)

# Excluded tickers management (delisted/unavailable)
# Uses failure counting - only excludes after FAILURE_THRESHOLD consecutive failures
# Constants imported from config.py: EXCLUDED_TICKERS_FILE, TICKER_FAILURES_FILE, FAILURE_THRESHOLD

def load_ticker_failures():
    """Load ticker failure counts"""
    if os.path.exists(TICKER_FAILURES_FILE):
        try:
            with open(TICKER_FAILURES_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_ticker_failures(failures):
    """Save ticker failure counts"""
    try:
        with open(TICKER_FAILURES_FILE, 'w') as f:
            json.dump(failures, f)
    except Exception as e:
        print(f"[Failures] Error saving: {e}")

def load_excluded_tickers():
    """Load excluded tickers from cache file"""
    if os.path.exists(EXCLUDED_TICKERS_FILE):
        try:
            with open(EXCLUDED_TICKERS_FILE, 'r') as f:
                data = json.load(f)
                return set(data.get('tickers', []))
        except Exception as e:
            print(f"[Excluded] Error loading excluded tickers: {e}")
    return set()

def save_excluded_tickers(tickers, reason='no_price_data'):
    """Save excluded tickers to cache file"""
    try:
        data = {
            'tickers': sorted(list(tickers)),
            'count': len(tickers),
            'reason': reason,
            'updated': datetime.now().isoformat()
        }
        with open(EXCLUDED_TICKERS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"[Excluded] Saved {len(tickers)} excluded tickers")
    except Exception as e:
        print(f"[Excluded] Error saving excluded tickers: {e}")

def record_ticker_failures(failed_tickers, successful_tickers):
    """
    Record ticker failures and update excluded list.
    Only excludes after FAILURE_THRESHOLD consecutive failures.
    Resets count for successful tickers.
    """
    failures = load_ticker_failures()
    excluded = load_excluded_tickers()
    newly_excluded = []

    # Increment failure counts
    for ticker in failed_tickers:
        failures[ticker] = failures.get(ticker, 0) + 1
        if failures[ticker] >= FAILURE_THRESHOLD and ticker not in excluded:
            excluded.add(ticker)
            newly_excluded.append(ticker)

    # Reset counts for successful tickers
    for ticker in successful_tickers:
        if ticker in failures:
            del failures[ticker]
        # Also remove from excluded if it was there (ticker recovered)
        if ticker in excluded:
            excluded.discard(ticker)
            print(f"[Excluded] Removed {ticker} from excluded (now has data)")

    save_ticker_failures(failures)
    if excluded:
        save_excluded_tickers(excluded)

    if newly_excluded:
        print(f"[Excluded] Newly excluded after {FAILURE_THRESHOLD} failures: {len(newly_excluded)} tickers")

    return newly_excluded

def clear_excluded_tickers():
    """Clear the excluded tickers list and failure counts"""
    if os.path.exists(EXCLUDED_TICKERS_FILE):
        os.remove(EXCLUDED_TICKERS_FILE)
    if os.path.exists(TICKER_FAILURES_FILE):
        os.remove(TICKER_FAILURES_FILE)
    print("[Excluded] Cleared excluded tickers list and failure counts")

def get_excluded_tickers_info():
    """Get info about excluded tickers"""
    result = {'tickers': [], 'count': 0, 'reason': 'none', 'updated': None, 'pending_failures': 0}
    if os.path.exists(EXCLUDED_TICKERS_FILE):
        try:
            with open(EXCLUDED_TICKERS_FILE, 'r') as f:
                data = json.load(f)
                result.update(data)
        except Exception:
            pass
    # Also report how many are pending (have failures but not yet excluded)
    failures = load_ticker_failures()
    pending = sum(1 for t, c in failures.items() if c < FAILURE_THRESHOLD)
    result['pending_failures'] = pending
    return result

def fetch_stock_price(ticker):
    """Fetch current stock price using the provider system."""
    orchestrator = get_orchestrator()
    result = orchestrator.fetch_price(ticker)
    if result.success:
        return result.data
    return None

def fetch_multiple_prices(tickers):
    """Fetch prices for multiple tickers using the provider system.

    Uses the configured provider priority order with automatic fallbacks.
    Caching is handled by the orchestrator.
    """
    start_time = time.time()

    # Use the provider orchestrator for fetching
    orchestrator = get_orchestrator()
    prices = orchestrator.fetch_prices(tickers)

    duration = time.time() - start_time
    success_count = len(prices)
    fail_count = len(tickers) - success_count

    if tickers:
        log.debug(f"fetch_multiple_prices: {len(tickers)} requested, {success_count} succeeded, {fail_count} failed in {duration:.2f}s")

    return prices

# Holdings functions (get_stocks, get_transactions, calculate_fifo_cost_basis, calculate_holdings)
# now imported from services.holdings
# get_validated_eps imported from services.valuation
# calculate_selloff_metrics now handled by orchestrator.fetch_selloff()

@app.route('/')
def index():
    return render_template('index.html')


# =============================================================================
# ROUTE ORGANIZATION
# =============================================================================
# All API routes are now handled by Flask Blueprints in routes/ directory.
# Duplicate route handlers have been removed from this file.
# Screener functions (run_screener, run_quick_price_update, etc.) are now
# imported from services/screener.py
#
# Blueprint mappings:
#   - routes/holdings.py: /api/holdings, /api/holdings-analysis
#   - routes/transactions.py: /api/transactions, /api/stocks
#   - routes/summary.py: /api/prices, /api/profit-timeline, /api/performance
#   - routes/screener.py: /api/screener/*, /api/indices, /api/refresh, /api/recommendations
#   - routes/valuation.py: /api/valuation/*, /api/sec-metrics/*
#   - routes/data.py: /api/data-status, /api/excluded-tickers/*, /api/eps-recommendations
#   - routes/sec.py: /api/sec/*
#
# Routes remaining in app.py (not in blueprints):
#   - / (index page)
#   - /api/summary (kept here per routes/summary.py comment)
#   - /api/orphans, /api/orphans/remove
#   - /api/all-tickers, /api/sec-filings/<ticker>
#   - /api/logs, /api/logs/clear
#   - /api/providers/* (provider configuration)
#   - /api/indexes/* (index settings)
# =============================================================================

def sanitize_for_json(obj):
    """Replace NaN and Inf values with None for JSON compatibility"""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj

def fetch_index_tickers_from_web(index_name):
    """Fetch fresh index constituents from source (Wikipedia/GitHub)."""
    return fetch_index_tickers(index_name)


def get_all_unique_tickers():
    """Get all unique tickers across all enabled indexes (deduplicated)"""
    all_tickers = set()
    enabled_indexes = db.get_enabled_indexes()
    for index_name in INDIVIDUAL_INDICES:
        if index_name in enabled_indexes:
            data = get_index_data(index_name)
            all_tickers.update(data.get('tickers', []))
    return sorted(list(all_tickers))

def get_ticker_indexes(ticker):
    """Get list of enabled indexes a ticker belongs to"""
    indexes = []
    enabled_indexes = db.get_enabled_indexes()
    for index_name in INDIVIDUAL_INDICES:
        if index_name in enabled_indexes:
            data = get_index_data(index_name)
            if ticker in data.get('tickers', []):
                short_name = INDEX_NAMES.get(index_name, (index_name, index_name))[1]
                indexes.append(short_name)
    return indexes

# Cache for ticker-to-index mapping (rebuilt when enabled indexes change)
_ticker_index_cache = None
_ticker_index_cache_enabled = None  # Track which indexes were enabled when cache was built

def get_all_ticker_indexes():
    """Get a mapping of all tickers to their enabled indexes (cached)"""
    global _ticker_index_cache, _ticker_index_cache_enabled
    enabled_indexes = db.get_enabled_indexes()
    # Rebuild cache if enabled indexes changed
    if _ticker_index_cache is None or _ticker_index_cache_enabled != enabled_indexes:
        _ticker_index_cache = {}
        _ticker_index_cache_enabled = enabled_indexes
        for index_name in INDIVIDUAL_INDICES:
            if index_name in enabled_indexes:
                data = get_index_data(index_name)
                short_name = INDEX_NAMES.get(index_name, (index_name, index_name))[1]
                for ticker in data.get('tickers', []):
                    if ticker not in _ticker_index_cache:
                        _ticker_index_cache[ticker] = []
                    _ticker_index_cache[ticker].append(short_name)
    return _ticker_index_cache


def get_index_data(index_name='all'):
    """Load index data from database.
    Uses centralized valuations from database.
    Index ticker lists are stored in ticker_indexes table."""
    if index_name not in VALID_INDICES:
        index_name = 'all'

    # Always load from centralized valuations storage
    valuations_data = data_manager.load_valuations()
    all_valuations = valuations_data.get('valuations', {})
    last_updated = valuations_data.get('last_updated')

    # Special handling for 'all' - combine all indexes
    if index_name == 'all':
        all_tickers = get_all_unique_tickers()
        return {
            'name': 'All Indexes',
            'short_name': 'All',
            'tickers': all_tickers,
            'valuations': all_valuations,
            'last_updated': last_updated
        }

    # Get tickers from database (excludes inactive/delisted)
    tickers = db.get_active_index_tickers(index_name)

    # If no tickers in database, fetch from web and store
    if not tickers:
        print(f"[Index] No tickers in database for {index_name}, fetching from web...")
        tickers = fetch_index_tickers_from_web(index_name)
        if tickers:
            db.refresh_index_membership(index_name, tickers)

    # Get index display names
    name, short_name = INDEX_NAMES.get(index_name, (index_name, index_name))

    # Filter centralized valuations to only include this index's tickers
    index_tickers = set(tickers)
    filtered_valuations = {
        ticker: val for ticker, val in all_valuations.items()
        if ticker in index_tickers
    }

    # Return with centralized valuations filtered by index
    result = {
        'name': name,
        'short_name': short_name,
        'tickers': tickers,
        'valuations': filtered_valuations,
        'last_updated': last_updated
    }

    return sanitize_for_json(result)

def save_index_data(index_name, data):
    """Save index tickers to database"""
    if index_name not in VALID_INDICES or index_name == 'all':
        return  # 'all' is a virtual index, nothing to save
    tickers = data.get('tickers', [])
    if tickers:
        db.refresh_index_membership(index_name, tickers)

# Keep old functions for backward compatibility
def get_sp500_data():
    return get_index_data('sp500')

def save_sp500_data(data):
    save_index_data('sp500', data)

def calculate_valuation(ticker):
    """Calculate valuation for a single ticker, returns dict or None on error"""
    try:
        # Fetch data using orchestrator
        orchestrator = get_orchestrator()

        # Get stock info (company name, 52-week high/low)
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

        # Fetch current price from provider system
        price_result = orchestrator.fetch_price(ticker)
        current_price = price_result.data if price_result.success else 0
        price_source = price_result.source if price_result.success else 'none'

        # Calculate % off 52-week high
        off_high_pct = None
        if fifty_two_week_high and current_price:
            off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

        # Get recent price history for momentum using orchestrator
        history_result = orchestrator.fetch_price_history(ticker, period='3mo')
        price_change_1m = None
        price_change_3m = None

        if history_result.success and history_result.data:
            price_data = history_result.data
            price_change_1m = price_data.change_1m_pct
            price_change_3m = price_data.change_3m_pct

        # Get validated EPS data using orchestrator
        eps_data, eps_source, validation_info = get_validated_eps(ticker)

        # Use SEC company name if available and SEC data was used
        if eps_source.startswith('sec'):
            sec_eps = sec_data.get_sec_eps(ticker)
            if sec_eps and sec_eps.get('company_name'):
                company_name = sec_eps['company_name']

        # Get dividends using orchestrator
        dividend_result = orchestrator.fetch_dividends(ticker)
        annual_dividend = 0
        last_dividend = None
        last_dividend_date = None

        if dividend_result.success and dividend_result.data:
            dividend_data = dividend_result.data
            annual_dividend = dividend_data.annual_dividend
            if dividend_data.payments and len(dividend_data.payments) > 0:
                # Get last dividend from payments list
                last_payment = dividend_data.payments[-1]
                last_dividend_date = last_payment['date']
                last_dividend = round(float(last_payment['amount']), 4)

        # Calculate valuation: (Average EPS over up to 8 years + Annual Dividend) × 10
        min_years_recommended = 8
        eps_avg = None
        estimated_value = None
        price_vs_value = None

        if len(eps_data) > 0 and current_price:
            eps_avg = sum(e['eps'] for e in eps_data) / len(eps_data)
            # Formula: (Average EPS + Annual Dividend) × 10
            estimated_value = (eps_avg + annual_dividend) * 10
            price_vs_value = ((current_price - estimated_value) / estimated_value) * 100 if estimated_value > 0 else None

        # Determine if stock is in a selloff (>20% off high or >15% drop in 3 months)
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
            'eps_avg': round(eps_avg, 2) if eps_avg is not None else None,
            'eps_years': len(eps_data),
            'eps_source': eps_source,
            'has_enough_years': len(eps_data) >= min_years_recommended,
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

def fetch_eps_for_ticker(ticker, existing_valuation=None, retry_count=0):
    """Fetch EPS and dividend data for a single ticker (used in parallel processing)"""
    max_retries = 2
    try:
        # Get company name from orchestrator
        orchestrator = get_orchestrator()
        info_result = orchestrator.fetch_stock_info(ticker)
        if info_result.success and info_result.data:
            company_name = info_result.data.company_name
        else:
            company_name = ticker

        # Get validated EPS data using orchestrator
        eps_data, eps_source, validation_info = get_validated_eps(ticker)

        # Get company name (prefer SEC if available)
        if eps_source.startswith('sec'):
            sec_eps = sec_data.get_sec_eps(ticker)
            if sec_eps and sec_eps.get('company_name'):
                company_name = sec_eps['company_name']

        # Get dividends using orchestrator
        dividend_result = orchestrator.fetch_dividends(ticker)
        annual_dividend = 0
        last_dividend = None
        last_dividend_date = None

        if dividend_result.success and dividend_result.data:
            dividend_data = dividend_result.data
            annual_dividend = dividend_data.annual_dividend
            if dividend_data.payments and len(dividend_data.payments) > 0:
                # Get last dividend from payments list
                last_payment = dividend_data.payments[-1]
                last_dividend_date = last_payment['date']
                last_dividend = round(float(last_payment['amount']), 4)

        # Calculate EPS average
        eps_avg = None
        if len(eps_data) > 0:
            eps_avg = sum(e['eps'] for e in eps_data) / len(eps_data)

        return {
            'ticker': ticker,
            'company_name': company_name,
            'eps_avg': round(eps_avg, 2) if eps_avg is not None else None,
            'eps_years': len(eps_data),
            'eps_source': eps_source,
            'has_enough_years': len(eps_data) >= 8,
            'annual_dividend': round(annual_dividend, 2),
            'last_dividend': last_dividend,
            'last_dividend_date': last_dividend_date,
        }
    except Exception as e:
        error_msg = str(e).lower()
        if 'rate' in error_msg or 'too many' in error_msg or '429' in error_msg:
            if retry_count < max_retries:
                time.sleep(2 ** retry_count)
                return fetch_eps_for_ticker(ticker, existing_valuation, retry_count + 1)
            return {'error': 'rate_limited', 'ticker': ticker}
        print(f"Error fetching EPS for {ticker}: {e}")
        return None


@app.route('/api/orphans')
def api_get_orphans():
    """Get list of orphan tickers (valuations not in any active index)"""
    orphans = db.get_orphan_tickers()
    return jsonify({
        'success': True,
        'count': len(orphans),
        'tickers': orphans
    })


@app.route('/api/orphans/remove', methods=['POST'])
def api_remove_orphans():
    """Remove all orphan valuations and related data"""
    result = db.remove_orphan_valuations()
    log.info(f"Removed orphans: {result}")
    return jsonify({
        'success': True,
        'removed': result
    })


@app.route('/api/summary')
def api_summary():
    """Calculate portfolio summary statistics"""
    stocks = {s['ticker']: s for s in get_stocks()}
    transactions = get_transactions()

    # Track by ticker
    by_ticker = {}
    for txn in transactions:
        ticker = txn['ticker']
        shares = int(txn['shares']) if txn['shares'] else 0
        price = float(txn['price']) if txn['price'] else 0
        status = (txn.get('status') or '').lower()

        # Skip watchlist items - only include buys that are confirmed (status='done')
        # Empty/"Active" and "Placed" statuses are still watchlist, not confirmed purchases
        if txn['action'] == 'buy' and status != 'done':
            continue

        if ticker not in by_ticker:
            by_ticker[ticker] = {
                'ticker': ticker,
                'name': stocks.get(ticker, {}).get('name', ticker),
                'type': stocks.get(ticker, {}).get('type', 'stock'),
                'shares_held': 0,
                'total_bought': 0,
                'total_buy_cost': 0,
                'total_sold': 0,
                'total_sell_revenue': 0,
                'realized_profit': 0,
                'avg_buy_price': 0,
                'pending_sells': [],
            }

        if txn['action'] == 'buy':
            by_ticker[ticker]['shares_held'] += shares
            by_ticker[ticker]['total_bought'] += shares
            by_ticker[ticker]['total_buy_cost'] += shares * price
        elif txn['action'] == 'sell':
            by_ticker[ticker]['shares_held'] -= shares
            if status == 'done':
                by_ticker[ticker]['total_sold'] += shares
                by_ticker[ticker]['total_sell_revenue'] += shares * price
            elif status == 'placed':
                by_ticker[ticker]['pending_sells'].append({
                    'shares': shares,
                    'price': price,
                    'value': shares * price
                })

    # Calculate per-ticker stats
    total_invested = 0
    total_current_cost_basis = 0
    total_realized_profit = 0
    total_pending_value = 0
    total_pending_profit = 0

    ticker_summaries = []
    for ticker, data in by_ticker.items():
        # Average buy price
        if data['total_bought'] > 0:
            data['avg_buy_price'] = data['total_buy_cost'] / data['total_bought']

        # Realized profit (sell revenue - proportional cost)
        if data['total_sold'] > 0 and data['avg_buy_price'] > 0:
            cost_of_sold = data['total_sold'] * data['avg_buy_price']
            data['realized_profit'] = data['total_sell_revenue'] - cost_of_sold

        # Current holdings value at cost
        data['current_cost_basis'] = data['shares_held'] * data['avg_buy_price'] if data['shares_held'] > 0 else 0

        # Pending sells
        pending_value = sum(p['value'] for p in data['pending_sells'])
        pending_shares = sum(p['shares'] for p in data['pending_sells'])
        pending_cost = pending_shares * data['avg_buy_price']
        data['pending_value'] = pending_value
        data['pending_profit'] = pending_value - pending_cost if pending_cost > 0 else 0

        total_invested += data['total_buy_cost']
        total_current_cost_basis += data['current_cost_basis']
        total_realized_profit += data['realized_profit']
        total_pending_value += pending_value
        total_pending_profit += data['pending_profit']

        ticker_summaries.append({
            'ticker': ticker,
            'name': data['name'],
            'type': data['type'],
            'shares_held': data['shares_held'],
            'avg_buy_price': round(data['avg_buy_price'], 2),
            'current_cost_basis': round(data['current_cost_basis'], 2),
            'realized_profit': round(data['realized_profit'], 2),
            'pending_value': round(data['pending_value'], 2),
            'pending_profit': round(data['pending_profit'], 2),
            'total_sell_revenue': round(data['total_sell_revenue'], 2),
        })

    # Sort by realized profit descending
    ticker_summaries.sort(key=lambda x: x['realized_profit'], reverse=True)

    return jsonify({
        'totals': {
            'total_invested': round(total_invested, 2),
            'current_cost_basis': round(total_current_cost_basis, 2),
            'realized_profit': round(total_realized_profit, 2),
            'pending_value': round(total_pending_value, 2),
            'pending_profit': round(total_pending_profit, 2),
            'total_returned': round(total_realized_profit + total_invested - total_current_cost_basis, 2),
        },
        'by_ticker': ticker_summaries
    })

def parse_date(date_str):
    """Parse date string to date object"""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        return None

@app.route('/api/all-tickers')
def api_all_tickers():
    """Get all tickers with key details for the Data Sets table"""
    # Get all valuations
    all_valuations = data_manager.load_valuations().get('valuations', {})

    # Get ticker status for index membership and SEC status
    ticker_status = data_manager.load_ticker_status().get('tickers', {})

    result = []
    for ticker, val in all_valuations.items():
        status = ticker_status.get(ticker, {})
        result.append({
            'ticker': ticker,
            'company_name': val.get('company_name', ticker),
            'current_price': val.get('current_price'),
            'eps_avg': val.get('eps_avg'),
            'eps_years': val.get('eps_years'),
            'eps_source': val.get('eps_source'),
            'estimated_value': val.get('estimated_value'),
            'price_vs_value': val.get('price_vs_value'),
            'annual_dividend': val.get('annual_dividend'),
            'indexes': status.get('indexes', []),
            'sec_status': status.get('sec_status', 'unknown'),
            'valuation_updated': val.get('updated'),
            'sec_checked': status.get('sec_checked')
        })

    # Sort by ticker
    result.sort(key=lambda x: x['ticker'])

    return jsonify({
        'tickers': result,
        'count': len(result)
    })

@app.route('/api/sec-filings/<ticker>')
def api_sec_filings(ticker):
    """Get 10-K filing URLs for a specific ticker"""
    filings = sec_data.get_10k_filings(ticker.upper())
    return jsonify(filings)


@app.route('/api/logs')
def api_logs():
    """Get recent log entries for debugging"""
    from logger import tail_log
    lines = request.args.get('lines', 100, type=int)
    lines = min(lines, 500)  # Cap at 500 lines
    log_content = tail_log(lines)
    return Response(log_content, mimetype='text/plain')

@app.route('/api/logs/clear', methods=['POST'])
def api_clear_logs():
    """Clear the log file"""
    from logger import clear_log
    clear_log()
    return jsonify({'status': 'ok', 'message': 'Log file cleared'})

# Provider configuration endpoints

@app.route('/api/providers/config')
def api_provider_config():
    """Get provider configuration and status"""
    config = get_config()
    registry = get_registry()
    disabled = set(config.disabled_providers)

    # Get status of all providers
    available_providers = []
    for provider in registry.get_all_providers():
        available_providers.append({
            'name': provider.name,
            'display_name': provider.display_name,
            'available': provider.is_available(),
            'enabled': provider.name not in disabled,
            'data_types': [dt.value for dt in provider.data_types],
            'supports_batch': provider.supports_batch
        })

    return jsonify({
        'price_providers': config.price_providers,
        'eps_providers': config.eps_providers,
        'dividend_providers': config.dividend_providers,
        'disabled_providers': config.disabled_providers,
        'available_providers': available_providers,
        'has_fmp_key': has_fmp_api_key(),
        'has_alpaca_key': has_alpaca_credentials(),
        'alpaca_endpoint': get_alpaca_api_endpoint() or '',
        'price_cache_seconds': config.price_cache_seconds,
        'prefer_batch': config.prefer_batch
    })

@app.route('/api/providers/config', methods=['POST'])
def api_update_provider_config():
    """Update provider configuration"""
    data = request.json

    if 'price_providers' in data:
        set_provider_order('price', data['price_providers'])
    if 'eps_providers' in data:
        set_provider_order('eps', data['eps_providers'])
    if 'dividend_providers' in data:
        set_provider_order('dividend', data['dividend_providers'])

    # Update other settings
    update_kwargs = {}
    if 'price_cache_seconds' in data:
        update_kwargs['price_cache_seconds'] = int(data['price_cache_seconds'])
    if 'prefer_batch' in data:
        update_kwargs['prefer_batch'] = bool(data['prefer_batch'])

    if update_kwargs:
        update_config(**update_kwargs)

    return jsonify({'status': 'ok', 'message': 'Configuration updated'})

@app.route('/api/providers/api-key', methods=['POST'])
def api_set_provider_api_key():
    """Set API key for a provider"""
    data = request.json
    provider = data.get('provider')

    if provider == 'fmp':
        api_key = data.get('api_key', '').strip()
        if not api_key:
            return jsonify({'status': 'error', 'message': 'API key required'}), 400

        # Validate the key first
        is_valid, message = validate_fmp_api_key(api_key)
        if not is_valid:
            return jsonify({'status': 'error', 'message': message}), 400

        set_secret('FMP_API_KEY', api_key)
        return jsonify({'status': 'ok', 'message': 'FMP API key saved and validated'})

    elif provider == 'alpaca':
        api_key = data.get('api_key', '').strip()
        api_secret = data.get('api_secret', '').strip()
        api_endpoint = data.get('api_endpoint', '').strip() or None
        if not api_key or not api_secret:
            return jsonify({'status': 'error', 'message': 'API key and secret required'}), 400

        # Validate the credentials with the optional endpoint
        is_valid, message = validate_alpaca_api_key(api_key, api_secret, api_endpoint)
        if not is_valid:
            return jsonify({'status': 'error', 'message': message}), 400

        set_alpaca_credentials(api_key, api_secret, api_endpoint)
        return jsonify({'status': 'ok', 'message': 'Alpaca credentials saved and validated'})

    else:
        return jsonify({'status': 'error', 'message': f'Unknown provider: {provider}'}), 400

@app.route('/api/providers/toggle', methods=['POST'])
def api_toggle_provider():
    """Enable or disable a provider"""
    data = request.json
    provider_name = data.get('provider')
    enabled = data.get('enabled', True)

    if not provider_name:
        return jsonify({'status': 'error', 'message': 'Provider name required'}), 400

    # Verify provider exists
    registry = get_registry()
    provider = registry.get_provider(provider_name)
    if not provider:
        return jsonify({'status': 'error', 'message': f'Unknown provider: {provider_name}'}), 404

    if enabled:
        enable_provider(provider_name)
        return jsonify({'status': 'ok', 'message': f'{provider.display_name} enabled'})
    else:
        disable_provider(provider_name)
        return jsonify({'status': 'ok', 'message': f'{provider.display_name} disabled'})

@app.route('/api/providers/test/<provider_name>')
def api_test_provider(provider_name):
    """Test a provider by fetching price for AAPL"""
    registry = get_registry()
    provider = registry.get_provider(provider_name)

    if not provider:
        return jsonify({'status': 'error', 'message': f'Provider not found: {provider_name}'}), 404

    if not provider.is_available():
        return jsonify({
            'status': 'error',
            'message': 'Provider not available (missing API key or dependency)',
            'available': False
        }), 400

    try:
        # Test by fetching AAPL price
        from services.providers.base import PriceProvider
        if isinstance(provider, PriceProvider):
            result = provider.fetch_price('AAPL')
            if result.success:
                return jsonify({
                    'status': 'ok',
                    'message': f'Successfully fetched AAPL price: ${result.data:.2f}',
                    'price': result.data,
                    'source': result.source
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': result.error
                }), 400
        else:
            return jsonify({
                'status': 'ok',
                'message': 'Provider is available but does not support price fetching'
            })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/providers/cache/stats')
def api_provider_cache_stats():
    """Get cache statistics"""
    orchestrator = get_orchestrator()
    return jsonify(orchestrator.get_cache_stats())

@app.route('/api/providers/cache/clear', methods=['POST'])
def api_clear_provider_cache():
    """Clear provider cache"""
    data = request.json or {}
    data_type = data.get('data_type')
    ticker = data.get('ticker')

    orchestrator = get_orchestrator()

    if data_type:
        from services.providers.base import DataType
        try:
            dt = DataType(data_type)
            entries_cleared = orchestrator.clear_cache(data_type=dt, ticker=ticker)
        except ValueError:
            return jsonify({'status': 'error', 'message': f'Invalid data type: {data_type}'}), 400
    else:
        entries_cleared = orchestrator.clear_cache(ticker=ticker)

    return jsonify({
        'status': 'ok',
        'message': f'Cache cleared ({entries_cleared} prices invalidated in database)'
    })


# ============================================
# Index Settings API
# ============================================

@app.route('/api/indexes/settings')
def api_index_settings():
    """Get all indexes with their enabled state."""
    indexes = db.get_all_indexes()
    return jsonify(indexes)


@app.route('/api/indexes/settings', methods=['POST'])
def api_update_index_settings():
    """Update index enabled states."""
    data = request.json
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400

    # Expect format: {'sp500': true, 'dow30': false, ...}
    updated = db.set_indexes_enabled(data)

    # Recalculate ticker enabled states based on new index settings
    ticker_stats = db.recalculate_ticker_enabled_states()

    return jsonify({
        'success': True,
        'updated': updated,
        'message': f'Updated {updated} index settings',
        'tickers_enabled': ticker_stats['enabled'],
        'tickers_disabled': ticker_stats['disabled']
    })


@app.route('/api/indexes/settings/<index_name>', methods=['POST'])
def api_toggle_index(index_name):
    """Toggle a single index enabled state."""
    data = request.json or {}
    enabled = data.get('enabled', True)

    success = db.set_index_enabled(index_name, enabled)

    if success:
        # Recalculate ticker enabled states based on new index settings
        ticker_stats = db.recalculate_ticker_enabled_states()

        return jsonify({
            'success': True,
            'index': index_name,
            'enabled': enabled,
            'tickers_enabled': ticker_stats['enabled'],
            'tickers_disabled': ticker_stats['disabled']
        })
    else:
        return jsonify({
            'success': False,
            'error': f'Index not found: {index_name}'
        }), 404


@app.route('/api/indexes/enabled-ticker-count')
def api_enabled_ticker_count():
    """Get count of unique tickers across all enabled indexes."""
    tickers = get_all_unique_tickers()
    return jsonify({
        'count': len(tickers),
        'enabled_indexes': list(db.get_enabled_indexes())
    })


@app.before_request
def check_startup_tasks():
    """Run startup tasks on first request"""
    global startup_check_done
    if not startup_check_done:
        startup_check_done = True
        # Get S&P 500 tickers and check SEC data in background
        try:
            sp500_data = get_sp500_data()
            tickers = sp500_data.get('tickers', [])
            # Run in a separate thread to not block the request
            thread = threading.Thread(
                target=sec_data.check_and_update_on_startup,
                args=(tickers,)
            )
            thread.daemon = True
            thread.start()
        except Exception as e:
            print(f"[Startup] Error checking SEC data: {e}")

def cleanup_providers():
    """Clean up provider connections on shutdown."""
    try:
        disconnect_ibkr()
        print("[Cleanup] IBKR connection closed")
    except Exception as e:
        print(f"[Cleanup] Error closing IBKR: {e}")

if __name__ == '__main__':
    import atexit
    atexit.register(cleanup_providers)

    # Initialize market data providers
    init_providers()
    app.run(debug=True, port=8080)

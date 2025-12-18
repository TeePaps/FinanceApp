from flask import Flask, render_template, jsonify, request, Response
import csv
import os
import time
import json
from datetime import datetime, timedelta
import yfinance as yf
import math
import threading
import sec_data
import data_manager
import database as db
from logger import log, log_yahoo_fetch, log_screener_progress, log_error
from config import (
    DATA_DIR, USER_DATA_DIR, EXCLUDED_TICKERS_FILE, TICKER_FAILURES_FILE,
    STOCKS_FILE, TRANSACTIONS_FILE,
    PRICE_CACHE_DURATION, FAILURE_THRESHOLD, VALID_INDICES,
    PE_RATIO_MULTIPLIER, RECOMMENDED_EPS_YEARS,
    YAHOO_BATCH_SIZE, YAHOO_BATCH_DELAY, YAHOO_SINGLE_DELAY,
    DIVIDEND_NO_DIVIDEND_PENALTY, DIVIDEND_POINTS_PER_PERCENT, DIVIDEND_MAX_POINTS,
    SELLOFF_SEVERE_BONUS, SELLOFF_MODERATE_BONUS, SELLOFF_RECENT_BONUS,
    SELLOFF_VOLUME_SEVERE, SELLOFF_VOLUME_HIGH, SELLOFF_VOLUME_MODERATE,
    SCORING_WEIGHTS, RECOMMENDATION_MIN_EPS_YEARS
)

app = Flask(__name__)

# Blueprint registration disabled - blueprints have different response formats
# that need to be aligned with JavaScript expectations before enabling
# from routes import register_blueprints
# register_blueprints(app)

# Flag to prevent multiple startup checks
startup_check_done = False

# Screener state
screener_running = False
screener_current_index = 'all'
screener_progress = {'current': 0, 'total': 0, 'ticker': '', 'status': 'idle', 'index': 'all'}

# Price cache: {ticker: {'price': float, 'timestamp': float}}
price_cache = {}
CACHE_DURATION = PRICE_CACHE_DURATION  # Use config value

def read_csv(filename):
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        return list(reader)

def write_csv(filename, data, fieldnames):
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)

def write_user_csv(filepath, data, fieldnames):
    """Write CSV to data_private directory (for personal holdings)"""
    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)

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
    """Fetch current stock price using yfinance"""
    global price_cache

    # Check cache first
    if ticker in price_cache:
        cached = price_cache[ticker]
        if time.time() - cached['timestamp'] < CACHE_DURATION:
            return cached['price']

    try:
        stock = yf.Ticker(ticker)
        # fast_info is quicker than info
        price = stock.fast_info.get('lastPrice')
        if price:
            price_cache[ticker] = {
                'price': price,
                'timestamp': time.time()
            }
            return price
    except Exception as e:
        print(f"Error fetching price for {ticker}: {e}")

    return None

def fetch_multiple_prices(tickers):
    """Fetch prices for multiple tickers using yfinance batch download with retry logic"""
    global price_cache
    now = time.time()
    start_time = time.time()

    # Check which tickers need fetching
    to_fetch = []
    cached_prices = {}
    for ticker in tickers:
        if ticker in price_cache and now - price_cache[ticker]['timestamp'] < CACHE_DURATION:
            cached_prices[ticker] = price_cache[ticker]['price']
        else:
            to_fetch.append(ticker)

    log.debug(f"fetch_multiple_prices: {len(tickers)} requested, {len(cached_prices)} cached, {len(to_fetch)} to fetch")

    # Fetch uncached tickers in batch with retry logic
    success_count = 0
    fail_count = 0
    failed_tickers = []

    if to_fetch:
        # Try batch download first with retries
        max_retries = 2
        for attempt in range(max_retries):
            try:
                log.debug(f"Yahoo batch download attempt {attempt + 1} for {len(to_fetch)} tickers...")
                data = yf.download(to_fetch, period='1d', progress=False, threads=True)

                if not data.empty:
                    # Get the latest close price for each ticker
                    if len(to_fetch) == 1:
                        # Single ticker returns different structure
                        ticker = to_fetch[0]
                        if 'Close' in data.columns:
                            price = data['Close'].iloc[-1]
                            if price and not (hasattr(price, 'isna') and price.isna()):
                                price_cache[ticker] = {'price': float(price), 'timestamp': now}
                                cached_prices[ticker] = float(price)
                                success_count += 1
                            else:
                                failed_tickers.append(ticker)
                        else:
                            failed_tickers.append(ticker)
                    else:
                        # Multiple tickers
                        for ticker in to_fetch:
                            try:
                                if ('Close', ticker) in data.columns:
                                    price = data[('Close', ticker)].iloc[-1]
                                    if price and not (hasattr(price, 'isna') and price.isna()):
                                        price_cache[ticker] = {'price': float(price), 'timestamp': now}
                                        cached_prices[ticker] = float(price)
                                        success_count += 1
                                    else:
                                        failed_tickers.append(ticker)
                                else:
                                    failed_tickers.append(ticker)
                            except Exception:
                                failed_tickers.append(ticker)
                    break  # Success, exit retry loop
                else:
                    log.warning(f"Yahoo batch download attempt {attempt + 1} returned empty data")
                    if attempt < max_retries - 1:
                        time.sleep(1)  # Wait before retry
                    else:
                        failed_tickers = to_fetch.copy()
            except Exception as e:
                log.warning(f"Yahoo batch download attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    failed_tickers = to_fetch.copy()

        # Fallback: try individual ticker fetching for failed tickers
        if failed_tickers:
            log.info(f"Falling back to individual fetching for {len(failed_tickers)} tickers")
            for ticker in failed_tickers:
                try:
                    time.sleep(0.2)  # Small delay between individual requests
                    stock = yf.Ticker(ticker)
                    # Try fast_info first (more reliable)
                    try:
                        price = stock.fast_info.get('lastPrice') or stock.fast_info.get('regularMarketPrice')
                    except Exception:
                        price = None

                    # Fallback to history if fast_info fails
                    if not price:
                        hist = stock.history(period='1d')
                        if not hist.empty and 'Close' in hist.columns:
                            price = hist['Close'].iloc[-1]

                    if price and price > 0:
                        price_cache[ticker] = {'price': float(price), 'timestamp': now}
                        cached_prices[ticker] = float(price)
                        success_count += 1
                        log.debug(f"Individual fetch succeeded for {ticker}: ${price:.2f}")
                    else:
                        fail_count += 1
                        log.debug(f"Individual fetch failed for {ticker}: no valid price")
                except Exception as e:
                    fail_count += 1
                    log.debug(f"Individual fetch error for {ticker}: {e}")

    duration = time.time() - start_time
    if to_fetch:
        log_yahoo_fetch(to_fetch, success_count, fail_count, duration)

    return cached_prices

def get_stocks():
    """Read stocks from database"""
    return db.get_stocks()

def get_transactions():
    """Read transactions from database"""
    return db.get_transactions()

def calculate_fifo_cost_basis(ticker, transactions):
    """
    Calculate FIFO cost basis for sells.
    Returns a dict mapping transaction id to cost basis info for sells.
    """
    # Build list of lots (buys) in order
    lots = []  # Each lot: {'shares': n, 'price': p, 'remaining': n}
    sell_basis = {}  # txn_id -> {'cost_basis': total_cost, 'shares': n, 'avg_cost_per_share': p}

    for txn in transactions:
        if txn['ticker'] != ticker:
            continue

        shares = int(txn['shares']) if txn['shares'] else 0
        price = float(txn['price']) if txn['price'] else 0

        if txn['action'] == 'buy':
            lots.append({'shares': shares, 'price': price, 'remaining': shares})
        elif txn['action'] == 'sell':
            # Use FIFO to determine cost basis
            shares_to_sell = shares
            total_cost = 0
            lots_used = []

            for lot in lots:
                if shares_to_sell <= 0:
                    break
                if lot['remaining'] <= 0:
                    continue

                take = min(lot['remaining'], shares_to_sell)
                total_cost += take * lot['price']
                lot['remaining'] -= take
                shares_to_sell -= take
                lots_used.append({'shares': take, 'price': lot['price']})

            avg_cost = total_cost / shares if shares > 0 else 0
            sell_basis[txn['id']] = {
                'cost_basis': total_cost,
                'shares': shares,
                'avg_cost_per_share': avg_cost,
                'lots_used': lots_used
            }

    return sell_basis, lots


def get_validated_eps(ticker, stock, income_stmt=None):
    """
    Get EPS data from SEC EDGAR (authoritative) or yfinance (fallback).
    SEC EDGAR data uses company fiscal year and is the official source.
    Returns (eps_data, source, validation_info)

    Pass income_stmt if already fetched to avoid duplicate API calls.
    """
    ticker = ticker.upper()
    validation_info = {'validated': False, 'years_available': 0}

    # Get SEC EDGAR data first - this is the authoritative source (from 10-K filings)
    sec_eps = sec_data.get_sec_eps(ticker)
    if sec_eps and sec_eps.get('eps_history'):
        sec_eps_data = [{
            'year': e['year'],
            'eps': e['eps'],
            'eps_type': e.get('eps_type', 'Diluted EPS'),
            'period_start': e.get('period_start') or e.get('start'),
            'period_end': e.get('period_end') or e.get('end')
        } for e in sec_eps['eps_history']]
        # SEC data is authoritative - use it directly
        # Note: Years are fiscal years from the company's 10-K filings
        validation_info = {
            'validated': True,
            'source': 'SEC EDGAR 10-K filings',
            'years_available': len(sec_eps_data),
            'fiscal_year': True
        }
        return sec_eps_data[:8], 'sec', validation_info

    # No SEC data available - fall back to yfinance
    yf_eps_data = _extract_yf_eps(stock, income_stmt)
    if yf_eps_data:
        validation_info = {
            'validated': False,
            'source': 'yfinance (SEC data not available)',
            'years_available': len(yf_eps_data)
        }
        return yf_eps_data[:8], 'yfinance', validation_info

    return [], 'none', validation_info


def _extract_yf_eps(stock, income_stmt=None):
    """Extract EPS data from yfinance income statement."""
    yf_eps_data = []
    try:
        if income_stmt is None:
            income_stmt = stock.income_stmt
        if income_stmt is not None and not income_stmt.empty:
            eps_row = None
            if 'Diluted EPS' in income_stmt.index:
                eps_row = income_stmt.loc['Diluted EPS']
            elif 'Basic EPS' in income_stmt.index:
                eps_row = income_stmt.loc['Basic EPS']

            if eps_row is not None:
                for date, eps in eps_row.items():
                    if eps is not None and not (isinstance(eps, float) and math.isnan(eps)):
                        year = date.year if hasattr(date, 'year') else int(str(date)[:4])
                        yf_eps_data.append({'year': int(year), 'eps': float(eps)})
                yf_eps_data.sort(key=lambda x: x['year'], reverse=True)
    except Exception:
        pass
    return yf_eps_data


def calculate_selloff_metrics(stock):
    """
    Calculate selloff metrics based on volume on down days vs average volume.
    Returns selloff rates for 1 day, 1 week, and 1 month timeframes.

    Selloff Rate = (Volume on down days) / (Average volume for period)
    Higher values indicate more selling pressure.
    """
    try:
        # Get 30 days of history for monthly calculation
        hist = stock.history(period='1mo')
        if hist is None or hist.empty or len(hist) < 2:
            return None

        # Get average volume from info (20-day average)
        info = stock.info
        avg_volume = info.get('averageVolume', 0) or info.get('averageDailyVolume10Day', 0)
        if not avg_volume or avg_volume == 0:
            # Calculate from history if not available
            avg_volume = hist['Volume'].mean()

        # Calculate daily price changes
        hist['price_change'] = hist['Close'].pct_change()
        hist['is_down_day'] = hist['price_change'] < 0

        # 1-Day selloff (today)
        today = hist.iloc[-1] if len(hist) > 0 else None
        day_selloff = None
        if today is not None and avg_volume > 0:
            if today['is_down_day']:
                day_selloff = today['Volume'] / avg_volume
            else:
                day_selloff = 0  # Not a down day

        # 1-Week selloff (last 5 trading days)
        week_data = hist.tail(5)
        week_down_days = week_data[week_data['is_down_day']]
        week_selloff = None
        if len(week_data) > 0 and avg_volume > 0:
            if len(week_down_days) > 0:
                # Average volume on down days relative to normal
                week_down_volume = week_down_days['Volume'].mean()
                week_selloff = week_down_volume / avg_volume
            else:
                week_selloff = 0  # No down days this week

        # 1-Month selloff (all available data, ~22 trading days)
        month_down_days = hist[hist['is_down_day']]
        month_selloff = None
        if len(hist) > 0 and avg_volume > 0:
            if len(month_down_days) > 0:
                month_down_volume = month_down_days['Volume'].mean()
                month_selloff = month_down_volume / avg_volume
            else:
                month_selloff = 0

        # Calculate overall selloff severity using config thresholds
        severity = 'none'
        max_selloff = max(day_selloff or 0, week_selloff or 0, month_selloff or 0)
        if max_selloff >= SELLOFF_VOLUME_SEVERE:
            severity = 'severe'  # 3x+ normal volume on down days
        elif max_selloff >= SELLOFF_VOLUME_HIGH:
            severity = 'high'    # 2x-3x normal volume
        elif max_selloff >= SELLOFF_VOLUME_MODERATE:
            severity = 'moderate' # 1.5x-2x normal volume
        elif max_selloff >= 1.0:
            severity = 'normal'   # Normal volume on down days

        # Count down days
        down_days_week = len(week_down_days)
        down_days_month = len(month_down_days)
        total_days_month = len(hist)

        return {
            'day': {
                'selloff_rate': round(day_selloff, 2) if day_selloff is not None else None,
                'is_down': bool(today['is_down_day']) if today is not None else None,
                'volume': int(today['Volume']) if today is not None else None,
                'price_change_pct': round(today['price_change'] * 100, 2) if today is not None and not math.isnan(today['price_change']) else None
            },
            'week': {
                'selloff_rate': round(week_selloff, 2) if week_selloff is not None else None,
                'down_days': down_days_week,
                'total_days': len(week_data)
            },
            'month': {
                'selloff_rate': round(month_selloff, 2) if month_selloff is not None else None,
                'down_days': down_days_month,
                'total_days': total_days_month
            },
            'avg_volume': int(avg_volume),
            'severity': severity,
            'formula': 'Selloff Rate = (Avg Volume on Down Days) / (Normal Avg Volume)'
        }
    except Exception as e:
        print(f"Error calculating selloff metrics: {e}")
        return None


def calculate_holdings(confirmed_only=False):
    """Calculate current holdings from transactions with FIFO lot tracking

    Args:
        confirmed_only: If True, only include buys with status='done'
    """
    stocks = {s['ticker']: s for s in get_stocks()}
    transactions = get_transactions()

    # Group transactions by ticker, optionally filtering buys by status
    by_ticker = {}
    for txn in transactions:
        # If confirmed_only, skip buy transactions that aren't 'done'
        if confirmed_only and txn['action'] == 'buy':
            status = (txn.get('status') or '').lower()
            if status != 'done':
                continue

        ticker = txn['ticker']
        if ticker not in by_ticker:
            by_ticker[ticker] = []
        by_ticker[ticker].append(txn)

    holdings = {}
    for ticker, ticker_txns in by_ticker.items():
        # Calculate FIFO cost basis for this ticker
        sell_basis, remaining_lots = calculate_fifo_cost_basis(ticker, ticker_txns)

        # Calculate remaining shares and cost basis
        total_shares = sum(lot['remaining'] for lot in remaining_lots)
        total_cost = sum(lot['remaining'] * lot['price'] for lot in remaining_lots)

        holdings[ticker] = {
            'ticker': ticker,
            'name': stocks.get(ticker, {}).get('name', ticker),
            'type': stocks.get(ticker, {}).get('type', 'stock'),
            'shares': total_shares,
            'total_cost': total_cost,
            'avg_cost': total_cost / total_shares if total_shares > 0 else 0,
            'remaining_lots': [{'shares': l['remaining'], 'price': l['price']} for l in remaining_lots if l['remaining'] > 0],
            'transactions': []
        }

        # Add transactions with computed gain percentages for sells
        for txn in ticker_txns:
            txn_copy = dict(txn)
            if txn['action'] == 'sell' and txn['id'] in sell_basis:
                basis = sell_basis[txn['id']]
                sell_price = float(txn['price']) if txn['price'] else 0
                if basis['avg_cost_per_share'] > 0:
                    gain_pct = ((sell_price - basis['avg_cost_per_share']) / basis['avg_cost_per_share']) * 100
                    txn_copy['computed_gain_pct'] = round(gain_pct, 1)
                    txn_copy['fifo_cost_basis'] = round(basis['avg_cost_per_share'], 2)
            holdings[ticker]['transactions'].append(txn_copy)

    return holdings

@app.route('/')
def index():
    return render_template('index.html')


# =============================================================================
# LEGACY ROUTE HANDLERS - NOW HANDLED BY BLUEPRINTS
# =============================================================================
# The following route handlers are now handled by Flask Blueprints in routes/
# These duplicates remain for reference but blueprints (registered first) take
# precedence. Safe to delete after verification.
#
# Blueprint mappings:
#   - routes/holdings.py: /api/holdings, /api/holdings-analysis
#   - routes/transactions.py: /api/transactions, /api/stocks
#   - routes/summary.py: /api/prices, /api/summary, /api/profit-timeline, /api/performance
#   - routes/screener.py: /api/screener/*, /api/indices, /api/refresh, /api/recommendations
#   - routes/valuation.py: /api/valuation/*, /api/sec-metrics/*
#   - routes/data.py: /api/data-status, /api/excluded-tickers/*, /api/eps-recommendations
#   - routes/sec.py: /api/sec/*
# =============================================================================

@app.route('/api/holdings')
def api_holdings():
    holdings = calculate_holdings()

    # Determine if each holding has any confirmed (done) shares
    def has_confirmed_shares(holding):
        """Check if holding has any confirmed (done) buy transactions"""
        for txn in holding['transactions']:
            if txn['action'] == 'buy':
                status = (txn.get('status') or '').lower()
                if status == 'done':  # Only 'done' status = confirmed purchase
                    return True
        return False

    # Separate into confirmed holdings vs pending/watchlist
    confirmed = {}
    pending = {}

    for ticker, holding in holdings.items():
        if has_confirmed_shares(holding):
            confirmed[ticker] = holding
        else:
            pending[ticker] = holding

    # Separate stocks and index funds for confirmed holdings
    stocks = {k: v for k, v in confirmed.items() if v['type'] == 'stock'}
    index_funds = {k: v for k, v in confirmed.items() if v['type'] == 'index'}

    # Separate pending by type as well
    pending_stocks = {k: v for k, v in pending.items() if v['type'] == 'stock'}
    pending_index = {k: v for k, v in pending.items() if v['type'] == 'index'}

    return jsonify({
        'stocks': stocks,
        'index_funds': index_funds,
        'pending_stocks': pending_stocks,
        'pending_index': pending_index
    })

@app.route('/api/holdings-analysis')
def api_holdings_analysis():
    """Get holdings with current prices, valuations, and sell recommendations"""
    holdings = calculate_holdings()
    valuations_data = data_manager.load_valuations()
    all_valuations = valuations_data.get('valuations', {})
    last_updated = valuations_data.get('last_updated')

    # Enrich holdings with current price and valuation data
    enriched_holdings = {}
    sell_candidates = []

    for ticker, holding in holdings.items():
        # Only process confirmed holdings (with done buy transactions)
        has_confirmed = any(
            txn['action'] == 'buy' and (txn.get('status') or '').lower() == 'done'
            for txn in holding['transactions']
        )
        if not has_confirmed:
            continue

        val = all_valuations.get(ticker, {})
        current_price = val.get('current_price')
        price_vs_value = val.get('price_vs_value')
        estimated_value = val.get('estimated_value')

        # Calculate average cost basis from remaining lots
        avg_cost = None
        total_cost = 0
        total_shares = 0
        if holding.get('remaining_lots'):
            for lot in holding['remaining_lots']:
                total_cost += lot['shares'] * lot['price']
                total_shares += lot['shares']
            if total_shares > 0:
                avg_cost = total_cost / total_shares

        # Calculate gain percentage if we have current price and cost basis
        gain_pct = None
        if current_price and avg_cost and avg_cost > 0:
            gain_pct = ((current_price - avg_cost) / avg_cost) * 100

        enriched = {
            **holding,
            'current_price': current_price,
            'estimated_value': estimated_value,
            'price_vs_value': price_vs_value,
            'avg_cost': round(avg_cost, 2) if avg_cost else None,
            'gain_pct': round(gain_pct, 1) if gain_pct else None,
            'annual_dividend': val.get('annual_dividend', 0),
            'dividend_yield': round((val.get('annual_dividend', 0) / current_price * 100), 2) if current_price and current_price > 0 else 0,
            'updated': val.get('updated')
        }
        enriched_holdings[ticker] = enriched

        # Check if this is a sell candidate
        # Criteria: overvalued (price > value) OR significant gain (>30%)
        is_overvalued = price_vs_value is not None and price_vs_value > 10
        has_big_gain = gain_pct is not None and gain_pct > 30

        if is_overvalued or has_big_gain:
            reasons = []
            if is_overvalued:
                reasons.append(f"Trading {price_vs_value:.0f}% above estimated value")
            if has_big_gain:
                reasons.append(f"Up {gain_pct:.0f}% from your cost basis of ${avg_cost:.2f}")

            sell_candidates.append({
                'ticker': ticker,
                'name': holding.get('name', ticker),
                'shares': holding.get('shares', 0),
                'current_price': current_price,
                'avg_cost': avg_cost,
                'gain_pct': gain_pct,
                'price_vs_value': price_vs_value,
                'estimated_value': estimated_value,
                'reasons': reasons,
                'priority': (price_vs_value or 0) + (gain_pct or 0) / 2  # Higher = stronger sell signal
            })

    # Sort sell candidates by priority
    sell_candidates.sort(key=lambda x: x['priority'], reverse=True)

    return jsonify({
        'holdings': enriched_holdings,
        'sell_recommendations': sell_candidates[:5],  # Top 5 sell candidates
        'last_updated': last_updated
    })

@app.route('/api/transactions')
def api_transactions():
    return jsonify(get_transactions())

@app.route('/api/transactions', methods=['POST'])
def add_transaction():
    data = request.json

    # Add transaction to database
    txn_id = db.add_transaction(
        ticker=data.get('ticker', ''),
        action=data.get('action', ''),
        shares=int(data.get('shares', 0)) if data.get('shares') else 0,
        price=float(data.get('price', 0)) if data.get('price') else 0,
        gain_pct=float(data.get('gain_pct')) if data.get('gain_pct') else None,
        date=data.get('date'),
        status=data.get('status')
    )

    return jsonify({'success': True, 'id': txn_id})

@app.route('/api/transactions/<int:txn_id>', methods=['PUT'])
def update_transaction(txn_id):
    data = request.json
    db.update_transaction(txn_id, data)
    return jsonify({'success': True})

@app.route('/api/transactions/<int:txn_id>', methods=['DELETE'])
def delete_transaction(txn_id):
    db.delete_transaction(txn_id)
    return jsonify({'success': True})

@app.route('/api/stocks')
def api_stocks():
    return jsonify(get_stocks())

@app.route('/api/prices')
def api_prices():
    """Fetch current prices for confirmed holdings only (not watchlist)"""
    holdings = calculate_holdings(confirmed_only=True)
    tickers = [t for t, h in holdings.items() if h['shares'] > 0]
    prices = fetch_multiple_prices(tickers)

    # Get cached valuations for updated timestamps
    all_valuations = data_manager.load_valuations().get('valuations', {})

    # Calculate unrealized gains
    results = {}
    for ticker in tickers:
        if ticker in prices:
            holding = holdings[ticker]
            current_price = prices[ticker]
            current_value = current_price * holding['shares']
            cost_basis = holding['total_cost']
            unrealized_gain = current_value - cost_basis
            unrealized_pct = (unrealized_gain / cost_basis * 100) if cost_basis > 0 else 0

            # Get updated timestamp from valuations
            val = all_valuations.get(ticker, {})

            results[ticker] = {
                'price': round(current_price, 2),
                'name': holding['name'],
                'shares': holding['shares'],
                'current_value': round(current_value, 2),
                'cost_basis': round(cost_basis, 2),
                'unrealized_gain': round(unrealized_gain, 2),
                'unrealized_pct': round(unrealized_pct, 1),
                'updated': val.get('updated')
            }

    # Calculate portfolio totals
    total_value = sum(r['current_value'] for r in results.values())
    total_cost = sum(r['cost_basis'] for r in results.values())
    total_gain = total_value - total_cost
    total_pct = (total_gain / total_cost * 100) if total_cost > 0 else 0

    return jsonify({
        'prices': results,
        'totals': {
            'current_value': round(total_value, 2),
            'cost_basis': round(total_cost, 2),
            'unrealized_gain': round(total_gain, 2),
            'unrealized_pct': round(total_pct, 1)
        },
        'cache_duration': CACHE_DURATION
    })

# VALID_INDICES imported from config.py

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
    """Fetch fresh index constituents from Wikipedia (for updates only)"""
    try:
        import pandas as pd
        import requests
        from io import StringIO

        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

        if index_name == 'sp500':
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
            resp = requests.get(url, headers=headers, timeout=30)
            table = pd.read_html(StringIO(resp.text))[0]
            return table['Symbol'].str.replace('.', '-', regex=False).tolist()
        elif index_name == 'nasdaq100':
            url = 'https://en.wikipedia.org/wiki/Nasdaq-100'
            resp = requests.get(url, headers=headers, timeout=30)
            table = pd.read_html(StringIO(resp.text))[4]
            return table['Ticker'].str.replace('.', '-', regex=False).tolist()
        elif index_name == 'dow30':
            url = 'https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average'
            resp = requests.get(url, headers=headers, timeout=30)
            table = pd.read_html(StringIO(resp.text))[2]  # Components table is at index 2
            return table['Symbol'].str.replace('.', '-', regex=False).tolist()
        elif index_name == 'sp600':
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_600_companies'
            resp = requests.get(url, headers=headers, timeout=30)
            table = pd.read_html(StringIO(resp.text))[0]
            return table['Symbol'].str.replace('.', '-', regex=False).tolist()
        elif index_name == 'russell2000':
            # Fetch from GitHub CSV (community-maintained list)
            url = 'https://raw.githubusercontent.com/ikoniaris/Russell2000/master/russell_2000_components.csv'
            resp = requests.get(url, headers=headers, timeout=30)
            df = pd.read_csv(StringIO(resp.text))
            return df['Ticker'].str.replace('.', '-', regex=False).tolist()
    except Exception as e:
        print(f"[Index] Error fetching {index_name} constituents: {e}")
    return []


INDEX_NAMES = {
    'all': ('All Indexes', 'All'),
    'sp500': ('S&P 500', 'S&P 500'),
    'nasdaq100': ('NASDAQ 100', 'NASDAQ 100'),
    'dow30': ('Dow Jones Industrial Average', 'DJIA'),
    'sp600': ('S&P SmallCap 600', 'S&P 600'),
    'russell2000': ('Russell 2000', 'Russell 2000')
}

# Individual indexes (excludes 'all')
INDIVIDUAL_INDICES = ['sp500', 'nasdaq100', 'dow30', 'sp600', 'russell2000']


def get_all_unique_tickers():
    """Get all unique tickers across all indexes (deduplicated)"""
    all_tickers = set()
    for index_name in INDIVIDUAL_INDICES:
        data = get_index_data(index_name)
        all_tickers.update(data.get('tickers', []))
    return sorted(list(all_tickers))

def get_ticker_indexes(ticker):
    """Get list of indexes a ticker belongs to"""
    indexes = []
    for index_name in INDIVIDUAL_INDICES:
        data = get_index_data(index_name)
        if ticker in data.get('tickers', []):
            short_name = INDEX_NAMES.get(index_name, (index_name, index_name))[1]
            indexes.append(short_name)
    return indexes

# Cache for ticker-to-index mapping (built once)
_ticker_index_cache = None

def get_all_ticker_indexes():
    """Get a mapping of all tickers to their indexes (cached)"""
    global _ticker_index_cache
    if _ticker_index_cache is None:
        _ticker_index_cache = {}
        for index_name in INDIVIDUAL_INDICES:
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

    # Get tickers from database
    tickers = db.get_index_tickers(index_name)

    # If no tickers in database, fetch from web and store
    if not tickers:
        print(f"[Index] No tickers in database for {index_name}, fetching from web...")
        tickers = fetch_index_tickers_from_web(index_name)
        if tickers:
            db.sync_index_membership(index_name, tickers)

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
        db.sync_index_membership(index_name, tickers)

# Keep old functions for backward compatibility
def get_sp500_data():
    return get_index_data('sp500')

def save_sp500_data(data):
    save_index_data('sp500', data)

def calculate_valuation(ticker):
    """Calculate valuation for a single ticker, returns dict or None on error"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        company_name = info.get('shortName', ticker)
        current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)

        # Get price momentum data
        fifty_two_week_high = info.get('fiftyTwoWeekHigh', 0)
        fifty_two_week_low = info.get('fiftyTwoWeekLow', 0)

        # Calculate % off 52-week high
        off_high_pct = None
        if fifty_two_week_high and current_price:
            off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

        # Get recent price history for momentum
        hist = stock.history(period='3mo')
        price_change_1m = None
        price_change_3m = None

        if hist is not None and len(hist) > 0:
            current = hist['Close'].iloc[-1] if len(hist) > 0 else None
            one_month_ago = hist['Close'].iloc[-22] if len(hist) >= 22 else hist['Close'].iloc[0]
            three_months_ago = hist['Close'].iloc[0]

            if current and one_month_ago:
                price_change_1m = ((current - one_month_ago) / one_month_ago) * 100
            if current and three_months_ago:
                price_change_3m = ((current - three_months_ago) / three_months_ago) * 100

        # Fetch income_stmt once for reuse in validation
        income_stmt = stock.income_stmt

        # Get validated EPS data (cross-checks SEC against yfinance)
        eps_data, eps_source, validation_info = get_validated_eps(ticker, stock, income_stmt)

        # Use SEC company name if available and SEC data was used
        if eps_source.startswith('sec'):
            sec_eps = sec_data.get_sec_eps(ticker)
            if sec_eps and sec_eps.get('company_name'):
                company_name = sec_eps['company_name']

        # Get dividends
        dividends = stock.dividends
        annual_dividend = 0
        last_dividend = None
        last_dividend_date = None

        if dividends is not None and len(dividends) > 0:
            from datetime import timedelta
            # Last dividend info
            last_dividend_date = dividends.index[-1].strftime('%Y-%m-%d')
            last_dividend = round(float(dividends.iloc[-1]), 4)

            # Annual dividend (last 12 months)
            one_year_ago = datetime.now() - timedelta(days=365)
            recent_dividends = dividends[dividends.index >= one_year_ago.strftime('%Y-%m-%d')]
            annual_dividend = sum(float(d) for d in recent_dividends)

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
        stock = yf.Ticker(ticker)
        info = stock.info

        # Check for rate limiting
        if info is None or (isinstance(info, dict) and len(info) == 0):
            if retry_count < max_retries:
                time.sleep(2 ** retry_count)  # Exponential backoff: 1s, 2s, 4s
                return fetch_eps_for_ticker(ticker, existing_valuation, retry_count + 1)
            return {'error': 'rate_limited', 'ticker': ticker}

        income_stmt = stock.income_stmt

        # Get validated EPS data
        eps_data, eps_source, validation_info = get_validated_eps(ticker, stock, income_stmt)

        # Get company name (prefer SEC if available)
        company_name = info.get('shortName', ticker)
        if eps_source.startswith('sec'):
            sec_eps = sec_data.get_sec_eps(ticker)
            if sec_eps and sec_eps.get('company_name'):
                company_name = sec_eps['company_name']

        # Get dividends
        dividends = stock.dividends
        annual_dividend = 0
        last_dividend = None
        last_dividend_date = None

        if dividends is not None and len(dividends) > 0:
            from datetime import timedelta
            last_dividend_date = dividends.index[-1].strftime('%Y-%m-%d')
            last_dividend = round(float(dividends.iloc[-1]), 4)
            one_year_ago = datetime.now() - timedelta(days=365)
            recent_dividends = dividends[dividends.index >= one_year_ago.strftime('%Y-%m-%d')]
            annual_dividend = sum(float(d) for d in recent_dividends)

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


def run_screener(index_name='all'):
    """
    Optimized background task for stock screening.

    Phase order (prioritizes fundamental data over prices):
    1. SEC EPS data - fetch/cache EPS from SEC EDGAR
    2. Dividends - fetch dividend data for tickers missing it
    3. Prices - batch download current prices
    4. Build valuations - combine all data
    """
    global screener_running, screener_progress, screener_current_index
    from concurrent.futures import ThreadPoolExecutor, as_completed

    log.info(f"=== SCREENER STARTED for index '{index_name}' ===")
    start_time = time.time()

    screener_running = True
    screener_current_index = index_name
    data = get_index_data(index_name)
    tickers = data['tickers']
    existing_valuations = data.get('valuations', {})  # Get cached data early for reuse
    index_display_name = data.get('short_name', index_name)

    log.info(f"Screener: {len(tickers)} tickers to process, {len(existing_valuations)} cached valuations")

    # Initialize progress
    screener_progress = {
        'current': 0, 'total': len(tickers),
        'ticker': 'Starting...',
        'status': 'running', 'phase': 'eps',
        'index': index_name, 'index_name': index_display_name
    }

    # =========================================================================
    # PHASE 1: SEC EPS Data (prioritize fundamental data)
    # =========================================================================
    log.info("Screener Phase 1: Loading SEC EPS data...")
    phase1_start = time.time()
    screener_progress['phase'] = 'eps'
    screener_progress['ticker'] = 'Loading SEC EPS data...'
    screener_progress['current'] = 0

    eps_results = {}
    sec_hits = 0
    sec_misses = 0

    for i, t in enumerate(tickers):
        if i % 100 == 0:
            screener_progress['current'] = i
            screener_progress['ticker'] = f'Loading SEC EPS... ({sec_hits} found)'

        # Try to get EPS from SEC cache first
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
                    'last_dividend': existing_valuations.get(t, {}).get('last_dividend'),
                    'last_dividend_date': existing_valuations.get(t, {}).get('last_dividend_date'),
                }
                sec_hits += 1
                continue

        # Fall back to existing valuation if no SEC data
        existing = existing_valuations.get(t, {})
        if existing.get('eps_avg') is not None:
            eps_results[t] = existing
        else:
            sec_misses += 1

    screener_progress['current'] = len(tickers)
    log.info(f"Screener Phase 1 complete: {time.time() - phase1_start:.1f}s, SEC EPS: {sec_hits} found, {sec_misses} missing")

    if not screener_running:
        screener_progress['status'] = 'cancelled'
        screener_running = False
        log.info("Screener cancelled after Phase 1")
        return

    # =========================================================================
    # PHASE 2: Dividends (fetch only if MISSING or STALE)
    # =========================================================================
    # Identify tickers needing dividend data:
    # - Missing: no annual_dividend value
    # - Stale: last_dividend_date is >4 months ago (most stocks pay quarterly)
    from datetime import timedelta
    four_months_ago = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')

    def needs_dividend_update(ticker):
        existing = existing_valuations.get(ticker, {})
        eps_info = eps_results.get(ticker, {})

        # Check if we have any dividend data
        annual_div = existing.get('annual_dividend') or eps_info.get('annual_dividend')
        if not annual_div:
            return True  # Missing - need to fetch

        # Check if dividend data is stale (last update >4 months ago)
        last_date = existing.get('last_dividend_date') or eps_info.get('last_dividend_date')
        if last_date and last_date < four_months_ago:
            return True  # Stale - might have new dividends

        return False  # Have recent dividend data, skip

    tickers_needing_dividends = [t for t in tickers if needs_dividend_update(t)]

    dividend_data = {}  # Store fetched dividend data
    if tickers_needing_dividends:
        log.info(f"Screener Phase 2: Fetching dividends for {len(tickers_needing_dividends)} tickers...")
        phase2_start = time.time()
        screener_progress['phase'] = 'dividends'
        screener_progress['ticker'] = f'Fetching dividends (0/{len(tickers_needing_dividends)})...'
        screener_progress['total'] = len(tickers_needing_dividends)
        screener_progress['current'] = 0

        dividend_count = 0
        rate_limit_count = 0
        error_count = 0
        backoff_delay = 0.3  # Start with 300ms between requests

        for i, ticker in enumerate(tickers_needing_dividends):
            if not screener_running:
                break
            if i % 50 == 0:
                screener_progress['current'] = i
                screener_progress['ticker'] = f'Fetching dividends... {i}/{len(tickers_needing_dividends)} ({dividend_count} found)'

            # Try up to 2 times with backoff for rate limits
            for attempt in range(2):
                try:
                    stock = yf.Ticker(ticker)
                    dividends = stock.dividends

                    if dividends is not None and len(dividends) > 0:
                        from datetime import timedelta
                        one_year_ago = datetime.now() - timedelta(days=365)
                        recent_dividends = dividends[dividends.index >= one_year_ago.strftime('%Y-%m-%d')]
                        annual_dividend = sum(float(d) for d in recent_dividends)

                        if annual_dividend > 0:
                            dividend_data[ticker] = {
                                'annual_dividend': round(annual_dividend, 2),
                                'last_dividend': round(float(dividends.iloc[-1]), 4),
                                'last_dividend_date': dividends.index[-1].strftime('%Y-%m-%d')
                            }
                            dividend_count += 1
                    break  # Success, exit retry loop

                except Exception as e:
                    error_str = str(e)
                    if 'Rate' in error_str or 'Too Many' in error_str:
                        rate_limit_count += 1
                        if attempt == 0:
                            # Increase backoff on rate limit
                            backoff_delay = min(backoff_delay * 2, 2.0)
                            time.sleep(backoff_delay * 2)  # Extra delay on rate limit
                        else:
                            error_count += 1
                    else:
                        error_count += 1
                        break  # Non-rate-limit error, don't retry

            time.sleep(backoff_delay)

        # Log summary of any issues
        if rate_limit_count > 0 or error_count > 0:
            log.warning(f"Screener dividends: {rate_limit_count} rate limits, {error_count} errors")

        screener_progress['current'] = len(tickers_needing_dividends)
        log.info(f"Screener Phase 2 complete: {time.time() - phase2_start:.1f}s, found dividends for {dividend_count}/{len(tickers_needing_dividends)} tickers")
    else:
        log.info("Screener Phase 2: All tickers have dividend data, skipping fetch")

    if not screener_running:
        screener_progress['status'] = 'cancelled'
        screener_running = False
        log.info("Screener cancelled after Phase 2")
        return

    # =========================================================================
    # PHASE 3: Batch download prices (fast - ~30 seconds for 2600 tickers)
    # =========================================================================
    log.info("Screener Phase 3: Batch downloading prices...")
    phase3_start = time.time()
    screener_progress['phase'] = 'prices'
    screener_progress['ticker'] = 'Batch downloading prices...'
    screener_progress['total'] = len(tickers)
    screener_progress['current'] = 0

    price_data = None
    info_cache = {}
    try:
        price_data = yf.download(tickers, period='3mo', progress=False, threads=True)

        # Reuse cached 52-week data from existing valuations (rarely changes)
        for t in tickers:
            existing = existing_valuations.get(t, {})
            if existing.get('fifty_two_week_high'):
                info_cache[t] = {
                    'fiftyTwoWeekHigh': existing.get('fifty_two_week_high', 0),
                    'fiftyTwoWeekLow': existing.get('fifty_two_week_low', 0),
                    'shortName': existing.get('company_name', t)
                }
        screener_progress['current'] = len(tickers)
        log.info(f"Screener Phase 3 complete: {time.time() - phase3_start:.1f}s, reusing cached 52-week data for {len(info_cache)} tickers")
    except Exception as e:
        log_error(f"Screener Phase 3 failed after {time.time() - phase3_start:.1f}s", e)
        price_data = None
        info_cache = {}

    if not screener_running:
        screener_progress['status'] = 'cancelled'
        screener_running = False
        return

    # =========================================================================
    # PHASE 4: Build valuations (VECTORIZED)
    # =========================================================================
    log.info("Screener Phase 4: Building valuations (vectorized)...")
    phase4_start = time.time()
    screener_progress['phase'] = 'combining'
    screener_progress['ticker'] = 'Building valuations...'
    screener_progress['total'] = len(tickers)

    valuations_batch = {}

    try:
        import numpy as np

        # Vectorized price calculations - all tickers at once
        if price_data is not None and len(tickers) > 1:
            close_prices = price_data['Close']

            # Current prices (last row)
            current_prices = close_prices.iloc[-1]

            # 3-month ago prices (first row)
            prices_3m_ago = close_prices.iloc[0]

            # 1-month ago prices (22 trading days back, or first if not enough data)
            if len(close_prices) >= 22:
                prices_1m_ago = close_prices.iloc[-22]
            else:
                prices_1m_ago = prices_3m_ago

            # Calculate percentage changes (vectorized)
            price_change_3m_all = ((current_prices - prices_3m_ago) / prices_3m_ago) * 100
            price_change_1m_all = ((current_prices - prices_1m_ago) / prices_1m_ago) * 100

            # Convert to dict for fast lookup
            current_prices_dict = current_prices.to_dict()
            price_change_3m_dict = price_change_3m_all.to_dict()
            price_change_1m_dict = price_change_1m_all.to_dict()
        else:
            current_prices_dict = {}
            price_change_3m_dict = {}
            price_change_1m_dict = {}

        # Phase 3.5: Retry failed tickers individually
        failed_tickers = [t for t in tickers if t not in current_prices_dict or
                          (isinstance(current_prices_dict.get(t), float) and np.isnan(current_prices_dict.get(t)))]

        if failed_tickers:
            screener_progress['phase'] = 'retrying'
            screener_progress['ticker'] = f'Retrying {len(failed_tickers)} failed tickers...'
            log.info(f"Screener: Retrying {len(failed_tickers)} tickers that failed batch download...")

            retry_count = 0
            for i, ticker in enumerate(failed_tickers):
                if not screener_running:
                    break
                if i % 50 == 0:
                    screener_progress['current'] = i
                    screener_progress['ticker'] = f'Retrying failed tickers... {i}/{len(failed_tickers)} ({retry_count} recovered)'

                try:
                    stock = yf.Ticker(ticker)
                    price = None
                    try:
                        price = stock.fast_info.get('lastPrice') or stock.fast_info.get('regularMarketPrice')
                    except Exception:
                        pass

                    if not price:
                        hist = stock.history(period='3mo')
                        if hist is not None and not hist.empty and 'Close' in hist.columns:
                            price = hist['Close'].iloc[-1]
                            if len(hist) >= 22:
                                price_1m_ago = hist['Close'].iloc[-22]
                                price_change_1m_dict[ticker] = ((price - price_1m_ago) / price_1m_ago) * 100
                            if len(hist) > 1:
                                price_3m_ago = hist['Close'].iloc[0]
                                price_change_3m_dict[ticker] = ((price - price_3m_ago) / price_3m_ago) * 100

                    if price and price > 0:
                        current_prices_dict[ticker] = float(price)
                        retry_count += 1

                    time.sleep(0.2)
                except Exception:
                    pass

            log.info(f"Screener: Recovered {retry_count}/{len(failed_tickers)} tickers from individual fetches")

        # Build valuations using pre-computed data
        # Note: Dividends were fetched in Phase 2 (before prices)
        now_iso = datetime.now().isoformat()

        for i, ticker in enumerate(tickers):
            if i % 500 == 0:  # Update progress every 500 tickers
                screener_progress['current'] = i + 1

            current_price = current_prices_dict.get(ticker)
            if current_price is None or (isinstance(current_price, float) and np.isnan(current_price)):
                continue

            price_change_3m = price_change_3m_dict.get(ticker)
            price_change_1m = price_change_1m_dict.get(ticker)

            # Handle NaN values
            if price_change_3m is not None and np.isnan(price_change_3m):
                price_change_3m = None
            if price_change_1m is not None and np.isnan(price_change_1m):
                price_change_1m = None

            # Get 52-week data from cache
            info = info_cache.get(ticker, {})
            fifty_two_week_high = info.get('fiftyTwoWeekHigh', 0)
            fifty_two_week_low = info.get('fiftyTwoWeekLow', 0)
            company_name = info.get('shortName', ticker)

            off_high_pct = None
            if fifty_two_week_high and current_price:
                off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

            # Get EPS data (from new fetch or existing)
            eps_info = eps_results.get(ticker) or existing_valuations.get(ticker, {})
            eps_avg = eps_info.get('eps_avg')

            # Get dividend data - check Phase 2 fetch first, then eps_info, then existing
            div_info = dividend_data.get(ticker, {})
            annual_dividend = (
                div_info.get('annual_dividend') or
                eps_info.get('annual_dividend') or
                existing_valuations.get(ticker, {}).get('annual_dividend') or
                0
            )

            # Use fetched company name if available
            if eps_info.get('company_name'):
                company_name = eps_info['company_name']

            # Calculate valuation
            estimated_value = None
            price_vs_value = None
            if eps_avg and eps_avg > 0:
                estimated_value = (eps_avg + annual_dividend) * 10
                if estimated_value > 0:
                    price_vs_value = ((current_price - estimated_value) / estimated_value) * 100

            # Determine selloff status
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

        screener_progress['current'] = len(tickers)

    except Exception as e:
        print(f"Error in vectorized valuation building: {e}")
        import traceback
        traceback.print_exc()

    data['last_updated'] = datetime.now().isoformat()

    # Save to consolidated data_manager
    log.info(f"Screener Phase 4 complete: {time.time() - phase4_start:.1f}s, built {len(valuations_batch)} valuations")
    if valuations_batch:
        data_manager.bulk_update_valuations(valuations_batch)
        log.info(f"Screener: Saved {len(valuations_batch)} valuations to data_manager")

    # Also save to index-specific file if not 'all'
    if index_name != 'all':
        save_index_data(index_name, data)

    total_duration = time.time() - start_time
    log.info(f"=== SCREENER COMPLETE for '{index_name}': {len(valuations_batch)} valuations in {total_duration:.1f}s ===")
    screener_progress['status'] = 'complete'
    screener_running = False

def run_quick_price_update(index_name='all'):
    """Fast update - batch download prices only, reuse ALL cached data (no individual API calls)"""
    global screener_running, screener_progress, screener_current_index
    import numpy as np
    import pandas as pd

    log.info(f"=== QUICK PRICE UPDATE STARTED for '{index_name}' ===")
    start_time = time.time()

    screener_running = True
    screener_current_index = index_name
    data = get_index_data(index_name)
    tickers_raw = data['tickers']
    existing_valuations = data.get('valuations', {})
    index_display_name = data.get('short_name', index_name)

    # Filter out excluded (delisted/unavailable) tickers
    excluded = load_excluded_tickers()
    if excluded:
        tickers = [t for t in tickers_raw if t not in excluded]
        excluded_count = len(tickers_raw) - len(tickers)
        if excluded_count > 0:
            print(f"[Quick Update] Excluding {excluded_count} previously unavailable tickers")
    else:
        tickers = tickers_raw

    screener_progress = {
        'current': 0, 'total': len(tickers),
        'ticker': 'Downloading prices...',
        'status': 'running', 'phase': 'prices',
        'index': index_name, 'index_name': index_display_name
    }

    try:
        # Phase 1: Batch download prices in chunks to avoid rate limiting
        all_price_data = []
        total_batches = (len(tickers) + YAHOO_BATCH_SIZE - 1) // YAHOO_BATCH_SIZE

        for batch_idx in range(0, len(tickers), YAHOO_BATCH_SIZE):
            if not screener_running:
                screener_progress['status'] = 'cancelled'
                return

            batch = tickers[batch_idx:batch_idx + YAHOO_BATCH_SIZE]
            batch_num = batch_idx // YAHOO_BATCH_SIZE + 1
            screener_progress['current'] = batch_idx
            screener_progress['ticker'] = f'Downloading batch {batch_num}/{total_batches}...'

            # Try up to 3 times with increasing delays
            for attempt in range(3):
                try:
                    batch_data = yf.download(batch, period='3mo', progress=False, threads=True)
                    if batch_data is not None and not batch_data.empty:
                        all_price_data.append(batch_data)
                        print(f"[Quick Update] Batch {batch_num}/{total_batches}: {len(batch)} tickers OK")
                        break
                except Exception as e:
                    if 'Rate' in str(e) and attempt < 2:
                        wait_time = (attempt + 1) * 5
                        print(f"[Quick Update] Batch {batch_num} rate limited, waiting {wait_time}s...")
                        screener_progress['ticker'] = f'Rate limited, waiting {wait_time}s...'
                        time.sleep(wait_time)
                    else:
                        print(f"[Quick Update] Batch {batch_num} error: {e}")
                        break

            # Delay between batches to avoid rate limiting
            if batch_idx + YAHOO_BATCH_SIZE < len(tickers):
                time.sleep(2)

        if not all_price_data:
            print("[Quick Update] No price data returned from any batch")
            screener_progress['status'] = 'complete'
            screener_running = False
            return

        # Combine all batch data
        price_data = pd.concat(all_price_data, axis=1)
        # Remove duplicate columns (same ticker might appear in multiple batches)
        price_data = price_data.loc[:, ~price_data.columns.duplicated()]

        screener_progress['current'] = len(tickers)
        screener_progress['ticker'] = 'Processing prices...'
        screener_progress['phase'] = 'combining'

        # Phase 2: Vectorized price calculations (no API calls)
        if 'Close' in price_data.columns:
            close_prices = price_data['Close']
        elif len(tickers) == 1:
            close_prices = price_data[['Close']].rename(columns={'Close': tickers[0]})
        else:
            # Handle multi-index columns from concat
            close_cols = [c for c in price_data.columns if c[0] == 'Close']
            if close_cols:
                close_prices = price_data[close_cols].droplevel(0, axis=1)
            else:
                print("[Quick Update] No Close prices found in data")
                screener_progress['status'] = 'complete'
                screener_running = False
                return

        current_prices = close_prices.iloc[-1]
        prices_3m_ago = close_prices.iloc[0]
        prices_1m_ago = close_prices.iloc[-22] if len(close_prices) >= 22 else prices_3m_ago

        # Vectorized percentage changes
        price_change_3m = ((current_prices - prices_3m_ago) / prices_3m_ago * 100).replace([np.inf, -np.inf], np.nan)
        price_change_1m = ((current_prices - prices_1m_ago) / prices_1m_ago * 100).replace([np.inf, -np.inf], np.nan)

        # Phase 3: Update valuations using cached data (no API calls)
        valuations_batch = {}
        updated_count = 0
        skipped_count = 0

        for i, ticker in enumerate(tickers):
            if not screener_running:
                screener_progress['status'] = 'cancelled'
                break

            if i % 200 == 0:
                screener_progress['current'] = i
                screener_progress['ticker'] = f'Building valuations... {i}/{len(tickers)}'

            try:
                if ticker not in current_prices.index or pd.isna(current_prices[ticker]):
                    skipped_count += 1
                    continue

                current_price = float(current_prices[ticker])

                # Get existing data (reuse EPS, 52-week, dividends, company name)
                existing = existing_valuations.get(ticker, {})
                eps_avg = existing.get('eps_avg')
                annual_dividend = existing.get('annual_dividend', 0)
                fifty_two_week_high = existing.get('fifty_two_week_high')
                fifty_two_week_low = existing.get('fifty_two_week_low')
                company_name = existing.get('company_name', ticker)

                # Calculate off-high using cached 52-week high
                off_high_pct = None
                if fifty_two_week_high and fifty_two_week_high > 0:
                    off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

                # Calculate valuation if we have EPS
                estimated_value = existing.get('estimated_value')
                price_vs_value = None
                if eps_avg and eps_avg > 0:
                    estimated_value = (eps_avg + annual_dividend) * 10
                    if estimated_value > 0:
                        price_vs_value = ((current_price - estimated_value) / estimated_value) * 100

                # Price changes from vectorized calculations
                pc_3m = price_change_3m.get(ticker) if ticker in price_change_3m.index else None
                pc_1m = price_change_1m.get(ticker) if ticker in price_change_1m.index else None

                # Determine selloff status
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
                    **existing,  # Keep EPS, dividend, 52-week data
                    'ticker': ticker,
                    'company_name': company_name,
                    'current_price': round(current_price, 2),
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

        screener_progress['current'] = len(tickers)
        screener_progress['ticker'] = f'Saving {updated_count} valuations...'
        log.info(f"Quick Update: Saving {updated_count} valuations ({skipped_count} skipped)")

        # Save to consolidated data_manager
        if valuations_batch:
            data_manager.bulk_update_valuations(valuations_batch)

    except Exception as e:
        log_error(f"Quick Update failed", e)

    total_duration = time.time() - start_time
    log.info(f"=== QUICK PRICE UPDATE COMPLETE for '{index_name}': {updated_count} updated in {total_duration:.1f}s ===")
    screener_progress['status'] = 'complete'
    screener_running = False

def run_smart_update(index_name='all'):
    """Smart update - prioritizes missing tickers, then updates prices for existing ones"""
    global screener_running, screener_progress, screener_current_index

    log.info(f"=== SMART UPDATE STARTED for '{index_name}' ===")
    start_time = time.time()

    screener_running = True
    screener_current_index = index_name
    data = get_index_data(index_name)
    tickers = data['tickers']
    existing_valuations = set(data.get('valuations', {}).keys())
    index_display_name = data.get('short_name', index_name)

    # Separate missing and existing tickers
    missing_tickers = [t for t in tickers if t not in existing_valuations]
    existing_tickers = [t for t in tickers if t in existing_valuations]

    total_work = len(missing_tickers) + len(existing_tickers)
    screener_progress = {
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
        if not screener_running:
            screener_progress['status'] = 'cancelled'
            break

        screener_progress['current'] = i + 1
        screener_progress['ticker'] = f"[NEW] {ticker}"
        screener_progress['phase'] = 'missing'

        valuation = calculate_valuation(ticker)
        if valuation:
            data['valuations'][ticker] = valuation
            # Save periodically to preserve progress
            if (i + 1) % 10 == 0:
                save_index_data(index_name, data)

        time.sleep(0.5)

    # Phase 2: Quick price update for existing tickers (no individual API calls)
    if screener_running and existing_tickers:
        import numpy as np

        screener_progress['phase'] = 'prices'
        screener_progress['ticker'] = 'Batch downloading prices...'

        try:
            price_data = yf.download(existing_tickers, period='3mo', progress=False, threads=True)

            if price_data is not None and not price_data.empty:
                # Vectorized price calculations
                if len(existing_tickers) > 1:
                    close_prices = price_data['Close']
                else:
                    close_prices = price_data[['Close']].rename(columns={'Close': existing_tickers[0]})

                current_prices = close_prices.iloc[-1]
                prices_3m_ago = close_prices.iloc[0]
                prices_1m_ago = close_prices.iloc[-22] if len(close_prices) >= 22 else prices_3m_ago

                price_change_3m = ((current_prices - prices_3m_ago) / prices_3m_ago * 100).replace([np.inf, -np.inf], np.nan)
                price_change_1m = ((current_prices - prices_1m_ago) / prices_1m_ago * 100).replace([np.inf, -np.inf], np.nan)

                for i, ticker in enumerate(existing_tickers):
                    if not screener_running:
                        screener_progress['status'] = 'cancelled'
                        break

                    screener_progress['current'] = len(missing_tickers) + i + 1
                    screener_progress['ticker'] = ticker

                    try:
                        if ticker not in current_prices.index or pd.isna(current_prices[ticker]):
                            continue

                        current_price = float(current_prices[ticker])

                        # Get existing data (reuse 52-week, EPS, dividends)
                        existing = data.get('valuations', {}).get(ticker, {})
                        eps_avg = existing.get('eps_avg')
                        annual_dividend = existing.get('annual_dividend', 0)
                        fifty_two_week_high = existing.get('fifty_two_week_high')
                        fifty_two_week_low = existing.get('fifty_two_week_low')

                        # Calculate off-high using cached data
                        off_high_pct = None
                        if fifty_two_week_high and fifty_two_week_high > 0:
                            off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

                        # Calculate valuation if we have EPS
                        estimated_value = existing.get('estimated_value')
                        price_vs_value = None
                        if eps_avg and eps_avg > 0:
                            estimated_value = (eps_avg + annual_dividend) * 10
                            if estimated_value > 0:
                                price_vs_value = ((current_price - estimated_value) / estimated_value) * 100

                        # Price changes from vectorized calculations
                        pc_3m = price_change_3m.get(ticker) if ticker in price_change_3m.index else None
                        pc_1m = price_change_1m.get(ticker) if ticker in price_change_1m.index else None

                        # Determine selloff status
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

                        # Update valuation (keep cached 52-week data)
                        data['valuations'][ticker] = {
                            **existing,
                            'ticker': ticker,
                            'current_price': round(current_price, 2),
                            'estimated_value': round(estimated_value, 2) if estimated_value else None,
                            'price_vs_value': round(price_vs_value, 1) if price_vs_value is not None else None,
                            'off_high_pct': round(off_high_pct, 1) if off_high_pct is not None else None,
                            'price_change_1m': round(float(pc_1m), 1) if pc_1m is not None and not pd.isna(pc_1m) else None,
                            'price_change_3m': round(float(pc_3m), 1) if pc_3m is not None and not pd.isna(pc_3m) else None,
                            'in_selloff': in_selloff,
                            'selloff_severity': selloff_severity,
                            'updated': datetime.now().isoformat()
                        }

                    except Exception as e:
                        print(f"[Smart Update] Error updating {ticker}: {e}")
                        continue

        except Exception as e:
            print(f"[Smart Update] Error in price phase: {e}")
            import traceback
            traceback.print_exc()

    data['last_updated'] = datetime.now().isoformat()

    # Save to consolidated data_manager
    if data.get('valuations'):
        data_manager.bulk_update_valuations(data['valuations'])
        log.info(f"Smart Update: Saved {len(data['valuations'])} valuations")

    # Also save to index-specific file if not 'all'
    if index_name != 'all':
        save_index_data(index_name, data)

    total_duration = time.time() - start_time
    log.info(f"=== SMART UPDATE COMPLETE for '{index_name}' in {total_duration:.1f}s ===")
    screener_progress['status'] = 'complete'
    screener_running = False


@app.route('/api/indices')
def api_indices():
    """Get list of available indices"""
    indices = []
    for index_name in VALID_INDICES:
        data = get_index_data(index_name)
        indices.append({
            'id': index_name,
            'name': data.get('name', index_name),
            'short_name': data.get('short_name', index_name),
            'ticker_count': len(data.get('tickers', [])),
            'last_updated': data.get('last_updated'),
            'valuations_count': len(data.get('valuations', {}))
        })
    return jsonify(indices)

@app.route('/api/screener')
def api_screener():
    """Get cached screener results for specified index"""
    index_name = request.args.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    data = get_index_data(index_name)

    # Filter to undervalued stocks (price_vs_value < -20)
    undervalued = []
    all_valuations = []

    for ticker, val in data.get('valuations', {}).items():
        all_valuations.append(val)
        if val.get('price_vs_value') is not None and val['price_vs_value'] < -20:
            undervalued.append(val)

    # Sort undervalued by most undervalued first
    undervalued.sort(key=lambda x: x['price_vs_value'])
    all_valuations.sort(key=lambda x: x.get('price_vs_value') or 999)

    total_tickers = len(data.get('tickers', []))
    valuations_count = len(data.get('valuations', {}))
    missing_count = total_tickers - valuations_count

    return jsonify({
        'index': index_name,
        'index_name': data.get('short_name', index_name),
        'last_updated': data.get('last_updated'),
        'total_tickers': total_tickers,
        'valuations_count': valuations_count,
        'missing_count': missing_count,
        'undervalued': undervalued,
        'all_valuations': all_valuations
    })

@app.route('/api/screener/start', methods=['POST'])
def api_screener_start():
    """Start the screener background task"""
    global screener_running

    if screener_running:
        return jsonify({'error': 'Screener already running'}), 400

    # Get index from request body or default to all
    data = request.get_json() or {}
    index_name = data.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    thread = threading.Thread(target=run_screener, args=(index_name,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'index': index_name})

@app.route('/api/screener/quick-update', methods=['POST'])
def api_screener_quick_update():
    """Quick price update - uses batch download, keeps cached EPS"""
    global screener_running

    if screener_running:
        return jsonify({'error': 'Screener already running'}), 400

    data = request.get_json() or {}
    index_name = data.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    thread = threading.Thread(target=run_quick_price_update, args=(index_name,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'index': index_name, 'mode': 'quick'})


@app.route('/api/screener/smart-update', methods=['POST'])
def api_screener_smart_update():
    """Smart update - only fetches missing tickers, then updates prices for all"""
    global screener_running

    if screener_running:
        return jsonify({'error': 'Screener already running'}), 400

    req_data = request.get_json() or {}
    index_name = req_data.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    thread = threading.Thread(target=run_smart_update, args=(index_name,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'index': index_name, 'mode': 'smart'})

@app.route('/api/screener/stop', methods=['POST'])
def api_screener_stop():
    """Stop the screener"""
    global screener_running
    screener_running = False
    return jsonify({'status': 'stopping'})


def run_global_refresh():
    """
    Optimized refresh across all indexes:
    1. Batch download prices in chunks to avoid rate limiting
    2. Use SEC data for EPS (NO API calls)
    3. Reuse cached data for 52-week/dividends (NO API calls)
    """
    global screener_running, screener_progress
    import numpy as np
    import pandas as pd

    screener_running = True

    # Collect all unique tickers across all indexes
    all_tickers_raw = get_all_unique_tickers()

    # Filter out excluded (delisted/unavailable) tickers
    excluded = load_excluded_tickers()
    excluded_count = 0
    if excluded:
        all_tickers = [t for t in all_tickers_raw if t not in excluded]
        excluded_count = len(all_tickers_raw) - len(all_tickers)
        print(f"[Refresh] Excluding {excluded_count} previously unavailable tickers")
    else:
        all_tickers = all_tickers_raw

    total_tickers = len(all_tickers)

    # Get existing valuations for reuse
    existing_valuations = data_manager.load_valuations().get('valuations', {})

    screener_progress = {
        'current': 0,
        'total': total_tickers,
        'ticker': 'Starting...',
        'status': 'running',
        'index': 'all',
        'index_name': 'All Indexes',
        'phase': 'prices'
    }

    # Phase 1: Batch download prices in chunks to avoid rate limiting
    all_price_data = []
    total_batches = (total_tickers + YAHOO_BATCH_SIZE - 1) // YAHOO_BATCH_SIZE

    print(f"[Refresh] Downloading prices for {total_tickers} tickers in {total_batches} batches...")

    for batch_idx in range(0, total_tickers, YAHOO_BATCH_SIZE):
        if not screener_running:
            screener_progress['status'] = 'cancelled'
            return

        batch = all_tickers[batch_idx:batch_idx + YAHOO_BATCH_SIZE]
        batch_num = batch_idx // YAHOO_BATCH_SIZE + 1
        screener_progress['current'] = batch_idx
        screener_progress['ticker'] = f'Downloading batch {batch_num}/{total_batches}...'

        # Try up to 3 times with increasing delays
        for attempt in range(3):
            try:
                batch_data = yf.download(batch, period='3mo', progress=False, threads=True)
                if batch_data is not None and not batch_data.empty:
                    all_price_data.append(batch_data)
                    print(f"[Refresh] Batch {batch_num}/{total_batches}: {len(batch)} tickers OK")
                    break
            except Exception as e:
                if 'Rate' in str(e) and attempt < 2:
                    wait_time = (attempt + 1) * 5
                    print(f"[Refresh] Batch {batch_num} rate limited, waiting {wait_time}s...")
                    screener_progress['ticker'] = f'Rate limited, waiting {wait_time}s...'
                    time.sleep(wait_time)
                else:
                    print(f"[Refresh] Batch {batch_num} error: {e}")
                    break

        # Delay between batches to avoid rate limiting
        if batch_idx + YAHOO_BATCH_SIZE < total_tickers:
            time.sleep(2)

    if not screener_running:
        screener_progress['status'] = 'cancelled'
        return

    # Phase 2: Combine and calculate price changes
    screener_progress['phase'] = 'calculating'
    screener_progress['ticker'] = 'Calculating price changes...'
    screener_progress['current'] = total_tickers

    current_prices_dict = {}
    price_change_1m_dict = {}
    price_change_3m_dict = {}

    if all_price_data:
        try:
            # Combine all batch data
            price_data = pd.concat(all_price_data, axis=1)
            price_data = price_data.loc[:, ~price_data.columns.duplicated()]

            # Extract close prices
            if 'Close' in price_data.columns:
                close_prices = price_data['Close']
            else:
                close_cols = [c for c in price_data.columns if c[0] == 'Close']
                if close_cols:
                    close_prices = price_data[close_cols].droplevel(0, axis=1)
                else:
                    print("[Refresh] No Close prices found")
                    close_prices = None

            if close_prices is not None:
                current_prices = close_prices.iloc[-1]
                prices_3m_ago = close_prices.iloc[0]
                prices_1m_ago = close_prices.iloc[-22] if len(close_prices) >= 22 else prices_3m_ago

                price_change_3m_all = ((current_prices - prices_3m_ago) / prices_3m_ago) * 100
                price_change_1m_all = ((current_prices - prices_1m_ago) / prices_1m_ago) * 100

                current_prices_dict = current_prices.to_dict()
                price_change_3m_dict = price_change_3m_all.to_dict()
                price_change_1m_dict = price_change_1m_all.to_dict()
                print(f"[Refresh] Got prices for {len(current_prices_dict)} tickers")
        except Exception as e:
            print(f"[Refresh] Error in vectorized calculations: {e}")
            import traceback
            traceback.print_exc()

    # Phase 3: Retry failed tickers individually (handles rate-limited tickers)
    failed_tickers = [t for t in all_tickers if t not in current_prices_dict or
                      (isinstance(current_prices_dict.get(t), float) and np.isnan(current_prices_dict.get(t)))]

    if failed_tickers:
        screener_progress['phase'] = 'retrying'
        screener_progress['ticker'] = f'Retrying {len(failed_tickers)} failed tickers...'
        print(f"[Refresh] Retrying {len(failed_tickers)} tickers that failed batch download...")

        retry_count = 0
        for i, ticker in enumerate(failed_tickers):
            if not screener_running:
                break
            if i % 50 == 0:
                screener_progress['current'] = i
                screener_progress['ticker'] = f'Retrying failed tickers... {i}/{len(failed_tickers)} ({retry_count} recovered)'

            try:
                stock = yf.Ticker(ticker)
                # Try fast_info first (faster)
                price = None
                try:
                    price = stock.fast_info.get('lastPrice') or stock.fast_info.get('regularMarketPrice')
                except Exception:
                    pass

                # Fall back to history if fast_info fails
                if not price:
                    hist = stock.history(period='3mo')
                    if hist is not None and not hist.empty and 'Close' in hist.columns:
                        price = hist['Close'].iloc[-1]
                        # Also get price changes
                        if len(hist) >= 22:
                            price_1m_ago = hist['Close'].iloc[-22]
                            price_change_1m_dict[ticker] = ((price - price_1m_ago) / price_1m_ago) * 100
                        if len(hist) > 1:
                            price_3m_ago = hist['Close'].iloc[0]
                            price_change_3m_dict[ticker] = ((price - price_3m_ago) / price_3m_ago) * 100

                if price and price > 0:
                    current_prices_dict[ticker] = float(price)
                    retry_count += 1

                time.sleep(0.2)  # Rate limit individual fetches
            except Exception as e:
                pass  # Skip this ticker

        print(f"[Refresh] Recovered {retry_count}/{len(failed_tickers)} tickers from individual fetches")

    # Phase 3.5: Fetch dividends for tickers missing dividend data
    tickers_needing_dividends = [
        t for t in all_tickers
        if t in current_prices_dict and not existing_valuations.get(t, {}).get('annual_dividend')
    ]

    if tickers_needing_dividends:
        screener_progress['phase'] = 'dividends'
        screener_progress['ticker'] = f'Fetching dividends for {len(tickers_needing_dividends)} tickers...'
        print(f"[Refresh] Fetching dividends for {len(tickers_needing_dividends)} tickers...")

        dividend_count = 0
        for i, ticker in enumerate(tickers_needing_dividends):
            if not screener_running:
                break
            if i % 100 == 0:
                screener_progress['current'] = i
                screener_progress['ticker'] = f'Fetching dividends... {i}/{len(tickers_needing_dividends)} ({dividend_count} with dividends)'

            try:
                stock = yf.Ticker(ticker)
                dividends = stock.dividends

                if dividends is not None and len(dividends) > 0:
                    from datetime import timedelta
                    one_year_ago = datetime.now() - timedelta(days=365)
                    recent_dividends = dividends[dividends.index >= one_year_ago.strftime('%Y-%m-%d')]
                    annual_dividend = sum(float(d) for d in recent_dividends)

                    if annual_dividend > 0:
                        if ticker not in existing_valuations:
                            existing_valuations[ticker] = {}
                        existing_valuations[ticker]['annual_dividend'] = round(annual_dividend, 2)
                        existing_valuations[ticker]['last_dividend'] = round(float(dividends.iloc[-1]), 4)
                        existing_valuations[ticker]['last_dividend_date'] = dividends.index[-1].strftime('%Y-%m-%d')
                        dividend_count += 1

                time.sleep(0.15)
            except Exception:
                pass

        print(f"[Refresh] Found dividends for {dividend_count}/{len(tickers_needing_dividends)} tickers")

    # Phase 4: Build valuations using SEC data + cached data (NO API calls)
    screener_progress['phase'] = 'valuations'
    ticker_valuations = {}
    now_iso = datetime.now().isoformat()

    # Track skip reasons
    skip_reasons = {
        'no_price': [],      # No price data from Yahoo
        'no_eps': [],        # Has price but no EPS data
        'success': [],       # Successfully created valuation
        'success_no_eps': [] # Has price but no EPS (partial data)
    }

    for i, ticker in enumerate(all_tickers):
        if not screener_running:
            screener_progress['status'] = 'cancelled'
            return

        if i % 100 == 0:
            screener_progress['current'] = i
            screener_progress['ticker'] = f'Building valuations... {i}/{total_tickers}'

        # Get price from batch download
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

        # Get EPS from SEC cache (NO API call)
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

        # Fall back to existing cached valuation
        existing = existing_valuations.get(ticker, {})
        if eps_avg is None and existing.get('eps_avg'):
            eps_avg = existing['eps_avg']
            eps_years = existing.get('eps_years', 0)
            eps_source = existing.get('eps_source', 'cached')

        if existing.get('company_name'):
            company_name = existing['company_name']

        # Reuse cached 52-week and dividend data (NO API call)
        fifty_two_week_high = existing.get('fifty_two_week_high', 0)
        fifty_two_week_low = existing.get('fifty_two_week_low', 0)
        annual_dividend = existing.get('annual_dividend', 0)

        # Calculate valuation
        estimated_value = None
        price_vs_value = None
        if eps_avg and eps_avg > 0:
            estimated_value = (eps_avg + annual_dividend) * 10
            if current_price and estimated_value > 0:
                price_vs_value = ((current_price - estimated_value) / estimated_value) * 100

        # Calculate off high
        off_high_pct = None
        if fifty_two_week_high and current_price:
            off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

        # Determine selloff status
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

        # Track success with/without EPS
        if eps_avg is not None:
            skip_reasons['success'].append(ticker)
        else:
            skip_reasons['success_no_eps'].append(ticker)

    screener_progress['current'] = total_tickers
    print(f"[Refresh] Built {len(ticker_valuations)} valuations")

    # Phase 4: Save to consolidated storage
    screener_progress['phase'] = 'saving'
    screener_progress['ticker'] = 'Saving...'

    # Save to consolidated valuations
    data_manager.bulk_update_valuations(ticker_valuations)

    # Update ticker status with SEC info
    ticker_status_updates = {}
    for ticker, val in ticker_valuations.items():
        sec_status = 'available' if val.get('eps_source') == 'sec' else 'unavailable'
        ticker_status_updates[ticker] = {
            'sec_status': sec_status,
            'valuation_updated': now_iso,
            'company_name': val.get('company_name')
        }
    data_manager.bulk_update_ticker_status(ticker_status_updates)

    # Record failures and successes (only excludes after 3+ consecutive failures)
    successful_tickers = skip_reasons['success'] + skip_reasons['success_no_eps']
    if skip_reasons['no_price'] or successful_tickers:
        newly_excluded = record_ticker_failures(skip_reasons['no_price'], successful_tickers)
        if newly_excluded:
            print(f"[Refresh] Newly excluded (3+ failures): {len(newly_excluded)} tickers")

    # Save skip reasons summary
    skip_summary = {
        'last_refresh': now_iso,
        'total_tickers': total_tickers,
        'excluded_count': excluded_count,  # Tickers skipped because they're in excluded list
        'no_price_data': len(skip_reasons['no_price']),
        'no_eps_data': len(skip_reasons['success_no_eps']),
        'full_data': len(skip_reasons['success']),
        'no_price_tickers': skip_reasons['no_price'][:50],  # Store first 50 for display
        'no_eps_tickers': skip_reasons['success_no_eps'][:50]
    }
    try:
        db.set_metadata('refresh_summary', json.dumps(skip_summary))
    except Exception as e:
        print(f"[Refresh] Error saving skip summary: {e}")

    screener_progress['status'] = 'complete'
    screener_progress['ticker'] = f'Done - {len(ticker_valuations)} valuations updated'
    screener_running = False
    print(f"[Refresh] Complete - {len(ticker_valuations)} valuations saved")
    print(f"[Refresh] Summary: {len(skip_reasons['success'])} with EPS, {len(skip_reasons['success_no_eps'])} without EPS, {len(skip_reasons['no_price'])} no price, {excluded_count} excluded")


@app.route('/api/refresh', methods=['POST'])
def api_global_refresh():
    """Start a global refresh of all data - uses run_screener with 'all' index"""
    global screener_running

    if screener_running:
        return jsonify({'error': 'Update already running'}), 400

    # Use run_screener which has the correct phase order: EPS -> Dividends -> Prices
    thread = threading.Thread(target=run_screener, args=('all',))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'mode': 'global'})

@app.route('/api/screener/progress')
def api_screener_progress():
    """Get screener progress"""
    return jsonify(screener_progress)

@app.route('/api/recommendations')
def api_recommendations():
    """
    Get top 10 stock recommendations based on:
    - High undervaluation (price_vs_value - more negative = more undervalued)
    - High to mid annual dividend yield
    - High selloff pressure (off_high_pct, in_selloff)
    """
    # Load all valuations
    all_valuations = data_manager.load_valuations().get('valuations', {})

    if not all_valuations:
        return jsonify({'recommendations': [], 'error': 'No valuation data available'})

    # Get ticker-to-index mapping
    ticker_indexes = get_all_ticker_indexes()

    scored_stocks = []

    for ticker, val in all_valuations.items():
        # Skip stocks without key metrics
        if not val.get('current_price') or val.get('current_price', 0) <= 0:
            continue
        if val.get('price_vs_value') is None:
            continue

        current_price = val.get('current_price', 0)
        price_vs_value = val.get('price_vs_value', 0)
        annual_dividend = val.get('annual_dividend', 0)
        off_high_pct = val.get('off_high_pct', 0) or 0
        in_selloff = val.get('in_selloff', False)
        selloff_severity = val.get('selloff_severity', 'none')
        eps_years = val.get('eps_years', 0)

        # Calculate dividend yield
        dividend_yield = (annual_dividend / current_price * 100) if current_price > 0 else 0

        # Skip stocks with very low data quality
        if eps_years < RECOMMENDATION_MIN_EPS_YEARS:
            continue

        # Calculate composite score (higher = better recommendation)
        # 1. Undervaluation score: more negative price_vs_value = better
        #    -50% undervalued gets 50 points, 0% gets 0, +50% overvalued gets -50
        undervalue_score = -price_vs_value  # Flip sign so undervalued = positive

        # 2. Dividend score: dividend is important!
        #    - No dividend: penalty (from config)
        #    - 0-6% yield mapped to 0-max points (from config)
        if dividend_yield <= 0:
            dividend_score = DIVIDEND_NO_DIVIDEND_PENALTY
        else:
            dividend_score = min(dividend_yield * DIVIDEND_POINTS_PER_PERCENT, DIVIDEND_MAX_POINTS)

        # 3. Selloff score: based on how far off the high
        #    -50% off high = 50 points, -20% = 20 points, 0% = 0 points
        selloff_score = -off_high_pct if off_high_pct < 0 else 0

        # Bonus for being in active selloff
        if in_selloff:
            if selloff_severity == 'severe':
                selloff_score += SELLOFF_SEVERE_BONUS
            elif selloff_severity in ('moderate', 'high'):
                selloff_score += SELLOFF_MODERATE_BONUS
            elif selloff_severity == 'recent':
                selloff_score += SELLOFF_RECENT_BONUS

        # Total score with weights from config
        total_score = (undervalue_score * SCORING_WEIGHTS['undervaluation']) + \
                      (dividend_score * SCORING_WEIGHTS['dividend']) + \
                      (selloff_score * SCORING_WEIGHTS['selloff'])

        # Build reasoning
        reasons = []

        # Undervaluation reason
        if price_vs_value <= -30:
            reasons.append(f"Significantly undervalued at {price_vs_value:.0f}% below estimated value")
        elif price_vs_value <= -15:
            reasons.append(f"Undervalued at {price_vs_value:.0f}% below estimated value")
        elif price_vs_value <= 0:
            reasons.append(f"Slightly undervalued at {price_vs_value:.0f}% below estimated value")

        # Dividend reason
        if dividend_yield >= 4:
            reasons.append(f"High dividend yield of {dividend_yield:.1f}%")
        elif dividend_yield >= 2:
            reasons.append(f"Solid dividend yield of {dividend_yield:.1f}%")
        elif dividend_yield >= 1:
            reasons.append(f"Moderate dividend yield of {dividend_yield:.1f}%")

        # Selloff reason
        if off_high_pct <= -40:
            reasons.append(f"Down {-off_high_pct:.0f}% from 52-week high - severe selloff")
        elif off_high_pct <= -25:
            reasons.append(f"Down {-off_high_pct:.0f}% from 52-week high - significant pullback")
        elif off_high_pct <= -15:
            reasons.append(f"Down {-off_high_pct:.0f}% from 52-week high - moderate pullback")

        # Data quality note
        if eps_years >= 10:
            reasons.append(f"Strong {eps_years}-year earnings history")
        elif eps_years >= 8:
            reasons.append(f"Good {eps_years}-year earnings history")

        scored_stocks.append({
            'ticker': ticker,
            'company_name': val.get('company_name', ticker),
            'current_price': current_price,
            'estimated_value': val.get('estimated_value'),
            'price_vs_value': price_vs_value,
            'annual_dividend': annual_dividend,
            'dividend_yield': round(dividend_yield, 2),
            'off_high_pct': off_high_pct,
            'in_selloff': in_selloff,
            'selloff_severity': selloff_severity,
            'eps_years': eps_years,
            'score': round(total_score, 1),
            'reasons': reasons,
            'indexes': ticker_indexes.get(ticker, []),
            'updated': val.get('updated')
        })

    # Sort by score descending and take top 10
    scored_stocks.sort(key=lambda x: x['score'], reverse=True)
    top_10 = scored_stocks[:10]

    return jsonify({
        'recommendations': top_10,
        'total_analyzed': len(scored_stocks),
        'criteria': {
            'undervaluation': 'Stocks trading below estimated value (based on 10x average EPS)',
            'dividend': 'Higher dividend yield preferred',
            'selloff': 'Stocks that have pulled back from highs (potential buying opportunity)'
        }
    })

@app.route('/api/screener/update-dividends', methods=['POST'])
def api_screener_update_dividends():
    """Quick update of just dividend data for cached stocks"""
    global screener_running, screener_progress

    if screener_running:
        return jsonify({'error': 'Screener already running'}), 400

    # Get index from request body or default to all
    req_data = request.get_json() or {}
    index_name = req_data.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    def update_dividends(idx):
        global screener_running, screener_progress
        screener_running = True

        data = get_index_data(idx)
        tickers = list(data.get('valuations', {}).keys())
        screener_progress = {'current': 0, 'total': len(tickers), 'ticker': '', 'status': 'running', 'index': idx}

        for i, ticker in enumerate(tickers):
            if not screener_running:
                screener_progress['status'] = 'cancelled'
                break

            screener_progress['current'] = i + 1
            screener_progress['ticker'] = ticker

            try:
                stock = yf.Ticker(ticker)
                dividends = stock.dividends

                if dividends is not None and len(dividends) > 0:
                    # Get last dividend
                    last_div_date = dividends.index[-1]
                    last_div_amount = float(dividends.iloc[-1])

                    # Annual dividend (last 12 months)
                    from datetime import timedelta
                    one_year_ago = datetime.now() - timedelta(days=365)
                    recent_dividends = dividends[dividends.index >= one_year_ago.strftime('%Y-%m-%d')]
                    annual_dividend = sum(float(d) for d in recent_dividends)

                    # Update the cached data
                    if ticker in data['valuations']:
                        data['valuations'][ticker]['annual_dividend'] = round(annual_dividend, 2)
                        data['valuations'][ticker]['last_dividend'] = round(last_div_amount, 4)
                        data['valuations'][ticker]['last_dividend_date'] = last_div_date.strftime('%Y-%m-%d')

                        # Recalculate estimated value
                        eps_avg = data['valuations'][ticker].get('eps_avg', 0)
                        data['valuations'][ticker]['estimated_value'] = round((eps_avg + annual_dividend) * 10, 2)
                else:
                    if ticker in data['valuations']:
                        data['valuations'][ticker]['annual_dividend'] = 0
                        data['valuations'][ticker]['last_dividend'] = None
                        data['valuations'][ticker]['last_dividend_date'] = None

            except Exception as e:
                print(f"Error updating dividend for {ticker}: {e}")

            time.sleep(0.3)  # Faster since we're only getting dividends

        data['dividends_updated'] = datetime.now().isoformat()
        save_index_data(idx, data)

        screener_progress['status'] = 'complete'
        screener_running = False

    thread = threading.Thread(target=update_dividends, args=(index_name,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'index': index_name})

@app.route('/api/valuation/<ticker>')
def api_valuation(ticker):
    """Calculate stock valuation using EPS and dividend formula"""
    ticker = ticker.upper()

    try:
        stock = yf.Ticker(ticker)

        # Get company info
        info = stock.info
        company_name = info.get('shortName', ticker)
        current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)

        # Fetch income_stmt once for reuse in validation
        income_stmt = stock.income_stmt

        # Get validated EPS data (cross-checks SEC against yfinance)
        eps_data, eps_source, validation_info = get_validated_eps(ticker, stock, income_stmt)

        # Use SEC company name if available and SEC data was used
        if eps_source.startswith('sec'):
            sec_eps = sec_data.get_sec_eps(ticker)
            if sec_eps and sec_eps.get('company_name'):
                company_name = sec_eps['company_name']

        # Get selloff metrics
        selloff_metrics = calculate_selloff_metrics(stock)

        # Get dividend info
        dividends = stock.dividends
        annual_dividend = 0
        dividend_info = []

        if dividends is not None and len(dividends) > 0:
            from datetime import timedelta
            one_year_ago = datetime.now() - timedelta(days=365)
            recent_dividends = dividends[dividends.index >= one_year_ago.strftime('%Y-%m-%d')]

            for date, amount in recent_dividends.items():
                dividend_info.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'amount': float(amount)
                })
                annual_dividend += float(amount)

        # Calculate valuation: (Average EPS over up to 8 years + Annual Dividend) × 10
        min_years_recommended = 8
        eps_avg = None
        estimated_value = None
        price_vs_value = None

        if len(eps_data) > 0:
            eps_avg = sum(e['eps'] for e in eps_data) / len(eps_data)

            # Formula: (Average EPS + Annual Dividend) × 10
            estimated_value = (eps_avg + annual_dividend) * 10

            if current_price and current_price > 0 and estimated_value > 0:
                price_vs_value = ((current_price - estimated_value) / estimated_value) * 100

        return jsonify({
            'ticker': ticker,
            'company_name': company_name,
            'current_price': round(current_price, 2) if current_price else None,
            'eps_data': eps_data,
            'eps_years': len(eps_data),
            'eps_source': eps_source,
            'eps_validation': validation_info,
            'eps_avg': round(eps_avg, 2) if eps_avg else None,
            'min_years_recommended': min_years_recommended,
            'has_enough_years': len(eps_data) >= min_years_recommended,
            'annual_dividend': round(annual_dividend, 2),
            'dividend_payments': dividend_info,
            'estimated_value': round(estimated_value, 2) if estimated_value else None,
            'price_vs_value': round(price_vs_value, 1) if price_vs_value else None,
            'formula': f'(({round(eps_avg, 2) if eps_avg else "N/A"} avg EPS) + {round(annual_dividend, 2)} dividend) × 10 = ${round(estimated_value, 2) if estimated_value else "N/A"}',
            'selloff': selloff_metrics
        })

    except Exception as e:
        error_msg = str(e).lower()

        # Check for rate limiting - try to use cached data as fallback
        if 'rate' in error_msg or 'too many' in error_msg or '429' in error_msg:
            # Try to get cached valuation data
            all_valuations = data_manager.load_valuations().get('valuations', {})
            cached = all_valuations.get(ticker)

            if cached:
                # Try to get SEC EPS history (doesn't require Yahoo Finance API)
                eps_data = []
                sec_eps = sec_data.get_sec_eps(ticker)
                if sec_eps and sec_eps.get('eps_history'):
                    for e in sec_eps['eps_history'][:8]:
                        # Format fiscal period from start/end dates
                        fiscal_period = ''
                        start_date = e.get('period_start') or e.get('start')
                        end_date = e.get('period_end') or e.get('end')
                        if start_date and end_date:
                            try:
                                start = datetime.fromisoformat(start_date)
                                end = datetime.fromisoformat(end_date)
                                fiscal_period = f"{start.strftime('%b %Y')} - {end.strftime('%b %Y')}"
                            except:
                                pass
                        eps_data.append({
                            'year': e['year'],
                            'eps': e['eps'],
                            'type': e.get('eps_type', 'Diluted EPS'),
                            'fiscal_period': fiscal_period
                        })

                # Return cached data with a note that it's from cache
                return jsonify({
                    'ticker': ticker,
                    'company_name': cached.get('company_name', ticker),
                    'current_price': cached.get('current_price'),
                    'eps_data': eps_data,
                    'eps_years': cached.get('eps_years', 0),
                    'eps_source': cached.get('eps_source', 'cached'),
                    'eps_avg': cached.get('eps_avg'),
                    'min_years_recommended': 8,
                    'has_enough_years': cached.get('eps_years', 0) >= 8,
                    'annual_dividend': cached.get('annual_dividend', 0),
                    'dividend_payments': [],
                    'estimated_value': cached.get('estimated_value'),
                    'price_vs_value': cached.get('price_vs_value'),
                    'formula': f'Cached data from {cached.get("updated", "unknown")}',
                    'selloff': None,
                    'from_cache': True,
                    'cache_note': f'Rate limited - showing cached data from {cached.get("updated", "earlier")}'
                })
            else:
                return jsonify({
                    'error': 'Too Many Requests. Rate limited. Try after a while.',
                    'ticker': ticker
                }), 429

        return jsonify({
            'error': str(e),
            'ticker': ticker
        }), 400


@app.route('/api/sec-metrics/<ticker>')
def api_sec_metrics(ticker):
    """Get detailed SEC metrics for a ticker (all EPS types, etc.)"""
    ticker = ticker.upper()

    try:
        metrics = sec_data.get_sec_metrics(ticker)
        if not metrics:
            return jsonify({
                'error': 'No SEC data available',
                'ticker': ticker
            }), 404

        return jsonify(metrics)
    except Exception as e:
        return jsonify({
            'error': str(e),
            'ticker': ticker
        }), 500


@app.route('/api/valuation/<ticker>/refresh', methods=['POST'])
def api_valuation_refresh(ticker):
    """
    Refresh valuation data for a ticker.
    - Fetches SEC data only if not already cached (or force=true clears cache first)
    - Always fetches fresh yfinance data (different fiscal calendars)
    """
    ticker = ticker.upper()
    force = request.args.get('force', 'false').lower() == 'true'

    try:
        # Step 1: Fetch SEC data
        sec_fetched = False
        sec_had_data = False
        new_eps_years = 0
        if force:
            # Force refresh: check SEC for any new EPS years
            _, new_eps_years = sec_data.force_refresh_sec_eps(ticker)
            sec_had_data = sec_data.has_cached_eps(ticker)
        else:
            # Normal: only fetch if not cached
            sec_had_data = sec_data.has_cached_eps(ticker)
            if not sec_had_data:
                _, sec_fetched = sec_data.fetch_sec_eps_if_missing(ticker)

        # Step 2: Fetch fresh yfinance data
        stock = yf.Ticker(ticker)

        # Get company info
        info = stock.info
        company_name = info.get('shortName', ticker)
        current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)

        # Fetch income_stmt once for reuse in validation
        income_stmt = stock.income_stmt

        # Get validated EPS data (cross-checks SEC against yfinance)
        eps_data, eps_source, validation_info = get_validated_eps(ticker, stock, income_stmt)

        # Use SEC company name if available and SEC data was used
        if eps_source.startswith('sec'):
            sec_eps = sec_data.get_sec_eps(ticker)
            if sec_eps and sec_eps.get('company_name'):
                company_name = sec_eps['company_name']

        # Get selloff metrics
        selloff_metrics = calculate_selloff_metrics(stock)

        # Get dividend info
        dividends = stock.dividends
        annual_dividend = 0
        dividend_info = []

        if dividends is not None and len(dividends) > 0:
            from datetime import timedelta
            one_year_ago = datetime.now() - timedelta(days=365)
            recent_dividends = dividends[dividends.index >= one_year_ago.strftime('%Y-%m-%d')]

            for date, amount in recent_dividends.items():
                dividend_info.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'amount': float(amount)
                })
                annual_dividend += float(amount)

        # Calculate valuation
        min_years_recommended = 8
        eps_avg = None
        estimated_value = None
        price_vs_value = None

        if len(eps_data) > 0:
            eps_avg = sum(e['eps'] for e in eps_data) / len(eps_data)
            estimated_value = (eps_avg + annual_dividend) * 10

            if current_price and current_price > 0 and estimated_value > 0:
                price_vs_value = ((current_price - estimated_value) / estimated_value) * 100

        return jsonify({
            'ticker': ticker,
            'company_name': company_name,
            'current_price': round(current_price, 2) if current_price else None,
            'eps_data': eps_data,
            'eps_years': len(eps_data),
            'eps_source': eps_source,
            'eps_validation': validation_info,
            'eps_avg': round(eps_avg, 2) if eps_avg else None,
            'min_years_recommended': min_years_recommended,
            'has_enough_years': len(eps_data) >= min_years_recommended,
            'annual_dividend': round(annual_dividend, 2),
            'dividend_payments': dividend_info,
            'estimated_value': round(estimated_value, 2) if estimated_value else None,
            'price_vs_value': round(price_vs_value, 1) if price_vs_value else None,
            'formula': f'(({round(eps_avg, 2) if eps_avg else "N/A"} avg EPS) + {round(annual_dividend, 2)} dividend) × 10 = ${round(estimated_value, 2) if estimated_value else "N/A"}',
            'selloff': selloff_metrics,
            'refresh_info': {
                'force_refresh': force,
                'sec_had_cached': sec_had_data,
                'sec_fetched': sec_fetched,
                'new_eps_years': new_eps_years,
                'yfinance_refreshed': True
            }
        })

    except Exception as e:
        return jsonify({
            'error': str(e),
            'ticker': ticker
        }), 400


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

@app.route('/api/stocks', methods=['POST'])
def add_stock():
    data = request.json
    stocks = get_stocks()

    # Check if already exists
    if any(s['ticker'] == data['ticker'] for s in stocks):
        return jsonify({'success': False, 'error': 'Stock already exists'})

    db.add_stock(
        ticker=data.get('ticker', ''),
        name=data.get('name', ''),
        stock_type=data.get('type', 'stock')
    )
    return jsonify({'success': True})

def parse_date(date_str):
    """Parse date string to date object"""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        return None

@app.route('/api/profit-timeline')
def api_profit_timeline():
    """Calculate realized profit within a date range"""
    start_date = request.args.get('start')
    end_date = request.args.get('end')

    start = parse_date(start_date)
    end = parse_date(end_date)

    stocks = {s['ticker']: s for s in get_stocks()}
    transactions = get_transactions()

    # First pass: calculate average buy price for each ticker (using ALL buys)
    avg_prices = {}
    for txn in transactions:
        ticker = txn['ticker']
        if ticker not in avg_prices:
            avg_prices[ticker] = {'total_shares': 0, 'total_cost': 0}

        if txn['action'] == 'buy':
            shares = int(txn['shares']) if txn['shares'] else 0
            price = float(txn['price']) if txn['price'] else 0
            avg_prices[ticker]['total_shares'] += shares
            avg_prices[ticker]['total_cost'] += shares * price

    for ticker in avg_prices:
        if avg_prices[ticker]['total_shares'] > 0:
            avg_prices[ticker]['avg'] = avg_prices[ticker]['total_cost'] / avg_prices[ticker]['total_shares']
        else:
            avg_prices[ticker]['avg'] = 0

    # Second pass: calculate profit from sales in date range
    sales_in_range = []
    total_profit = 0
    total_revenue = 0
    by_ticker = {}
    by_month = {}

    for txn in transactions:
        if txn['action'] != 'sell':
            continue
        status = (txn['status'] or '').lower()
        if status != 'done':
            continue

        txn_date = parse_date(txn['date'])
        if not txn_date:
            continue

        # Check date range
        if start and txn_date < start:
            continue
        if end and txn_date > end:
            continue

        ticker = txn['ticker']
        shares = int(txn['shares']) if txn['shares'] else 0
        price = float(txn['price']) if txn['price'] else 0
        revenue = shares * price
        cost = shares * avg_prices.get(ticker, {}).get('avg', 0)
        profit = revenue - cost

        total_profit += profit
        total_revenue += revenue

        # By ticker
        if ticker not in by_ticker:
            by_ticker[ticker] = {
                'ticker': ticker,
                'name': stocks.get(ticker, {}).get('name', ticker),
                'shares_sold': 0,
                'revenue': 0,
                'profit': 0,
                'sales': []
            }
        by_ticker[ticker]['shares_sold'] += shares
        by_ticker[ticker]['revenue'] += revenue
        by_ticker[ticker]['profit'] += profit
        by_ticker[ticker]['sales'].append({
            'date': txn['date'],
            'shares': shares,
            'price': price,
            'revenue': revenue,
            'profit': round(profit, 2)
        })

        # By month
        month_key = txn_date.strftime('%Y-%m')
        if month_key not in by_month:
            by_month[month_key] = {'month': month_key, 'profit': 0, 'revenue': 0, 'sales_count': 0}
        by_month[month_key]['profit'] += profit
        by_month[month_key]['revenue'] += revenue
        by_month[month_key]['sales_count'] += 1

        sales_in_range.append({
            'date': txn['date'],
            'ticker': ticker,
            'shares': shares,
            'price': price,
            'profit': round(profit, 2)
        })

    # Sort and format
    sales_in_range.sort(key=lambda x: x['date'])
    by_ticker_list = sorted(by_ticker.values(), key=lambda x: x['profit'], reverse=True)
    for t in by_ticker_list:
        t['revenue'] = round(t['revenue'], 2)
        t['profit'] = round(t['profit'], 2)

    by_month_list = sorted(by_month.values(), key=lambda x: x['month'])
    for m in by_month_list:
        m['profit'] = round(m['profit'], 2)
        m['revenue'] = round(m['revenue'], 2)

    return jsonify({
        'date_range': {
            'start': start_date or 'all time',
            'end': end_date or 'now'
        },
        'totals': {
            'profit': round(total_profit, 2),
            'revenue': round(total_revenue, 2),
            'sales_count': len(sales_in_range)
        },
        'by_ticker': by_ticker_list,
        'by_month': by_month_list,
        'sales': sales_in_range
    })

@app.route('/api/performance')
def api_performance():
    """Calculate portfolio performance over various time periods"""
    from datetime import date, timedelta
    from dateutil.relativedelta import relativedelta

    stocks = {s['ticker']: s for s in get_stocks()}
    transactions = get_transactions()
    today = date.today()

    # Calculate average buy prices
    avg_prices = {}
    for txn in transactions:
        ticker = txn['ticker']
        if ticker not in avg_prices:
            avg_prices[ticker] = {'total_shares': 0, 'total_cost': 0}
        if txn['action'] == 'buy':
            shares = int(txn['shares']) if txn['shares'] else 0
            price = float(txn['price']) if txn['price'] else 0
            avg_prices[ticker]['total_shares'] += shares
            avg_prices[ticker]['total_cost'] += shares * price

    for ticker in avg_prices:
        if avg_prices[ticker]['total_shares'] > 0:
            avg_prices[ticker]['avg'] = avg_prices[ticker]['total_cost'] / avg_prices[ticker]['total_shares']
        else:
            avg_prices[ticker]['avg'] = 0

    def calc_profit_for_period(start_date):
        """Calculate realized profit from start_date to now"""
        total_profit = 0
        total_revenue = 0
        sales_count = 0

        for txn in transactions:
            if txn['action'] != 'sell':
                continue
            status = (txn['status'] or '').lower()
            if status != 'done':
                continue

            txn_date = parse_date(txn['date'])
            if not txn_date or txn_date < start_date:
                continue

            ticker = txn['ticker']
            shares = int(txn['shares']) if txn['shares'] else 0
            price = float(txn['price']) if txn['price'] else 0
            revenue = shares * price
            cost = shares * avg_prices.get(ticker, {}).get('avg', 0)
            profit = revenue - cost

            total_profit += profit
            total_revenue += revenue
            sales_count += 1

        return {
            'profit': round(total_profit, 2),
            'revenue': round(total_revenue, 2),
            'sales_count': sales_count
        }

    # Calculate for different periods
    periods = {
        'ytd': date(today.year, 1, 1),
        '1y': today - relativedelta(years=1),
        '2y': today - relativedelta(years=2),
        '3y': today - relativedelta(years=3),
        '5y': today - relativedelta(years=5),
        '10y': today - relativedelta(years=10),
        'all': date(2000, 1, 1)
    }

    results = {}
    for period_name, start_date in periods.items():
        results[period_name] = calc_profit_for_period(start_date)
        results[period_name]['label'] = {
            'ytd': 'Year to Date',
            '1y': '1 Year',
            '2y': '2 Years',
            '3y': '3 Years',
            '5y': '5 Years',
            '10y': '10 Years',
            'all': 'All Time'
        }[period_name]

    return jsonify(results)

# SEC Data API endpoints
@app.route('/api/sec/status')
def api_sec_status():
    """Get SEC data cache status"""
    status = sec_data.get_cache_status()
    progress = sec_data.get_update_progress()
    return jsonify({
        'cache': status,
        'update': progress
    })


@app.route('/api/data-status')
def api_data_status():
    """Get comprehensive data status for all datasets"""
    # Get consolidated stats from data manager
    dm_stats = data_manager.get_data_stats()

    # SEC data status (from sec_data module for CIK info)
    sec_status = sec_data.get_cache_status()

    # Index data status - use consolidated data
    indices = []
    for index_name in VALID_INDICES:
        try:
            # Get tickers for this index from status
            index_tickers = data_manager.get_index_tickers(index_name)
            total_tickers = len(index_tickers) if index_tickers else 0

            # If no tickers in status, fall back to old index file
            if total_tickers == 0:
                data = get_index_data(index_name)
                total_tickers = len(data.get('tickers', []))
                index_tickers = data.get('tickers', [])

            # Get valuations from consolidated storage
            valuations = data_manager.get_valuations_for_index(index_name, index_tickers)
            valuations_count = len(valuations)

            # Count by EPS source
            sec_source_count = sum(1 for v in valuations if v.get('eps_source') == 'sec')
            yf_source_count = sum(1 for v in valuations if v.get('eps_source') == 'yfinance')

            # Average EPS years
            eps_years = [v.get('eps_years', 0) for v in valuations if v.get('eps_years')]
            avg_eps_years = sum(eps_years) / len(eps_years) if eps_years else 0

            # Get last updated from consolidated data
            last_updated = None
            if valuations:
                updates = [v.get('updated') for v in valuations if v.get('updated')]
                if updates:
                    last_updated = max(updates)

            indices.append({
                'id': index_name,
                'name': INDEX_NAMES.get(index_name, (index_name, index_name))[0],
                'short_name': INDEX_NAMES.get(index_name, (index_name, index_name))[1],
                'total_tickers': total_tickers,
                'valuations_count': valuations_count,
                'coverage_pct': round((valuations_count / total_tickers * 100) if total_tickers > 0 else 0, 1),
                'sec_source_count': sec_source_count,
                'yf_source_count': yf_source_count,
                'avg_eps_years': round(avg_eps_years, 1),
                'last_updated': last_updated
            })
        except Exception as e:
            print(f"[DataStatus] Error loading {index_name}: {e}")

    # Current refresh status
    refresh_status = {
        'running': screener_running,
        'progress': screener_progress
    }

    # Load refresh summary from database
    refresh_summary = None
    try:
        summary_str = db.get_metadata('refresh_summary')
        if summary_str:
            refresh_summary = json.loads(summary_str)
    except Exception:
        pass

    # Get excluded tickers info
    excluded_info = get_excluded_tickers_info()

    return jsonify({
        'sec': {
            'companies_cached': dm_stats['sec_available'],
            'sec_unavailable': dm_stats['sec_unavailable'],
            'sec_unknown': dm_stats['sec_unknown'],
            'cik_mappings': sec_status.get('cik_mapping', {}).get('count', 0),
            'cik_updated': sec_status.get('cik_mapping', {}).get('updated'),
            'last_full_update': dm_stats.get('status_last_updated')
        },
        'indices': indices,
        'consolidated': {
            'total_tickers': dm_stats['total_tickers'],
            'with_valuation': dm_stats['with_valuation'],
            'status_updated': dm_stats['status_last_updated'],
            'valuations_updated': dm_stats['valuations_last_updated']
        },
        'refresh': refresh_status,
        'refresh_summary': refresh_summary,
        'excluded_tickers': excluded_info
    })

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

@app.route('/api/excluded-tickers', methods=['GET'])
def api_get_excluded_tickers():
    """Get list of excluded (delisted/unavailable) tickers"""
    return jsonify(get_excluded_tickers_info())

@app.route('/api/excluded-tickers/clear', methods=['POST'])
def api_clear_excluded_tickers():
    """Clear the excluded tickers list to re-check them on next refresh"""
    clear_excluded_tickers()
    return jsonify({'status': 'cleared', 'message': 'Excluded tickers list cleared. Next refresh will re-check all tickers.'})

@app.route('/api/sec/update', methods=['POST'])
def api_sec_update():
    """Start SEC data update for S&P 500 tickers"""
    sp500_data = get_sp500_data()
    tickers = sp500_data.get('tickers', [])

    if sec_data.start_background_update(tickers):
        return jsonify({'status': 'started', 'tickers': len(tickers)})
    else:
        return jsonify({'error': 'Update already running'}), 400

@app.route('/api/sec/stop', methods=['POST'])
def api_sec_stop():
    """Stop SEC data update"""
    sec_data.stop_update()
    return jsonify({'status': 'stopping'})

@app.route('/api/sec/progress')
def api_sec_progress():
    """Get SEC update progress"""
    return jsonify(sec_data.get_update_progress())

@app.route('/api/sec/eps/<ticker>')
def api_sec_eps(ticker):
    """Get SEC EPS data for a specific ticker"""
    data = sec_data.get_sec_eps(ticker.upper())
    if data:
        return jsonify(data)
    return jsonify({'error': 'No data available', 'ticker': ticker}), 404


@app.route('/api/sec-filings/<ticker>')
def api_sec_filings(ticker):
    """Get 10-K filing URLs for a specific ticker"""
    filings = sec_data.get_10k_filings(ticker.upper())
    return jsonify(filings)


@app.route('/api/eps-recommendations')
def api_eps_recommendations():
    """Get recommendations for which tickers need EPS data updates"""
    # Use consolidated data manager for status info
    dm_stats = data_manager.get_data_stats()
    status_data = data_manager.load_ticker_status()

    # Get SEC-specific recommendations (fiscal year based)
    recommendations = sec_data.get_eps_update_recommendations()

    # Get summary stats for top recommendations
    top_updates = []
    for ticker in recommendations['needs_update'][:20]:  # Top 20
        info = recommendations['details'].get(ticker, {})
        top_updates.append({
            'ticker': ticker,
            'company_name': info.get('company_name', ticker),
            'latest_fy': info.get('latest_fy'),
            'fiscal_year_end': info.get('fiscal_year_end_parsed'),
            'next_fy_end': info.get('next_fy_end'),
            'expected_filing': info.get('expected_filing'),
            'priority': info.get('priority', 'normal'),
            'reason': info.get('reason'),
            'days_since_fy_end': info.get('days_since_fy_end')
        })

    # Use data_manager for status categorization
    tickers_by_status = {
        'available': set(),
        'unavailable': set(),
        'unknown': set()
    }

    for ticker, info in status_data.get('tickers', {}).items():
        sec_status = info.get('sec_status', 'unknown')
        if sec_status in tickers_by_status:
            tickers_by_status[sec_status].add(ticker)

    # Build per-index breakdowns
    missing_by_index = {}
    unavailable_by_index = {}
    total_missing = 0
    total_unavailable = 0

    for index_name in VALID_INDICES:
        # Get index tickers from data_manager or fall back to old file
        index_tickers = set(data_manager.get_index_tickers(index_name))
        if not index_tickers:
            data = get_index_data(index_name)
            index_tickers = set(data.get('tickers', []))

        short_name = INDEX_NAMES.get(index_name, (index_name, index_name))[1]

        # Missing = SEC status unknown (not yet fetched)
        missing = index_tickers & tickers_by_status['unknown']
        if missing:
            missing_by_index[index_name] = {
                'short_name': short_name,
                'missing_count': len(missing),
                'total_count': len(index_tickers),
                'missing_tickers': sorted(list(missing))[:50]
            }
            total_missing += len(missing)

        # Unavailable = SEC has no EPS data (will use yfinance)
        unavailable = index_tickers & tickers_by_status['unavailable']
        if unavailable:
            unavailable_by_index[index_name] = {
                'short_name': short_name,
                'unavailable_count': len(unavailable),
                'total_count': len(index_tickers),
                'unavailable_tickers': sorted(list(unavailable))[:50]
            }
            total_unavailable += len(unavailable)

    return jsonify({
        'needs_update_count': recommendations['needs_update_count'],
        'recently_updated_count': recommendations['recently_updated_count'],
        'total_cached': recommendations['total_cached'],
        'top_updates': top_updates,
        'all_needs_update': recommendations['needs_update'],
        'missing_by_index': missing_by_index,
        'total_missing': total_missing,
        'unavailable_by_index': unavailable_by_index,
        'total_unavailable': total_unavailable,
        'generated': recommendations['generated']
    })

@app.route('/api/sec/compare/<ticker>')
def api_sec_compare(ticker):
    """Compare SEC EDGAR EPS data with yfinance data for validation"""
    ticker = ticker.upper()

    comparison = {
        'ticker': ticker,
        'sec': {'available': False, 'eps_data': []},
        'yfinance': {'available': False, 'eps_data': []},
        'comparison': [],
        'summary': {}
    }

    # Get SEC EDGAR data
    sec_eps = sec_data.get_sec_eps(ticker)
    if sec_eps and sec_eps.get('eps_history'):
        comparison['sec']['available'] = True
        comparison['sec']['eps_data'] = sec_eps['eps_history']
        comparison['sec']['company_name'] = sec_eps.get('company_name', ticker)

    # Get yfinance data
    try:
        stock = yf.Ticker(ticker)
        income_stmt = stock.income_stmt

        if income_stmt is not None and not income_stmt.empty:
            yf_eps = []
            eps_row = None
            eps_type = None

            # Try diluted first (matches SEC preference), then basic
            if 'Diluted EPS' in income_stmt.index:
                eps_row = income_stmt.loc['Diluted EPS']
                eps_type = 'Diluted'
            elif 'Basic EPS' in income_stmt.index:
                eps_row = income_stmt.loc['Basic EPS']
                eps_type = 'Basic'

            if eps_row is not None:
                for date, eps in eps_row.items():
                    if eps is not None and not (isinstance(eps, float) and math.isnan(eps)):
                        year = date.year if hasattr(date, 'year') else int(str(date)[:4])
                        yf_eps.append({
                            'year': int(year),
                            'eps': round(float(eps), 2)
                        })

                yf_eps.sort(key=lambda x: x['year'], reverse=True)
                comparison['yfinance']['available'] = True
                comparison['yfinance']['eps_data'] = yf_eps
                comparison['yfinance']['eps_type'] = eps_type
    except Exception as e:
        comparison['yfinance']['error'] = str(e)

    # Build year-by-year comparison
    sec_by_year = {e['year']: e['eps'] for e in comparison['sec']['eps_data']}
    yf_by_year = {e['year']: e['eps'] for e in comparison['yfinance']['eps_data']}

    all_years = sorted(set(list(sec_by_year.keys()) + list(yf_by_year.keys())), reverse=True)

    total_diff = 0
    matched_years = 0

    for year in all_years[:8]:  # Compare up to 8 years
        sec_val = sec_by_year.get(year)
        yf_val = yf_by_year.get(year)

        row = {
            'year': year,
            'sec_eps': round(sec_val, 2) if sec_val is not None else None,
            'yf_eps': round(yf_val, 2) if yf_val is not None else None,
            'diff': None,
            'diff_pct': None,
            'match': None
        }

        if sec_val is not None and yf_val is not None:
            diff = sec_val - yf_val
            row['diff'] = round(diff, 2)

            # Calculate percentage difference
            if yf_val != 0:
                row['diff_pct'] = round((diff / abs(yf_val)) * 100, 1)

            # Consider a match if within 1% or $0.02 (accounting for rounding)
            row['match'] = abs(diff) < 0.02 or (yf_val != 0 and abs(diff / yf_val) < 0.01)

            if row['match']:
                matched_years += 1
            total_diff += abs(diff)

        comparison['comparison'].append(row)

    # Summary statistics
    years_with_both = len([r for r in comparison['comparison'] if r['sec_eps'] is not None and r['yf_eps'] is not None])
    comparison['summary'] = {
        'years_compared': years_with_both,
        'years_matched': matched_years,
        'match_rate': round(matched_years / years_with_both * 100, 1) if years_with_both > 0 else 0,
        'avg_absolute_diff': round(total_diff / years_with_both, 2) if years_with_both > 0 else None,
        'data_quality': 'good' if years_with_both >= 4 and matched_years >= years_with_both * 0.75 else 'review'
    }

    return jsonify(comparison)

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

if __name__ == '__main__':
    app.run(debug=True, port=8080)

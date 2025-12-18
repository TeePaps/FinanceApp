"""
Yahoo Finance API wrapper with caching, rate limiting, and fallback sources.

Provides centralized access to stock price data with:
- Price caching to reduce API calls
- Batch downloading for efficiency
- Fallback to alternate data sources when yfinance fails
- Selloff metrics calculation
- EPS extraction from income statements

Fallback sources (in order):
1. yfinance batch download
2. yfinance individual ticker (fast_info)
3. yfinance history
4. Financial Modeling Prep API (if API key configured)
"""

import os
import time
import math
import requests
import yfinance as yf
from datetime import datetime, timedelta
from config import (
    PRICE_CACHE_DURATION,
    YAHOO_BATCH_SIZE,
    YAHOO_BATCH_DELAY,
    SELLOFF_VOLUME_SEVERE,
    SELLOFF_VOLUME_HIGH,
    SELLOFF_VOLUME_MODERATE
)

# Optional API keys for fallback sources (set via environment variables)
FMP_API_KEY = os.environ.get('FMP_API_KEY')  # Financial Modeling Prep

# Module-level price cache: {ticker: {'price': float, 'timestamp': float}}
_price_cache = {}

# Track persistent failures to avoid repeated API calls
_persistent_failures = set()


def _fetch_price_from_fmp(ticker):
    """
    Fetch price from Financial Modeling Prep API (fallback source).

    Free tier: 250 requests/day - use sparingly for individual failures.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Price as float, or None if not available
    """
    if not FMP_API_KEY:
        return None

    try:
        url = f"https://financialmodelingprep.com/api/v3/quote-short/{ticker}?apikey={FMP_API_KEY}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0 and 'price' in data[0]:
                return float(data[0]['price'])
    except Exception as e:
        print(f"[FMP] Error fetching {ticker}: {e}")

    return None


def _fetch_price_with_retries(ticker, max_retries=3):
    """
    Fetch price for a single ticker with multiple fallback methods.

    Tries in order:
    1. yfinance fast_info
    2. yfinance history
    3. Financial Modeling Prep (if API key configured)

    Args:
        ticker: Stock ticker symbol
        max_retries: Number of retry attempts per method

    Returns:
        Tuple of (price, source) or (None, None) if all methods fail
    """
    # Method 1: yfinance fast_info (fastest)
    for attempt in range(max_retries):
        try:
            stock = yf.Ticker(ticker)
            price = stock.fast_info.get('lastPrice') or stock.fast_info.get('regularMarketPrice')
            if price and price > 0:
                return float(price), 'yfinance_fast'
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))  # Exponential backoff

    # Method 2: yfinance history (more reliable but slower)
    for attempt in range(max_retries):
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period='5d')
            if hist is not None and not hist.empty and 'Close' in hist.columns:
                price = hist['Close'].iloc[-1]
                if price and price > 0:
                    return float(price), 'yfinance_history'
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))

    # Method 3: Financial Modeling Prep (if configured)
    if FMP_API_KEY:
        price = _fetch_price_from_fmp(ticker)
        if price:
            return price, 'fmp'

    return None, None


def fetch_stock_price(ticker):
    """
    Fetch current stock price with caching and fallback sources.

    Tries multiple data sources if primary fails:
    1. yfinance fast_info (fastest)
    2. yfinance history (more reliable)
    3. Financial Modeling Prep (if API key configured)

    Args:
        ticker: Stock ticker symbol

    Returns:
        Current price as float, or None if not available
    """
    global _price_cache

    # Check cache first
    if ticker in _price_cache:
        cached = _price_cache[ticker]
        if time.time() - cached['timestamp'] < PRICE_CACHE_DURATION:
            return cached['price']

    # Skip known persistent failures (delisted stocks, etc.)
    if ticker in _persistent_failures:
        return None

    # Use enhanced retry logic with fallbacks
    price, source = _fetch_price_with_retries(ticker, max_retries=2)

    if price:
        _price_cache[ticker] = {
            'price': price,
            'timestamp': time.time(),
            'source': source
        }
        return price

    return None


def mark_ticker_failed(ticker):
    """Mark a ticker as persistently failed (delisted, invalid, etc.)."""
    _persistent_failures.add(ticker)


def clear_failed_tickers():
    """Clear the persistent failures set (for retry after fixes)."""
    _persistent_failures.clear()


def get_failed_tickers():
    """Get list of persistently failed tickers."""
    return list(_persistent_failures)


def fetch_multiple_prices(tickers):
    """
    Fetch prices for multiple tickers with fallback sources.

    Uses batch download for efficiency, with individual fallbacks for failures:
    1. yfinance batch download (primary)
    2. yfinance individual ticker (fast_info, then history)
    3. Financial Modeling Prep API (if FMP_API_KEY env var is set)

    Args:
        tickers: List of ticker symbols

    Returns:
        Dict mapping ticker to price
    """
    global _price_cache
    now = time.time()

    # Check which tickers need fetching
    to_fetch = []
    cached_prices = {}
    for ticker in tickers:
        if ticker in _price_cache and now - _price_cache[ticker]['timestamp'] < PRICE_CACHE_DURATION:
            cached_prices[ticker] = _price_cache[ticker]['price']
        else:
            to_fetch.append(ticker)

    # Fetch uncached tickers in batch with retry logic
    failed_tickers = []

    if to_fetch:
        # Try batch download first with retries
        max_retries = 2
        for attempt in range(max_retries):
            try:
                data = yf.download(to_fetch, period='1d', progress=False, threads=True)

                if not data.empty:
                    if len(to_fetch) == 1:
                        ticker = to_fetch[0]
                        if 'Close' in data.columns:
                            price = data['Close'].iloc[-1]
                            if price and not (hasattr(price, 'isna') and price.isna()):
                                _price_cache[ticker] = {'price': float(price), 'timestamp': now}
                                cached_prices[ticker] = float(price)
                            else:
                                failed_tickers.append(ticker)
                        else:
                            failed_tickers.append(ticker)
                    else:
                        for ticker in to_fetch:
                            try:
                                if ('Close', ticker) in data.columns:
                                    price = data[('Close', ticker)].iloc[-1]
                                    if price and not (hasattr(price, 'isna') and price.isna()):
                                        _price_cache[ticker] = {'price': float(price), 'timestamp': now}
                                        cached_prices[ticker] = float(price)
                                    else:
                                        failed_tickers.append(ticker)
                                else:
                                    failed_tickers.append(ticker)
                            except Exception:
                                failed_tickers.append(ticker)
                    break  # Success, exit retry loop
                else:
                    if attempt < max_retries - 1:
                        time.sleep(1)  # Wait before retry
                    else:
                        failed_tickers = to_fetch.copy()
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    failed_tickers = to_fetch.copy()

        # Fallback: try individual ticker fetching for failed tickers
        still_failed = []
        if failed_tickers:
            for ticker in failed_tickers:
                # Skip known persistent failures
                if ticker in _persistent_failures:
                    continue

                try:
                    time.sleep(0.2)
                    stock = yf.Ticker(ticker)
                    try:
                        price = stock.fast_info.get('lastPrice') or stock.fast_info.get('regularMarketPrice')
                    except Exception:
                        price = None

                    if not price:
                        hist = stock.history(period='5d')  # Try 5 days for more reliability
                        if not hist.empty and 'Close' in hist.columns:
                            price = hist['Close'].iloc[-1]

                    if price and price > 0:
                        _price_cache[ticker] = {'price': float(price), 'timestamp': now, 'source': 'yfinance'}
                        cached_prices[ticker] = float(price)
                    else:
                        still_failed.append(ticker)
                except Exception:
                    still_failed.append(ticker)

        # Final fallback: try Financial Modeling Prep for remaining failures
        if still_failed and FMP_API_KEY:
            fmp_recovered = 0
            for ticker in still_failed[:50]:  # Limit to 50 to preserve daily quota
                price = _fetch_price_from_fmp(ticker)
                if price:
                    _price_cache[ticker] = {'price': price, 'timestamp': now, 'source': 'fmp'}
                    cached_prices[ticker] = price
                    fmp_recovered += 1
                time.sleep(0.1)  # Gentle rate limiting
            if fmp_recovered > 0:
                print(f"[FMP Fallback] Recovered {fmp_recovered}/{len(still_failed[:50])} tickers")

    return cached_prices


def get_stock_info(ticker):
    """
    Get comprehensive stock info: price, dividends, 52w range, etc.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dict with stock info or None if not available
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        return {
            'ticker': ticker,
            'company_name': info.get('shortName', ticker),
            'current_price': info.get('currentPrice') or info.get('regularMarketPrice'),
            'fifty_two_week_high': info.get('fiftyTwoWeekHigh'),
            'fifty_two_week_low': info.get('fiftyTwoWeekLow'),
            'average_volume': info.get('averageVolume'),
            'market_cap': info.get('marketCap'),
            'pe_ratio': info.get('trailingPE'),
            'forward_pe': info.get('forwardPE'),
            'dividend_yield': info.get('dividendYield'),
            'sector': info.get('sector'),
            'industry': info.get('industry')
        }
    except Exception as e:
        print(f"Error fetching stock info for {ticker}: {e}")
        return None


def extract_yf_eps(stock, income_stmt=None):
    """
    Extract EPS data from yfinance income statement.

    Args:
        stock: yfinance Ticker object
        income_stmt: Optional pre-fetched income statement

    Returns:
        List of dicts with year and eps values, sorted by year descending
    """
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

    Args:
        stock: yfinance Ticker object

    Returns:
        Dict with selloff metrics or None if calculation fails
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


def get_annual_dividend(ticker):
    """
    Get the annual dividend amount for a ticker.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Tuple of (annual_dividend, dividend_info list)
    """
    try:
        stock = yf.Ticker(ticker)
        dividends = stock.dividends
        annual_dividend = 0
        dividend_info = []

        if dividends is not None and len(dividends) > 0:
            one_year_ago = datetime.now() - timedelta(days=365)
            recent_dividends = dividends[dividends.index >= one_year_ago.strftime('%Y-%m-%d')]

            for date, amount in recent_dividends.items():
                dividend_info.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'amount': float(amount)
                })
                annual_dividend += float(amount)

        return annual_dividend, dividend_info
    except Exception as e:
        print(f"Error fetching dividend for {ticker}: {e}")
        return 0, []


def clear_price_cache():
    """Clear the price cache."""
    global _price_cache
    _price_cache = {}


def get_cache_stats():
    """Get statistics about the price cache."""
    now = time.time()
    valid_entries = sum(1 for v in _price_cache.values()
                       if now - v['timestamp'] < PRICE_CACHE_DURATION)
    return {
        'total_entries': len(_price_cache),
        'valid_entries': valid_entries,
        'expired_entries': len(_price_cache) - valid_entries
    }

"""
Stock utilities and yfinance helpers.

Provides:
- Stock info fetching (PE ratio, market cap, sector, etc.)
- Selloff metrics calculation (volume-based analysis)
- EPS extraction from income statements
- Dividend fetching
- Price fetching (delegates to provider orchestrator)

Price fetching uses the pluggable provider system (see services/providers/).
Other utilities use yfinance directly for data not available through providers.
"""

import time
import math
from datetime import datetime, timedelta
from config import PRICE_CACHE_DURATION

# Import provider system (for orchestrator usage)
try:
    from services.providers import get_orchestrator
    _HAS_PROVIDERS = True
except ImportError:
    _HAS_PROVIDERS = False

# Module-level price cache: {ticker: {'price': float, 'timestamp': float}}
_price_cache = {}

# Track persistent failures to avoid repeated API calls
_persistent_failures = set()

# Whether to use the provider orchestrator for price fetching
USE_ORCHESTRATOR = True  # Set to False to use legacy behavior


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
    # Use the provider orchestrator which handles fallbacks and logging
    try:
        from services.providers import get_orchestrator
        orchestrator = get_orchestrator()
        result = orchestrator.fetch_price(ticker, skip_cache=True)
        if result.success and result.data and result.data > 0:
            return float(result.data), result.source
    except Exception:
        pass

    return None, None


def fetch_stock_price(ticker):
    """
    Fetch current stock price with caching and fallback sources.

    If USE_ORCHESTRATOR is True, uses the provider system for data fetching.
    Otherwise uses legacy behavior:
    1. yfinance fast_info (fastest)
    2. yfinance history (more reliable)
    3. Financial Modeling Prep (if API key configured)

    Args:
        ticker: Stock ticker symbol

    Returns:
        Current price as float, or None if not available
    """
    global _price_cache

    # Use provider orchestrator if available
    if USE_ORCHESTRATOR and _HAS_PROVIDERS:
        try:
            orchestrator = get_orchestrator()
            result = orchestrator.fetch_price(ticker)
            if result.success:
                return result.data
            return None
        except Exception:
            pass  # Fall through to legacy behavior

    # Legacy behavior below

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

    If USE_ORCHESTRATOR is True, uses the provider system for data fetching.
    Otherwise uses legacy behavior:
    1. yfinance batch download (primary)
    2. yfinance individual ticker (fast_info, then history)
    3. Financial Modeling Prep API (if configured)

    Args:
        tickers: List of ticker symbols

    Returns:
        Dict mapping ticker to price
    """
    # Use provider orchestrator if available
    if USE_ORCHESTRATOR and _HAS_PROVIDERS:
        try:
            orchestrator = get_orchestrator()
            return orchestrator.fetch_prices(tickers)
        except Exception:
            pass  # Fall through to legacy behavior

    # Legacy behavior - use orchestrator for individual tickers with local caching
    global _price_cache
    now = time.time()
    cached_prices = {}

    # Check cache and fetch remaining via orchestrator individually
    for ticker in tickers:
        if ticker in _price_cache and now - _price_cache[ticker]['timestamp'] < PRICE_CACHE_DURATION:
            cached_prices[ticker] = _price_cache[ticker]['price']
        elif ticker not in _persistent_failures:
            # Use orchestrator for individual fetch (handles all provider fallbacks)
            price, source = _fetch_price_with_retries(ticker, max_retries=2)
            if price and price > 0:
                _price_cache[ticker] = {'price': float(price), 'timestamp': now, 'source': source}
                cached_prices[ticker] = float(price)

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
        orchestrator = get_orchestrator()

        # Fetch stock info from orchestrator
        info_result = orchestrator.fetch_stock_info(ticker)
        if not info_result.success or not info_result.data:
            return None

        info_data = info_result.data

        # Fetch current price from orchestrator
        price_result = orchestrator.fetch_price(ticker)
        current_price = price_result.data if price_result.success else None

        return {
            'ticker': ticker,
            'company_name': info_data.company_name,
            'current_price': current_price,
            'fifty_two_week_high': info_data.fifty_two_week_high,
            'fifty_two_week_low': info_data.fifty_two_week_low,
            'average_volume': None,  # Not available in StockInfoData yet
            'market_cap': info_data.market_cap,
            'pe_ratio': info_data.pe_ratio,
            'forward_pe': None,  # Not available in StockInfoData yet
            'dividend_yield': info_data.dividend_yield,
            'sector': info_data.sector,
            'industry': info_data.industry
        }
    except Exception as e:
        print(f"Error fetching stock info for {ticker}: {e}")
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
        orchestrator = get_orchestrator()
        result = orchestrator.fetch_dividends(ticker)

        if result.success and result.data:
            dividend_data = result.data
            return dividend_data.annual_dividend, dividend_data.payments

        return 0, []
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

"""
Stock utilities for data fetching.

Provides:
- Stock info fetching (PE ratio, market cap, sector, etc.)
- Dividend fetching
- Price fetching (delegates to provider orchestrator)

All data fetching uses the pluggable provider system (see services/providers/).
"""

# Import provider system (for orchestrator usage)
try:
    from services.providers import get_orchestrator
    _HAS_PROVIDERS = True
except ImportError:
    _HAS_PROVIDERS = False

# Track persistent failures to avoid repeated API calls
_persistent_failures = set()


def fetch_stock_price(ticker):
    """
    Fetch current stock price using the provider orchestrator.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Current price as float, or None if not available
    """
    if not _HAS_PROVIDERS:
        return None

    try:
        orchestrator = get_orchestrator()
        result = orchestrator.fetch_price(ticker)
        if result.success:
            return result.data
        return None
    except Exception:
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
    Fetch prices for multiple tickers using the provider orchestrator.

    Args:
        tickers: List of ticker symbols

    Returns:
        Dict mapping ticker to price
    """
    if not _HAS_PROVIDERS:
        return {}

    try:
        orchestrator = get_orchestrator()
        return orchestrator.fetch_prices(tickers)
    except Exception:
        return {}


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



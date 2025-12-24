"""
Alpaca Markets provider implementation.

Provides price data from Alpaca Data API.
Requires API key and secret - stored in data_private/secrets.json
"""

import time
from typing import Dict, List
from datetime import datetime, timedelta

from .base import PriceProvider, ProviderResult
from .secrets import get_secret

# Alpaca configuration
ALPACA_REQUEST_TIMEOUT = 15


def _get_alpaca_client():
    """Get Alpaca stock historical data client if configured."""
    api_key = get_secret('ALPACA_API_KEY')
    api_secret = get_secret('ALPACA_API_SECRET')
    api_endpoint = get_secret('ALPACA_API_ENDPOINT')

    if not api_key or not api_secret:
        return None

    try:
        from alpaca.data.historical import StockHistoricalDataClient

        # If custom endpoint is configured, use it
        if api_endpoint:
            return StockHistoricalDataClient(api_key, api_secret, url_override=api_endpoint)
        else:
            return StockHistoricalDataClient(api_key, api_secret)
    except ImportError:
        return None
    except Exception:
        return None


class AlpacaPriceProvider(PriceProvider):
    """
    Alpaca Markets price provider.

    Requires API key and secret to function. Supports batch fetching.
    """

    def __init__(self):
        self._client = None
        self._client_checked = False

    @property
    def name(self) -> str:
        return "alpaca"

    @property
    def display_name(self) -> str:
        return "Alpaca Markets"

    def is_available(self) -> bool:
        api_key = get_secret('ALPACA_API_KEY')
        api_secret = get_secret('ALPACA_API_SECRET')
        return bool(api_key and api_secret)

    @property
    def rate_limit(self) -> float:
        return 0.1  # 100ms between requests

    @property
    def supports_batch(self) -> bool:
        return True

    def _get_client(self):
        """Get or create the Alpaca client."""
        if not self._client_checked:
            self._client = _get_alpaca_client()
            self._client_checked = True
        return self._client

    def fetch_price(self, ticker: str) -> ProviderResult:
        """Fetch price for a single ticker."""
        result = self.fetch_prices([ticker])
        return result.get(ticker.upper(), ProviderResult(
            success=False,
            data=None,
            source=self.name,
            error="Ticker not found"
        ))

    def fetch_prices(self, tickers: List[str]) -> Dict[str, ProviderResult]:
        """Batch fetch prices using Alpaca Data API."""
        client = self._get_client()

        if not client:
            return {
                t.upper(): ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="Alpaca API not configured"
                ) for t in tickers
            }

        tickers = [t.upper() for t in tickers]
        results = {}

        # Log start for larger batches
        if len(tickers) > 5:
            try:
                from services.activity_log import activity_log
                activity_log.log("info", "alpaca", f"Fetching prices for {len(tickers)} tickers...")
            except Exception:
                pass

        try:
            from alpaca.data.requests import StockLatestQuoteRequest, StockLatestTradeRequest

            # Try to get latest trades (more reliable for price)
            try:
                request = StockLatestTradeRequest(symbol_or_symbols=tickers)
                trades = client.get_stock_latest_trade(request)

                for ticker in tickers:
                    if ticker in trades:
                        trade = trades[ticker]
                        price = float(trade.price)
                        if price > 0:
                            results[ticker] = ProviderResult(
                                success=True,
                                data=price,
                                source=self.name
                            )
                        else:
                            results[ticker] = ProviderResult(
                                success=False,
                                data=None,
                                source=self.name,
                                error="Invalid price"
                            )
                    else:
                        results[ticker] = ProviderResult(
                            success=False,
                            data=None,
                            source=self.name,
                            error="Ticker not in response"
                        )

            except Exception as e:
                # Fall back to quotes
                try:
                    request = StockLatestQuoteRequest(symbol_or_symbols=tickers)
                    quotes = client.get_stock_latest_quote(request)

                    for ticker in tickers:
                        if ticker in quotes:
                            quote = quotes[ticker]
                            # Use ask price, or bid if ask not available
                            price = quote.ask_price or quote.bid_price
                            if price and price > 0:
                                results[ticker] = ProviderResult(
                                    success=True,
                                    data=float(price),
                                    source=self.name
                                )
                            else:
                                results[ticker] = ProviderResult(
                                    success=False,
                                    data=None,
                                    source=self.name,
                                    error="No valid price in quote"
                                )
                        else:
                            results[ticker] = ProviderResult(
                                success=False,
                                data=None,
                                source=self.name,
                                error="Ticker not in quote response"
                            )
                except Exception as quote_error:
                    for ticker in tickers:
                        if ticker not in results:
                            results[ticker] = ProviderResult(
                                success=False,
                                data=None,
                                source=self.name,
                                error=str(quote_error)
                            )

            # Mark any missing tickers as failed
            for ticker in tickers:
                if ticker not in results:
                    results[ticker] = ProviderResult(
                        success=False,
                        data=None,
                        source=self.name,
                        error="Ticker not processed"
                    )

            # Log results for larger batches
            if len(tickers) > 5:
                success_count = sum(1 for r in results.values() if r.success)
                try:
                    from services.activity_log import activity_log
                    if success_count > 0:
                        activity_log.log("success", "alpaca", f"{success_count} prices fetched")
                    else:
                        activity_log.log("warning", "alpaca", "batch returned no data")
                except Exception:
                    pass

            return results

        except ImportError:
            try:
                from services.activity_log import activity_log
                activity_log.log("error", "alpaca", "alpaca-py not installed")
            except Exception:
                pass
            return {
                t: ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="alpaca-py not installed"
                ) for t in tickers
            }
        except Exception as e:
            try:
                from services.activity_log import activity_log
                activity_log.log("error", "alpaca", f"error - {str(e)[:50]}")
            except Exception:
                pass
            return {
                t: ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=str(e)
                ) for t in tickers
            }


def validate_alpaca_api_key(api_key: str, api_secret: str, api_endpoint: str = None) -> tuple:
    """
    Validate Alpaca API credentials.

    Args:
        api_key: Alpaca API key
        api_secret: Alpaca API secret
        api_endpoint: Optional custom API endpoint URL

    Returns:
        Tuple of (is_valid: bool, message: str)
    """
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest

        if api_endpoint:
            client = StockHistoricalDataClient(api_key, api_secret, url_override=api_endpoint)
        else:
            client = StockHistoricalDataClient(api_key, api_secret)

        # Try to fetch a price to validate
        request = StockLatestTradeRequest(symbol_or_symbols=["AAPL"])
        trades = client.get_stock_latest_trade(request)

        if "AAPL" in trades:
            return True, "API credentials are valid"
        else:
            return False, "Unexpected response format"

    except ImportError:
        return False, "alpaca-py not installed"
    except Exception as e:
        error_msg = str(e).lower()
        # Handle common error cases
        if "forbidden" in error_msg or "unauthorized" in error_msg or "401" in error_msg:
            return False, "Invalid API credentials"
        if "not found" in error_msg or "404" in error_msg:
            return False, "Invalid API endpoint or credentials"
        if "connection" in error_msg or "resolve" in error_msg:
            return False, "Cannot connect to Alpaca API - check endpoint URL"
        # Strip HTML from error messages
        error_str = str(e)
        if "<html" in error_str.lower():
            return False, "Invalid API credentials"
        # Return cleaned error
        return False, error_str[:100] if len(error_str) > 100 else error_str

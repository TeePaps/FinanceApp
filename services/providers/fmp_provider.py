"""
Financial Modeling Prep (FMP) provider implementation.

Provides price data from FMP API. Supports batch fetching when available.
API key required - stored in data_private/secrets.json
"""

import time
import requests
from typing import Dict, List

import config
from .base import PriceProvider, ProviderResult
from .secrets import get_fmp_api_key

# FMP API configuration - using stable endpoint (v3 is legacy)
FMP_BASE_URL = "https://financialmodelingprep.com/stable"
FMP_REQUEST_TIMEOUT = 15
FMP_BATCH_SIZE = 100  # Max tickers per batch request


class FMPPriceProvider(PriceProvider):
    """
    Financial Modeling Prep price provider.

    Requires API key to function. Supports batch fetching.
    Falls back to individual requests if batch endpoint is not available.
    """

    def __init__(self):
        self._batch_available = None  # None = not tested yet

    @property
    def name(self) -> str:
        return "fmp"

    @property
    def display_name(self) -> str:
        return "Financial Modeling Prep"

    def is_available(self) -> bool:
        return bool(get_fmp_api_key())

    @property
    def rate_limit(self) -> float:
        return 0.1  # 100ms between requests

    @property
    def supports_batch(self) -> bool:
        return True  # Will try batch, falls back to individual

    def fetch_price(self, ticker: str) -> ProviderResult:
        """Fetch price for a single ticker."""
        api_key = get_fmp_api_key()
        if not api_key:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error="FMP API key not configured"
            )

        ticker = ticker.upper()

        try:
            url = f"{FMP_BASE_URL}/quote?symbol={ticker}&apikey={api_key}"
            response = requests.get(url, timeout=FMP_REQUEST_TIMEOUT)

            if response.status_code == 401:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="Invalid FMP API key"
                )

            if response.status_code == 429:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="FMP rate limit exceeded"
                )

            if response.status_code != 200:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=f"FMP API error: {response.status_code}"
                )

            data = response.json()

            # Check for error response
            if isinstance(data, dict) and 'Error Message' in data:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=data['Error Message']
                )

            if data and len(data) > 0 and 'price' in data[0]:
                price = float(data[0]['price'])
                if price > 0:
                    return ProviderResult(
                        success=True,
                        data=price,
                        source=self.name
                    )

            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=f"No price data for {ticker}"
            )

        except requests.Timeout:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error="FMP API timeout"
            )
        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )

    def fetch_prices(self, tickers: List[str]) -> Dict[str, ProviderResult]:
        """
        Batch fetch prices.

        Tries batch endpoint first. If subscription doesn't support it,
        falls back to individual requests.
        """
        api_key = get_fmp_api_key()
        if not api_key:
            return {
                t: ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="FMP API key not configured"
                ) for t in tickers
            }

        tickers = [t.upper() for t in tickers]
        results = {}

        # Try batch endpoint first (if not known to be unavailable)
        if self._batch_available is not False:
            batch_results = self._fetch_batch(tickers, api_key)

            if batch_results is not None:
                self._batch_available = True
                return batch_results
            else:
                # Batch failed - might be subscription limitation
                self._batch_available = False
                print("[FMP] Batch endpoint not available, using individual requests")

        # Fall back to individual requests
        for ticker in tickers:
            results[ticker] = self.fetch_price(ticker)
            time.sleep(self.rate_limit)

        return results

    def _fetch_batch(self, tickers: List[str], api_key: str) -> Dict[str, ProviderResult]:
        """
        Try to fetch prices using batch endpoint.

        Returns None if batch endpoint is not available (subscription limitation).
        """
        results = {}

        try:
            # Process in chunks
            for i in range(0, len(tickers), FMP_BATCH_SIZE):
                batch = tickers[i:i + FMP_BATCH_SIZE]
                symbols = ','.join(batch)

                url = f"{FMP_BASE_URL}/quote?symbol={symbols}&apikey={api_key}"
                response = requests.get(url, timeout=FMP_REQUEST_TIMEOUT)

                if response.status_code == 401:
                    # Invalid API key
                    return {
                        t: ProviderResult(
                            success=False,
                            data=None,
                            source=self.name,
                            error="Invalid FMP API key"
                        ) for t in tickers
                    }

                if response.status_code == 403:
                    # Forbidden - likely subscription limitation
                    return None  # Signal to fall back to individual

                if response.status_code == 429:
                    # Rate limit - wait and retry
                    print(f"[FMP] Rate limit hit, waiting {config.FMP_RATE_LIMIT_BACKOFF}s...")
                    time.sleep(config.FMP_RATE_LIMIT_BACKOFF)
                    continue

                if response.status_code != 200:
                    # Mark batch as failed, try individual
                    for ticker in batch:
                        if ticker not in results:
                            results[ticker] = ProviderResult(
                                success=False,
                                data=None,
                                source=self.name,
                                error=f"API error: {response.status_code}"
                            )
                    continue

                data = response.json()

                # Check for subscription error
                if isinstance(data, dict):
                    if 'Error Message' in data:
                        error_msg = data['Error Message'].lower()
                        if 'upgrade' in error_msg or 'subscription' in error_msg or 'plan' in error_msg:
                            return None  # Signal to fall back to individual
                        # Other error
                        for ticker in batch:
                            results[ticker] = ProviderResult(
                                success=False,
                                data=None,
                                source=self.name,
                                error=data['Error Message']
                            )
                        continue

                # Process successful response
                if isinstance(data, list):
                    found_tickers = set()
                    for quote in data:
                        symbol = quote.get('symbol', '').upper()
                        price = quote.get('price')

                        if symbol and price is not None and price > 0:
                            results[symbol] = ProviderResult(
                                success=True,
                                data=float(price),
                                source=self.name
                            )
                            found_tickers.add(symbol)

                    # Mark not-found tickers as failed
                    for ticker in batch:
                        if ticker not in found_tickers and ticker not in results:
                            results[ticker] = ProviderResult(
                                success=False,
                                data=None,
                                source=self.name,
                                error="Ticker not in batch response"
                            )

                # Rate limiting between batches
                if i + FMP_BATCH_SIZE < len(tickers):
                    time.sleep(config.FMP_BATCH_INTERVAL)

            # Mark any remaining tickers as failed
            for ticker in tickers:
                if ticker not in results:
                    results[ticker] = ProviderResult(
                        success=False,
                        data=None,
                        source=self.name,
                        error="Ticker not processed"
                    )

            return results

        except requests.Timeout:
            return None  # Fall back to individual
        except Exception as e:
            print(f"[FMP] Batch error: {e}")
            return None  # Fall back to individual


def validate_fmp_api_key(api_key: str) -> tuple:
    """
    Validate an FMP API key.

    Returns:
        Tuple of (is_valid: bool, message: str)
    """
    try:
        url = f"{FMP_BASE_URL}/quote?symbol=AAPL&apikey={api_key}"
        response = requests.get(url, timeout=10)

        if response.status_code == 401:
            return False, "Invalid API key"

        if response.status_code == 403:
            return False, "API key forbidden (may be expired or invalid)"

        if response.status_code == 429:
            return True, "Rate limit exceeded (key is valid but rate limited)"

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                return True, "API key is valid"
            if isinstance(data, dict) and 'Error Message' in data:
                return False, data['Error Message']

        return False, f"Unexpected response: {response.status_code}"

    except requests.Timeout:
        return False, "Request timed out"
    except Exception as e:
        return False, str(e)

"""
Provider registry and data orchestrator.

The registry manages all available providers and the orchestrator coordinates
fetching data with configurable priority and automatic fallbacks.

Includes:
- Timeout handling for slow/hung providers
- Circuit breaker pattern to skip failing providers temporarily
"""

import time
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from .base import (
    BaseProvider, PriceProvider, EPSProvider, DividendProvider, HistoricalPriceProvider, StockInfoProvider, SelloffProvider,
    DataType, ProviderResult, PriceData, EPSData, DividendData, HistoricalPriceData, StockInfoData, SelloffData
)
from .config import get_config, ProviderConfig
from .circuit_breaker import get_circuit_breaker, CircuitBreaker


class ProviderRegistry:
    """
    Central registry for all data providers.

    Providers register themselves here and can be queried by data type.
    The registry handles provider availability and ordering.
    """

    def __init__(self):
        # All registered providers by name
        self._providers: Dict[str, BaseProvider] = {}

        # Providers grouped by data type
        self._by_type: Dict[DataType, List[BaseProvider]] = {
            DataType.PRICE: [],
            DataType.PRICE_HISTORY: [],
            DataType.EPS: [],
            DataType.DIVIDEND: [],
            DataType.STOCK_INFO: [],
            DataType.SELLOFF: [],
            DataType.SEC_METRICS: [],
            DataType.FILINGS: [],
        }

    def register(self, provider: BaseProvider):
        """
        Register a provider.

        Args:
            provider: Provider instance to register
        """
        self._providers[provider.name] = provider

        for data_type in provider.data_types:
            if provider not in self._by_type[data_type]:
                self._by_type[data_type].append(provider)

    def unregister(self, provider_name: str):
        """Remove a provider from the registry."""
        if provider_name in self._providers:
            provider = self._providers[provider_name]
            del self._providers[provider_name]

            for data_type in provider.data_types:
                if provider in self._by_type[data_type]:
                    self._by_type[data_type].remove(provider)

    def get_provider(self, name: str) -> Optional[BaseProvider]:
        """Get a provider by name."""
        return self._providers.get(name)

    def get_providers(self, data_type: DataType, config: Optional[ProviderConfig] = None) -> List[BaseProvider]:
        """
        Get all providers for a data type, filtered to available and enabled ones.

        Args:
            data_type: The type of data needed
            config: Optional config for checking disabled providers

        Returns:
            List of available providers (unordered)
        """
        if config is None:
            config = get_config()

        disabled = set(config.disabled_providers)
        return [p for p in self._by_type[data_type]
                if p.is_available() and p.name not in disabled]

    def get_providers_ordered(self, data_type: DataType, config: ProviderConfig) -> List[BaseProvider]:
        """
        Get providers for a data type in configured priority order.

        For PRICE data: Real-time providers are prioritized before historical-only
        providers, regardless of config order. This ensures we try all real-time
        sources before falling back to historical/delayed data.

        Batch-capable providers are moved to the front if prefer_batch is True.
        Disabled providers are excluded.

        Args:
            data_type: The type of data needed
            config: Provider configuration

        Returns:
            List of available and enabled providers in priority order
        """
        # Get configured order for this data type
        if data_type == DataType.PRICE:
            order = config.price_providers
        elif data_type == DataType.PRICE_HISTORY:
            # Use same order as price providers for historical data
            order = config.price_providers
        elif data_type == DataType.EPS:
            order = config.eps_providers
        elif data_type == DataType.DIVIDEND:
            order = config.dividend_providers
        elif data_type == DataType.STOCK_INFO:
            # Use same order as price providers for stock info
            order = config.price_providers
        elif data_type == DataType.SELLOFF:
            # Use same order as price providers for selloff data
            order = config.price_providers
        else:
            order = []

        # Get available providers (already filtered for disabled)
        available = {p.name: p for p in self.get_providers(data_type, config)}

        # Build ordered list
        ordered = []
        for name in order:
            if name in available:
                ordered.append(available[name])
                del available[name]

        # Add any remaining providers not in config
        ordered.extend(available.values())

        # If prefer_batch, move batch-capable providers to front
        if config.prefer_batch:
            batch_providers = [p for p in ordered if p.supports_batch]
            non_batch = [p for p in ordered if not p.supports_batch]
            ordered = batch_providers + non_batch

        # For PRICE data: ensure real-time providers come before historical-only
        # This guarantees we exhaust all real-time options before falling back
        # to delayed/historical data (e.g., weekly snapshots)
        if data_type == DataType.PRICE:
            realtime = [p for p in ordered if p.is_realtime]
            historical = [p for p in ordered if not p.is_realtime]
            ordered = realtime + historical

        return ordered

    def get_all_providers(self) -> List[BaseProvider]:
        """Get all registered providers."""
        return list(self._providers.values())

    def get_available_providers(self) -> List[Dict]:
        """Get status of all providers for API/UI display."""
        return [p.get_status() for p in self._providers.values()]


class DataOrchestrator:
    """
    Coordinates data fetching across multiple providers.

    Handles:
    - Provider priority and fallback
    - Database caching (valuations table)
    - Rate limiting
    - Batch optimization
    - Timeouts for slow providers
    - Circuit breaker for failing providers
    """

    def __init__(self, registry: ProviderRegistry):
        self.registry = registry

        # Rate limiting: {provider_name: last_request_time}
        self._last_request: Dict[str, float] = {}

        # Thread pool for timeout handling
        self._executor = ThreadPoolExecutor(max_workers=4)

    @property
    def config(self) -> ProviderConfig:
        """Always get the current config (supports runtime changes)."""
        return get_config()

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Get the circuit breaker instance."""
        return get_circuit_breaker()

    def _execute_with_timeout(
        self,
        func: Callable,
        timeout_seconds: Optional[float] = None
    ) -> Any:
        """
        Execute a function with a timeout.

        Args:
            func: Zero-argument callable to execute
            timeout_seconds: Max seconds to wait (None = use config default)

        Returns:
            Result of func()

        Raises:
            TimeoutError: If execution exceeds timeout
            Exception: Any exception raised by func
        """
        if timeout_seconds is None:
            timeout_seconds = self.config.provider_timeout_seconds

        future = self._executor.submit(func)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError:
            future.cancel()
            raise TimeoutError(f"Provider call timed out after {timeout_seconds}s")

    def _should_try_provider(self, provider: BaseProvider) -> bool:
        """
        Check if we should attempt to use this provider.

        Checks circuit breaker state if enabled.
        """
        if not self.config.circuit_breaker_enabled:
            return True
        return self.circuit_breaker.can_execute(provider.name)

    def _record_provider_success(self, provider: BaseProvider):
        """Record successful provider call."""
        if self.config.circuit_breaker_enabled:
            self.circuit_breaker.record_success(provider.name)

    def _record_provider_failure(self, provider: BaseProvider):
        """Record failed provider call."""
        if self.config.circuit_breaker_enabled:
            self.circuit_breaker.record_failure(provider.name)

    def _get_cache_max_age(self, data_type: DataType) -> timedelta:
        """Get cache duration based on data type."""
        if data_type == DataType.PRICE:
            return timedelta(seconds=self.config.price_cache_seconds)
        elif data_type == DataType.EPS:
            return timedelta(days=self.config.eps_cache_days)
        elif data_type == DataType.DIVIDEND:
            return timedelta(days=self.config.dividend_cache_days)
        else:
            return timedelta(minutes=5)

    def _get_cached_price(self, ticker: str) -> Optional[ProviderResult]:
        """Get cached price from database if valid."""
        import database as db
        ticker = ticker.upper()

        valuation = db.get_valuation(ticker)
        if not valuation:
            return None

        current_price = valuation.get('current_price')
        updated_str = valuation.get('updated')
        price_source = valuation.get('price_source', 'unknown')

        if not current_price or not updated_str:
            return None

        # Parse timestamp and check if still valid
        try:
            updated = datetime.fromisoformat(updated_str)
            max_age = self._get_cache_max_age(DataType.PRICE)

            if datetime.now() - updated < max_age:
                return ProviderResult(
                    success=True,
                    data=current_price,
                    source=price_source,
                    cached=True,
                    timestamp=updated
                )
        except (ValueError, TypeError):
            pass

        return None

    def _save_price_to_cache(self, ticker: str, price: float, source: str):
        """Save price to database cache."""
        import database as db
        ticker = ticker.upper()

        # Update just the price fields in the valuation
        db.bulk_update_valuations({
            ticker: {
                'current_price': price,
                'price_source': source,
            }
        })

    def _get_cached(self, data_type: DataType, ticker: str) -> Optional[ProviderResult]:
        """Get cached data - stub for EPS/dividend (not database cached yet)."""
        # Only prices use database caching
        if data_type == DataType.PRICE:
            return self._get_cached_price(ticker)
        # EPS and dividends don't have caching implemented yet
        return None

    def _set_cache(self, data_type: DataType, ticker: str, data: Any, source: str):
        """Set cache - stub for EPS/dividend (not database cached yet)."""
        # Only prices use database caching
        if data_type == DataType.PRICE:
            self._save_price_to_cache(ticker, data, source)
        # EPS and dividends don't have caching implemented yet
        pass

    def _rate_limit(self, provider: BaseProvider):
        """Apply rate limiting for a provider."""
        if provider.rate_limit <= 0:
            return

        last_time = self._last_request.get(provider.name, 0)
        elapsed = time.time() - last_time
        wait_time = provider.rate_limit - elapsed

        if wait_time > 0:
            time.sleep(wait_time)

        self._last_request[provider.name] = time.time()

    def fetch_price(self, ticker: str, skip_cache: bool = False) -> ProviderResult:
        """
        Fetch price for a single ticker.

        Tries providers in priority order until one succeeds.

        Args:
            ticker: Stock ticker symbol
            skip_cache: If True, bypass cache and fetch fresh

        Returns:
            ProviderResult with price data
        """
        ticker = ticker.upper()

        # Check database cache first (unless skipping)
        if not skip_cache:
            cached = self._get_cached_price(ticker)
            if cached:
                return cached

        # Try providers in order
        providers = self.registry.get_providers_ordered(DataType.PRICE, self.config)
        errors = []

        for provider in providers:
            if not isinstance(provider, PriceProvider):
                continue

            # Check circuit breaker before trying
            if not self._should_try_provider(provider):
                errors.append(f"{provider.name}: circuit open (skipped)")
                continue

            try:
                # Log provider attempt
                try:
                    from services.activity_log import activity_log
                    activity_log.log("info", provider.name, f"Trying {ticker}...")
                except Exception:
                    pass

                self._rate_limit(provider)

                # Execute with timeout
                result = self._execute_with_timeout(
                    lambda p=provider, t=ticker: p.fetch_price(t)
                )

                if result.success:
                    # Record success with circuit breaker
                    self._record_provider_success(provider)

                    # Log success
                    try:
                        from services.activity_log import activity_log
                        activity_log.log("success", provider.name, f"{ticker} = ${result.data:.2f}")
                    except ImportError:
                        pass

                    # Save to database cache
                    self._save_price_to_cache(ticker, result.data, provider.name)
                    return result
                else:
                    # Record failure with circuit breaker
                    self._record_provider_failure(provider)

                    # Log failure
                    try:
                        from services.activity_log import activity_log
                        activity_log.log("warning", provider.name, f"{ticker} failed")
                    except ImportError:
                        pass

                    errors.append(f"{provider.name}: {result.error}")

            except TimeoutError as e:
                # Timeout - record as failure
                self._record_provider_failure(provider)
                try:
                    from services.activity_log import activity_log
                    activity_log.log("error", provider.name, f"{ticker} timeout")
                except ImportError:
                    pass
                errors.append(f"{provider.name}: {str(e)}")

            except Exception as e:
                # Other exception - record as failure
                self._record_provider_failure(provider)
                try:
                    from services.activity_log import activity_log
                    activity_log.log("error", provider.name, f"{ticker} error")
                except ImportError:
                    pass
                errors.append(f"{provider.name}: {str(e)}")

        return ProviderResult(
            success=False,
            data=None,
            source="none",
            error=f"All providers failed: {'; '.join(errors)}"
        )

    def fetch_prices(self, tickers: List[str], skip_cache: bool = False, return_sources: bool = False):
        """
        Fetch prices for multiple tickers with provider fallbacks.

        Uses batch-capable providers first for efficiency.
        Failed tickers are retried with subsequent providers.

        Args:
            tickers: List of ticker symbols
            skip_cache: If True, bypass cache and fetch fresh
            return_sources: If True, return (prices, sources) tuple

        Returns:
            Dict mapping ticker to price (only successful fetches)
            If return_sources=True: Tuple of (prices dict, sources dict)
        """
        tickers = [t.upper() for t in tickers]
        results: Dict[str, float] = {}
        sources: Dict[str, str] = {}

        # Check database cache for each ticker (unless skipping)
        remaining = []
        if not skip_cache:
            for ticker in tickers:
                cached = self._get_cached_price(ticker)
                if cached and cached.success:
                    results[ticker] = cached.data
                    sources[ticker] = cached.source
                else:
                    remaining.append(ticker)
        else:
            remaining = list(tickers)

        if not remaining:
            return results

        # Get providers in order (batch-capable first if preferred)
        providers = self.registry.get_providers_ordered(DataType.PRICE, self.config)

        for provider in providers:
            if not remaining:
                break

            if not isinstance(provider, PriceProvider):
                continue

            # Check circuit breaker before trying
            if not self._should_try_provider(provider):
                continue

            try:
                self._rate_limit(provider)

                if provider.supports_batch:
                    # Batch fetch with timeout - only log for large batches (screener)
                    # Small fetches (1-5 tickers) are usually UI lookups, log them more quietly
                    if len(remaining) > 5:
                        try:
                            from services.activity_log import activity_log
                            activity_log.log("info", provider.name, f"Fetching prices for {len(remaining)} tickers...")
                        except ImportError:
                            pass

                    # Use longer timeout for batch (scales with ticker count)
                    batch_timeout = self.config.provider_timeout_seconds * max(1, len(remaining) // 50)
                    batch_results = self._execute_with_timeout(
                        lambda p=provider, t=remaining: p.fetch_prices(t),
                        timeout_seconds=batch_timeout
                    )

                    success_count = 0
                    for ticker, result in batch_results.items():
                        if result.success and result.data is not None:
                            results[ticker] = result.data
                            sources[ticker] = provider.name
                            self._save_price_to_cache(ticker, result.data, provider.name)
                            if ticker in remaining:
                                remaining.remove(ticker)
                            success_count += 1

                    # Log batch results - only log for large batches (screener operations)
                    if len(tickers) > 5:
                        try:
                            from services.activity_log import activity_log
                            if success_count > 0:
                                activity_log.log("success", provider.name, f"{success_count}/{len(tickers)} prices fetched")
                            else:
                                activity_log.log("warning", provider.name, f"Batch price fetch returned no data ({len(tickers)} tickers requested)")
                        except ImportError:
                            pass

                    # Record success/failure based on batch results
                    if success_count > 0:
                        self._record_provider_success(provider)
                    else:
                        self._record_provider_failure(provider)

                else:
                    # Individual fetch for remaining tickers
                    still_remaining = []
                    success_count = 0

                    for ticker in remaining:
                        try:
                            result = self._execute_with_timeout(
                                lambda p=provider, t=ticker: p.fetch_price(t)
                            )

                            if result.success and result.data is not None:
                                results[ticker] = result.data
                                sources[ticker] = provider.name
                                self._save_price_to_cache(ticker, result.data, provider.name)
                                success_count += 1
                            else:
                                still_remaining.append(ticker)

                        except TimeoutError:
                            still_remaining.append(ticker)
                        except Exception:
                            still_remaining.append(ticker)

                        self._rate_limit(provider)

                    remaining = still_remaining

                    # Record overall success/failure
                    if success_count > 0:
                        self._record_provider_success(provider)
                    elif len(still_remaining) == len(tickers):
                        self._record_provider_failure(provider)

            except TimeoutError as e:
                self._record_provider_failure(provider)
                try:
                    from services.activity_log import activity_log
                    activity_log.log("error", provider.name, "batch timeout")
                except ImportError:
                    pass

            except Exception as e:
                self._record_provider_failure(provider)
                try:
                    from services.activity_log import activity_log
                    activity_log.log("error", provider.name, f"error - {str(e)}")
                except ImportError:
                    pass

        if return_sources:
            return results, sources
        return results

    def fetch_eps(self, ticker: str) -> ProviderResult:
        """
        Fetch EPS history for a ticker.

        Tries providers in order, preferring authoritative sources.
        Includes timeout handling and circuit breaker for fault tolerance.

        Args:
            ticker: Stock ticker symbol

        Returns:
            ProviderResult with EPS data
        """
        ticker = ticker.upper()

        # Check cache first
        cached = self._get_cached(DataType.EPS, ticker)
        if cached:
            return cached

        # Try providers in order
        providers = self.registry.get_providers_ordered(DataType.EPS, self.config)
        errors = []

        for provider in providers:
            if not isinstance(provider, EPSProvider):
                continue

            # Check circuit breaker before trying
            if not self._should_try_provider(provider):
                errors.append(f"{provider.name}: circuit open (skipped)")
                continue

            try:
                self._rate_limit(provider)

                # Execute with timeout
                result = self._execute_with_timeout(
                    lambda p=provider, t=ticker: p.fetch_eps(t)
                )

                if result.success:
                    # Record success with circuit breaker
                    self._record_provider_success(provider)
                    self._set_cache(DataType.EPS, ticker, result.data, provider.name)
                    return result
                else:
                    # Record failure with circuit breaker
                    self._record_provider_failure(provider)
                    errors.append(f"{provider.name}: {result.error}")

            except TimeoutError as e:
                # Timeout - record as failure
                self._record_provider_failure(provider)
                errors.append(f"{provider.name}: {str(e)}")

            except Exception as e:
                # Other exception - record as failure
                self._record_provider_failure(provider)
                errors.append(f"{provider.name}: {str(e)}")

        return ProviderResult(
            success=False,
            data=None,
            source="none",
            error=f"All providers failed: {'; '.join(errors)}"
        )

    def fetch_dividends(self, ticker: str) -> ProviderResult:
        """
        Fetch dividend data for a ticker.

        Includes timeout handling and circuit breaker for fault tolerance.

        Args:
            ticker: Stock ticker symbol

        Returns:
            ProviderResult with dividend data
        """
        ticker = ticker.upper()

        # Check cache first
        cached = self._get_cached(DataType.DIVIDEND, ticker)
        if cached:
            return cached

        # Try providers in order
        providers = self.registry.get_providers_ordered(DataType.DIVIDEND, self.config)
        errors = []

        for provider in providers:
            if not isinstance(provider, DividendProvider):
                continue

            # Check circuit breaker before trying
            if not self._should_try_provider(provider):
                errors.append(f"{provider.name}: circuit open (skipped)")
                continue

            try:
                self._rate_limit(provider)

                # Execute with timeout
                result = self._execute_with_timeout(
                    lambda p=provider, t=ticker: p.fetch_dividends(t)
                )

                if result.success:
                    # Record success with circuit breaker
                    self._record_provider_success(provider)
                    self._set_cache(DataType.DIVIDEND, ticker, result.data, provider.name)
                    return result
                else:
                    # Record failure with circuit breaker
                    self._record_provider_failure(provider)
                    errors.append(f"{provider.name}: {result.error}")

            except TimeoutError as e:
                # Timeout - record as failure
                self._record_provider_failure(provider)
                errors.append(f"{provider.name}: {str(e)}")

            except Exception as e:
                # Other exception - record as failure
                self._record_provider_failure(provider)
                errors.append(f"{provider.name}: {str(e)}")

        return ProviderResult(
            success=False,
            data=None,
            source="none",
            error=f"All providers failed: {'; '.join(errors)}"
        )

    def fetch_stock_info(self, ticker: str) -> ProviderResult:
        """
        Fetch stock metadata for a ticker.

        Includes timeout handling and circuit breaker for fault tolerance.

        Args:
            ticker: Stock ticker symbol

        Returns:
            ProviderResult with StockInfoData
        """
        ticker = ticker.upper()

        # Try providers in order
        providers = self.registry.get_providers_ordered(DataType.STOCK_INFO, self.config)
        errors = []

        for provider in providers:
            if not isinstance(provider, StockInfoProvider):
                continue

            # Check circuit breaker before trying
            if not self._should_try_provider(provider):
                errors.append(f"{provider.name}: circuit open (skipped)")
                continue

            try:
                self._rate_limit(provider)

                # Execute with timeout
                result = self._execute_with_timeout(
                    lambda p=provider, t=ticker: p.fetch_stock_info(t)
                )

                if result.success:
                    # Record success with circuit breaker
                    self._record_provider_success(provider)
                    return result
                else:
                    # Record failure with circuit breaker
                    self._record_provider_failure(provider)
                    errors.append(f"{provider.name}: {result.error}")

            except TimeoutError as e:
                # Timeout - record as failure
                self._record_provider_failure(provider)
                errors.append(f"{provider.name}: {str(e)}")

            except Exception as e:
                # Other exception - record as failure
                self._record_provider_failure(provider)
                errors.append(f"{provider.name}: {str(e)}")

        return ProviderResult(
            success=False,
            data=None,
            source="none",
            error=f"All providers failed: {'; '.join(errors)}"
        )

    def fetch_selloff(self, ticker: str) -> ProviderResult:
        """
        Fetch selloff metrics for a ticker.

        Calculates selling pressure by comparing volume on down days to average volume.

        Args:
            ticker: Stock ticker symbol

        Returns:
            ProviderResult with SelloffData
        """
        ticker = ticker.upper()

        # Try providers in order
        providers = self.registry.get_providers_ordered(DataType.SELLOFF, self.config)
        errors = []

        for provider in providers:
            if not isinstance(provider, SelloffProvider):
                continue

            # Check circuit breaker before trying
            if not self._should_try_provider(provider):
                errors.append(f"{provider.name}: circuit open (skipped)")
                continue

            try:
                self._rate_limit(provider)

                # Execute with timeout
                result = self._execute_with_timeout(
                    lambda p=provider, t=ticker: p.fetch_selloff(t)
                )

                if result.success:
                    self._record_provider_success(provider)
                    return result
                else:
                    self._record_provider_failure(provider)
                    errors.append(f"{provider.name}: {result.error}")

            except TimeoutError as e:
                self._record_provider_failure(provider)
                errors.append(f"{provider.name}: {str(e)}")

            except Exception as e:
                self._record_provider_failure(provider)
                errors.append(f"{provider.name}: {str(e)}")

        return ProviderResult(
            success=False,
            data=None,
            source="none",
            error=f"All providers failed: {'; '.join(errors)}"
        )

    def fetch_price_history(self, ticker: str, period: str = '3mo') -> ProviderResult:
        """
        Fetch historical price data for a single ticker.

        Returns price history including current price, 1-month and 3-month changes.

        Args:
            ticker: Stock ticker symbol
            period: Time period ('1mo', '3mo', '6mo', '1y', etc.)

        Returns:
            ProviderResult with HistoricalPriceData
        """
        ticker = ticker.upper()

        # Try providers in order
        providers = self.registry.get_providers_ordered(DataType.PRICE_HISTORY, self.config)
        errors = []

        for provider in providers:
            if not isinstance(provider, HistoricalPriceProvider):
                continue

            # Check circuit breaker before trying
            if not self._should_try_provider(provider):
                errors.append(f"{provider.name}: circuit open (skipped)")
                continue

            try:
                self._rate_limit(provider)

                # Log provider attempt
                try:
                    from services.activity_log import activity_log
                    activity_log.log("info", provider.name, f"Trying {ticker}...")
                except ImportError:
                    pass

                # Execute with timeout
                result = self._execute_with_timeout(
                    lambda p=provider, t=ticker, per=period: p.fetch_price_history(t, per)
                )

                if result.success:
                    self._record_provider_success(provider)
                    try:
                        from services.activity_log import activity_log
                        activity_log.log("success", provider.name, f"{ticker} history OK")
                    except ImportError:
                        pass
                    return result
                else:
                    self._record_provider_failure(provider)
                    errors.append(f"{provider.name}: {result.error}")
                    try:
                        from services.activity_log import activity_log
                        activity_log.log("warning", provider.name, f"{ticker} failed")
                    except ImportError:
                        pass

            except TimeoutError as e:
                self._record_provider_failure(provider)
                errors.append(f"{provider.name}: {str(e)}")

            except Exception as e:
                self._record_provider_failure(provider)
                errors.append(f"{provider.name}: {str(e)}")

        return ProviderResult(
            success=False,
            data=None,
            source="none",
            error=f"All providers failed: {'; '.join(errors)}"
        )

    def fetch_price_history_batch(
        self,
        tickers: List[str],
        period: str = '3mo'
    ) -> Dict[str, ProviderResult]:
        """
        Fetch historical price data for multiple tickers.

        Uses batch-capable providers for efficiency.
        Returns price history including current price, 1-month and 3-month changes.

        Args:
            tickers: List of ticker symbols
            period: Time period ('1mo', '3mo', '6mo', '1y', etc.)

        Returns:
            Dict mapping ticker to ProviderResult with HistoricalPriceData
        """
        tickers = [t.upper() for t in tickers]
        results: Dict[str, ProviderResult] = {}

        if not tickers:
            return results

        # Get historical price providers
        providers = self.registry.get_providers_ordered(DataType.PRICE_HISTORY, self.config)

        remaining = list(tickers)

        for provider in providers:
            if not remaining:
                break

            if not isinstance(provider, HistoricalPriceProvider):
                continue

            # Check circuit breaker before trying
            if not self._should_try_provider(provider):
                continue

            try:
                self._rate_limit(provider)

                if provider.supports_batch:
                    # Use longer timeout for batch (scales with ticker count)
                    batch_timeout = self.config.provider_timeout_seconds * max(1, len(remaining) // 20)
                    batch_results = self._execute_with_timeout(
                        lambda p=provider, t=remaining, per=period: p.fetch_price_history_batch(t, per),
                        timeout_seconds=batch_timeout
                    )

                    success_count = 0
                    for ticker, result in batch_results.items():
                        if result.success:
                            results[ticker] = result
                            if ticker in remaining:
                                remaining.remove(ticker)
                            success_count += 1

                    # Log result
                    try:
                        from services.activity_log import activity_log
                        if success_count > 0:
                            activity_log.log("success", provider.name, f"{success_count} tickers history OK")
                        else:
                            activity_log.log("warning", provider.name, "no history data returned")
                    except ImportError:
                        pass

                    if success_count > 0:
                        self._record_provider_success(provider)
                    else:
                        self._record_provider_failure(provider)

                else:
                    # Individual fetch for remaining tickers
                    still_remaining = []
                    success_count = 0

                    for ticker in remaining:
                        try:
                            result = self._execute_with_timeout(
                                lambda p=provider, t=ticker, per=period: p.fetch_price_history(t, per)
                            )

                            if result.success:
                                results[ticker] = result
                                success_count += 1
                            else:
                                still_remaining.append(ticker)

                        except TimeoutError:
                            still_remaining.append(ticker)
                        except Exception:
                            still_remaining.append(ticker)

                        self._rate_limit(provider)

                    remaining = still_remaining

                    if success_count > 0:
                        self._record_provider_success(provider)
                    elif len(still_remaining) == len(tickers):
                        self._record_provider_failure(provider)

            except TimeoutError as e:
                self._record_provider_failure(provider)
                try:
                    from services.activity_log import activity_log
                    activity_log.log("error", provider.name, "history batch timeout")
                except ImportError:
                    pass

            except Exception as e:
                self._record_provider_failure(provider)
                try:
                    from services.activity_log import activity_log
                    activity_log.log("error", provider.name, f"history error - {str(e)[:30]}")
                except ImportError:
                    pass

        # Return failed results for remaining tickers
        for ticker in remaining:
            results[ticker] = ProviderResult(
                success=False,
                data=None,
                source="none",
                error="All providers failed for historical data"
            )

        return results

    def clear_cache(self, data_type: Optional[DataType] = None, ticker: Optional[str] = None) -> int:
        """
        Clear cached data in database by setting prices to NULL.

        Args:
            data_type: Clear only this data type (or all if None)
            ticker: Clear only this ticker (or all if None)

        Returns:
            Number of entries cleared
        """
        import database as db

        # For now, only price cache clearing is supported via database
        if data_type is not None and data_type != DataType.PRICE:
            return 0  # Only price caching uses database

        with db.get_db() as conn:
            cursor = conn.cursor()

            if ticker is None:
                # Clear all prices
                cursor.execute('UPDATE valuations SET current_price = NULL, price_source = NULL')
                cleared = cursor.rowcount
            else:
                # Clear specific ticker
                cursor.execute(
                    'UPDATE valuations SET current_price = NULL, price_source = NULL WHERE ticker = ?',
                    (ticker.upper(),)
                )
                cleared = cursor.rowcount

            return cleared

    def get_cache_stats(self) -> Dict:
        """Get cache statistics from database."""
        import database as db

        stats = {
            'total_entries': 0,
            'by_type': {dt.value: 0 for dt in DataType},
            'sources': {}
        }

        # Count cached prices (where current_price is not NULL and updated is recent)
        max_age = self._get_cache_max_age(DataType.PRICE)
        cutoff = (datetime.now() - max_age).isoformat()

        with db.get_db() as conn:
            cursor = conn.cursor()

            # Count valid cached prices
            cursor.execute('''
                SELECT COUNT(*) as count, price_source
                FROM valuations
                WHERE current_price IS NOT NULL AND updated > ?
                GROUP BY price_source
            ''', (cutoff,))

            for row in cursor.fetchall():
                count = row['count']
                source = row['price_source'] or 'unknown'
                stats['total_entries'] += count
                stats['by_type']['price'] += count
                stats['sources'][source] = stats['sources'].get(source, 0) + count

        return stats

    # SEC-specific methods
    # These provide access to SEC data through the orchestrator

    def fetch_sec_metrics(self, ticker: str) -> 'ProviderResult':
        """
        Fetch SEC metrics (multi-year EPS matrix + dividends) for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            ProviderResult with SECMetricsData
        """
        from .sec_provider import SECEPSProvider

        provider = self.registry.get_provider("sec_edgar")
        if not provider:
            return ProviderResult(
                success=False,
                data=None,
                source="none",
                error="SEC provider not available"
            )

        return provider.fetch_metrics(ticker)

    def fetch_filings(self, ticker: str) -> 'ProviderResult':
        """
        Fetch 10-K filing URLs for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            ProviderResult with FilingsData
        """
        provider = self.registry.get_provider("sec_edgar")
        if not provider:
            return ProviderResult(
                success=False,
                data=None,
                source="none",
                error="SEC provider not available"
            )

        return provider.fetch_filings(ticker)

    def get_sec_cache_status(self) -> Dict:
        """Get SEC data cache statistics."""
        from .sec_provider import SECEPSProvider
        return SECEPSProvider.get_cache_status()

    def get_sec_update_progress(self) -> Dict:
        """Get SEC background update progress."""
        from .sec_provider import SECEPSProvider
        return SECEPSProvider.get_update_progress()

    def start_sec_background_update(self, tickers: List[str]) -> bool:
        """Start SEC background update for tickers."""
        from .sec_provider import SECEPSProvider
        return SECEPSProvider.start_background_update(tickers)

    def stop_sec_background_update(self) -> None:
        """Stop SEC background update."""
        from .sec_provider import SECEPSProvider
        SECEPSProvider.stop_background_update()

    def get_eps_update_recommendations(self) -> Dict:
        """Get recommendations for which tickers need EPS updates."""
        from .sec_provider import SECEPSProvider
        return SECEPSProvider.get_eps_update_recommendations()

    def check_sec_startup(self, tickers: List[str]) -> None:
        """Run SEC startup checks and background updates if needed."""
        from .sec_provider import SECEPSProvider
        SECEPSProvider.check_and_update_on_startup(tickers)


# Global instances
_registry: Optional[ProviderRegistry] = None
_orchestrator: Optional[DataOrchestrator] = None


def get_registry() -> ProviderRegistry:
    """Get the global provider registry."""
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def get_orchestrator() -> DataOrchestrator:
    """Get the global data orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = DataOrchestrator(get_registry())
    return _orchestrator


def init_providers():
    """
    Initialize all providers.

    Should be called once at application startup.
    """
    from .yfinance_provider import (
        YFinancePriceProvider, YFinanceEPSProvider, YFinanceDividendProvider
    )
    from .sec_provider import SECEPSProvider
    from .fmp_provider import FMPPriceProvider
    from .alpaca_provider import AlpacaPriceProvider
    from .ibkr_provider import IBKRPriceProvider
    from .defeatbeta_provider import DefeatBetaPriceProvider, DefeatBetaEPSProvider

    registry = get_registry()

    # Register all providers
    # Real-time price providers
    registry.register(YFinancePriceProvider())
    registry.register(FMPPriceProvider())
    registry.register(AlpacaPriceProvider())
    registry.register(IBKRPriceProvider())

    # Historical-only price providers (used as fallback after real-time fails)
    registry.register(DefeatBetaPriceProvider())

    # EPS providers
    registry.register(SECEPSProvider())
    registry.register(YFinanceEPSProvider())
    registry.register(DefeatBetaEPSProvider())

    # Dividend providers
    registry.register(YFinanceDividendProvider())

    print(f"[Providers] Initialized {len(registry.get_all_providers())} providers")
    for provider in registry.get_all_providers():
        status = "available" if provider.is_available() else "unavailable"
        realtime_tag = "" if provider.is_realtime else " [historical]"
        print(f"  - {provider.display_name} ({provider.name}): {status}{realtime_tag}")

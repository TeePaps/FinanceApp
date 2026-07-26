"""
DefeatBeta API provider implementations.

DefeatBeta is an open-source alternative to Yahoo Finance that provides
market data via a DuckDB-backed dataset hosted on Hugging Face. It offers
no rate limits and efficient batch querying, but data is updated weekly
(not real-time).

Key characteristics:
- No rate limits (queries local cached parquet files)
- Supports batch operations via SQL
- Historical data only (updates weekly)
- No API key required
- Includes EPS, income statements, earnings transcripts

See: https://github.com/piotryordanov/defeatbeta-api
"""

import functools
import importlib.util
from typing import Dict, List
from datetime import datetime

from .base import (
    PriceProvider, EPSProvider,
    ProviderResult, DataType, PriceData, EPSData
)


@functools.lru_cache(maxsize=1)
def _is_defeatbeta_available() -> bool:
    """Check if the defeatbeta-api package is installed.

    Deliberately a spec lookup rather than a real import: importing
    defeatbeta_api executes an nltk.download() and a Hugging Face lookup at
    module scope, so probing availability - which init_providers() does for
    every provider at startup - would block on two third-party network calls
    and can raise non-ImportError exceptions when offline. The real import
    happens lazily at first data use instead.
    """
    return importlib.util.find_spec('defeatbeta_api') is not None


class DefeatBetaPriceProvider(PriceProvider):
    """
    DefeatBeta price provider.

    Provides historical price data from the DefeatBeta dataset.
    Data is updated weekly, so this is NOT a real-time source.
    Should be used as a fallback after real-time providers fail.
    """

    @property
    def name(self) -> str:
        return "defeatbeta"

    @property
    def display_name(self) -> str:
        return "DefeatBeta"

    def is_available(self) -> bool:
        return _is_defeatbeta_available()

    @property
    def rate_limit(self) -> float:
        return 0  # No rate limiting needed

    @property
    def supports_batch(self) -> bool:
        return True  # Can query multiple tickers via SQL

    @property
    def is_realtime(self) -> bool:
        return False  # Historical data only (weekly updates)

    def fetch_price(self, ticker: str) -> ProviderResult:
        """Fetch the most recent price for a single ticker."""
        ticker = ticker.upper()

        if not self.is_available():
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error="defeatbeta-api package not installed"
            )

        try:
            from defeatbeta_api.data.ticker import Ticker

            db_ticker = Ticker(ticker)
            price_df = db_ticker.price()

            if price_df is None or price_df.empty:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=f"No price data available for {ticker}"
                )

            # Get the most recent close price
            # DataFrame has columns: symbol, report_date, open, close, high, low, volume
            latest = price_df.iloc[-1]
            price = float(latest['close'])

            if price <= 0:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=f"Invalid price for {ticker}: {price}"
                )

            return ProviderResult(
                success=True,
                data=price,
                source=self.name
            )

        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )

    def _fetch_prices_batched(self, tickers: List[str]):
        """One DuckDB query returning the latest close for every ticker.

        Returns {ticker: ProviderResult} for the tickers found, or None if the
        batched path is unavailable - in which case the caller falls back to
        the per-ticker loop.
        """
        try:
            from defeatbeta_api.data.ticker import Ticker

            # The client exposes the shared DuckDB connection and the resolved
            # parquet URL through any Ticker instance.
            probe = Ticker(tickers[0])
            conn = getattr(probe, 'conn', None) or getattr(probe, 'client', None)
            query = getattr(conn, 'query', None) or getattr(conn, 'execute', None)
            url = getattr(probe, 'stock_prices_url', None)
            if query is None or not url:
                return None

            symbols = ", ".join("'" + t.replace("'", "''") + "'" for t in tickers)
            sql = f"""
                SELECT symbol, close FROM '{url}'
                WHERE symbol IN ({symbols})
                QUALIFY row_number() OVER (
                    PARTITION BY symbol ORDER BY report_date DESC) = 1
            """
            frame = query(sql)
            if hasattr(frame, 'df'):
                frame = frame.df()
            if frame is None or frame.empty:
                return None

            results = {}
            for _, row in frame.iterrows():
                symbol = str(row['symbol']).upper()
                try:
                    price = float(row['close'])
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    results[symbol] = ProviderResult(
                        success=True, data=price, source=self.name)

            for ticker in tickers:
                if ticker not in results:
                    results[ticker] = ProviderResult(
                        success=False, data=None, source=self.name,
                        error=f"No price data available for {ticker}")
            return results

        except Exception:
            # Any shape mismatch in the third-party client: fall back quietly.
            return None

    def fetch_prices(self, tickers: List[str]) -> Dict[str, ProviderResult]:
        """
        Batch fetch prices for multiple tickers.

        Uses a single DuckDB query across all symbols when the client exposes
        its connection, falling back to the per-ticker path otherwise.
        """
        tickers = [t.upper() for t in tickers]
        results = {}

        # Log start for larger batches
        if len(tickers) > 5:
            try:
                from services.activity_log import activity_log
                activity_log.log("info", "defeatbeta", f"Fetching prices for {len(tickers)} tickers...")
            except Exception:
                pass

        if not self.is_available():
            try:
                from services.activity_log import activity_log
                activity_log.log("error", "defeatbeta", "defeatbeta-api package not installed")
            except Exception:
                pass
            for ticker in tickers:
                results[ticker] = ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="defeatbeta-api package not installed"
                )
            return results

        # Try one DuckDB query for the whole set. Looping fetch_price() ran a
        # full remote parquet scan per ticker to read one closing price, which
        # made supports_batch=True a claim the provider did not honour.
        batched = self._fetch_prices_batched(tickers)
        if batched is not None:
            results.update(batched)
        else:
            for ticker in tickers:
                results[ticker] = self.fetch_price(ticker)

        # Log results for larger batches
        if len(tickers) > 5:
            success_count = sum(1 for r in results.values() if r.success)
            try:
                from services.activity_log import activity_log
                if success_count > 0:
                    activity_log.log("success", "defeatbeta", f"{success_count} prices fetched")
                else:
                    activity_log.log("warning", "defeatbeta", "batch returned no data")
            except Exception:
                pass

        return results


class DefeatBetaEPSProvider(EPSProvider):
    """
    DefeatBeta EPS provider.

    Extracts EPS data from income statements. Provides annual EPS values
    from the quarterly income statement data (using TTM or annual figures).
    """

    @property
    def name(self) -> str:
        return "defeatbeta"

    @property
    def display_name(self) -> str:
        return "DefeatBeta"

    def is_available(self) -> bool:
        return _is_defeatbeta_available()

    @property
    def rate_limit(self) -> float:
        return 0  # No rate limiting needed

    @property
    def is_authoritative(self) -> bool:
        return False  # SEC EDGAR is authoritative for EPS

    @property
    def is_realtime(self) -> bool:
        return False  # Historical data (weekly updates)

    def fetch_eps(self, ticker: str) -> ProviderResult:
        """
        Fetch EPS history from DefeatBeta income statement data.

        Uses quarterly income statement and extracts annual EPS. The DefeatBeta
        DataFrame has columns like 'Breakdown', 'TTM', '2024-09-30', etc.
        We aggregate quarterly data into annual figures.
        """
        ticker = ticker.upper()

        try:
            from services.activity_log import activity_log
            activity_log.log("info", "defeatbeta", f"Fetching EPS for {ticker}...", ticker=ticker)
        except Exception:
            pass

        if not self.is_available():
            try:
                from services.activity_log import activity_log
                activity_log.log("error", "defeatbeta", "defeatbeta-api package not installed")
            except Exception:
                pass
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error="defeatbeta-api package not installed"
            )

        try:
            from defeatbeta_api.data.ticker import Ticker

            db_ticker = Ticker(ticker)

            # Get quarterly income statement
            try:
                statement = db_ticker.quarterly_income_statement()
                # df() is a method that returns a DataFrame
                df = statement.df()
            except Exception as e:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=f"No income statement data for {ticker}: {e}"
                )

            if df is None or df.empty:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=f"Empty income statement for {ticker}"
                )

            eps_history = []

            # DefeatBeta DataFrame structure:
            # - 'Breakdown' column contains row names (e.g., 'Diluted EPS', 'Basic EPS')
            # - Other columns are dates: 'TTM', '2024-09-30', '2024-06-30', etc.

            # Find the EPS row (prefer Diluted EPS)
            eps_row_idx = None
            eps_type = None
            for idx, row_name in enumerate(df['Breakdown']):
                if row_name == 'Diluted EPS':
                    eps_row_idx = idx
                    eps_type = 'Diluted EPS'
                    break
                elif row_name == 'Basic EPS' and eps_row_idx is None:
                    eps_row_idx = idx
                    eps_type = 'Basic EPS'

            if eps_row_idx is None:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=f"No EPS data found in income statement for {ticker}"
                )

            # Get date columns (exclude 'Breakdown' and 'TTM')
            date_columns = [col for col in df.columns if col not in ['Breakdown', 'TTM']]

            # Aggregate quarterly EPS into annual figures
            # Group by fiscal year (use the year from the date column)
            yearly_eps = {}  # {year: [eps_values]}

            for col in date_columns:
                try:
                    # Parse year from column name (e.g., '2024-09-30' -> 2024)
                    year = int(str(col)[:4])
                    eps_value = df.loc[eps_row_idx, col]

                    # Handle various value formats
                    if eps_value == '*' or eps_value == '' or eps_value is None:
                        continue

                    eps_float = float(eps_value)

                    # Skip NaN
                    if eps_float != eps_float:
                        continue

                    if year not in yearly_eps:
                        yearly_eps[year] = []
                    yearly_eps[year].append(eps_float)

                except (ValueError, TypeError, KeyError):
                    continue

            # Sum quarterly EPS to get annual EPS. Only emit a year with all
            # four quarters — the source typically carries just 1-3 quarters
            # for the in-progress year, and summing those produced a fractional
            # value that (after the descending sort) became the FIRST, most
            # heavily weighted entry in the fair-value average, understating it.
            # The current year is covered by the TTM entry below instead.
            for year, quarters in yearly_eps.items():
                if len(quarters) >= 4:
                    annual_eps = sum(quarters)
                    eps_history.append({
                        'year': year,
                        'eps': annual_eps,
                        'eps_type': eps_type,
                        'quarters': len(quarters),
                        'source': self.name
                    })

            # Also add TTM if available. Explicit sentinel checks (not
            # truthiness) so a legitimate break-even TTM of 0.0 is kept — the
            # quarterly loop above already uses explicit checks for the same
            # reason.
            try:
                ttm_value = df.loc[eps_row_idx, 'TTM']
                if ttm_value is not None and ttm_value != '*' and ttm_value != '':
                    ttm_eps = float(ttm_value)
                    if ttm_eps == ttm_eps:  # Not NaN
                        # Add as current year if not already present
                        current_year = datetime.now().year
                        if not any(e['year'] == current_year for e in eps_history):
                            eps_history.append({
                                'year': current_year,
                                'eps': ttm_eps,
                                'eps_type': f'TTM {eps_type}',
                                'source': self.name
                            })
            except (ValueError, TypeError, KeyError):
                pass

            if not eps_history:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=f"No valid EPS values found for {ticker}"
                )

            # Sort by year descending
            eps_history.sort(key=lambda x: x['year'], reverse=True)

            # Limit to 10 years
            eps_history = eps_history[:10]

            eps_data = EPSData(
                ticker=ticker,
                source=self.name,
                eps_history=eps_history,
                company_name=ticker  # DefeatBeta doesn't easily provide company name
            )

            try:
                from services.activity_log import activity_log
                activity_log.log("success", "defeatbeta", f"{ticker} EPS: {len(eps_history)} years", ticker=ticker)
            except Exception:
                pass

            return ProviderResult(
                success=True,
                data=eps_data,
                source=self.name
            )

        except Exception as e:
            try:
                from services.activity_log import activity_log
                activity_log.log("error", "defeatbeta", f"{ticker} error: {str(e)[:50]}", ticker=ticker)
            except Exception:
                pass
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )

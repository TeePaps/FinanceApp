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

from typing import Dict, List
from datetime import datetime

from .base import (
    PriceProvider, EPSProvider,
    ProviderResult, DataType, PriceData, EPSData
)


def _is_defeatbeta_available() -> bool:
    """Check if defeatbeta-api package is installed."""
    try:
        import defeatbeta_api
        return True
    except ImportError:
        return False


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

    def fetch_prices(self, tickers: List[str]) -> Dict[str, ProviderResult]:
        """
        Batch fetch prices for multiple tickers.

        DefeatBeta uses DuckDB which can efficiently query multiple symbols,
        but the API is ticker-by-ticker. We loop but benefit from cached data.
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

            # Sum quarterly EPS to get annual EPS
            for year, quarters in yearly_eps.items():
                if len(quarters) >= 1:  # Accept partial years
                    annual_eps = sum(quarters)
                    eps_history.append({
                        'year': year,
                        'eps': annual_eps,
                        'eps_type': eps_type,
                        'quarters': len(quarters),
                        'source': self.name
                    })

            # Also add TTM if available
            try:
                ttm_value = df.loc[eps_row_idx, 'TTM']
                if ttm_value and ttm_value != '*' and ttm_value != '':
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

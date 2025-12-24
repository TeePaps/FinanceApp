"""
SEC EDGAR provider implementation.

Wraps existing sec_data.py logic to provide authoritative EPS data
from SEC 10-K filings through the standard provider interface.
"""

import sys
import os

# Add parent directory to path for importing sec_data
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from typing import Dict, List
from .base import EPSProvider, ProviderResult, EPSData, SECMetricsData, FilingsData


class SECEPSProvider(EPSProvider):
    """
    SEC EDGAR EPS provider.

    Provides authoritative EPS data from SEC 10-K filings.
    Wraps the existing sec_data module.
    """

    @property
    def name(self) -> str:
        return "sec_edgar"

    @property
    def display_name(self) -> str:
        return "SEC EDGAR"

    def is_available(self) -> bool:
        # SEC EDGAR is always available (no API key required)
        return True

    @property
    def rate_limit(self) -> float:
        return 0.12  # SEC limit: 10 requests/second

    @property
    def is_authoritative(self) -> bool:
        return True  # SEC filings are the authoritative source

    def fetch_eps(self, ticker: str) -> ProviderResult:
        """
        Fetch EPS history from SEC EDGAR.

        Uses existing sec_data module for the actual fetching.
        """
        ticker = ticker.upper()

        try:
            from services.activity_log import activity_log
            activity_log.log("info", "sec", f"Fetching EPS for {ticker}...", ticker=ticker)
        except Exception:
            pass

        try:
            # Import sec_data here to avoid circular imports
            import sec_data

            # Use existing function to get SEC EPS data
            data = sec_data.get_sec_eps(ticker)

            if not data:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="No SEC data available for ticker"
                )

            eps_history = data.get('eps_history', [])

            if not eps_history:
                # Check if SEC explicitly has no EPS for this ticker
                if data.get('sec_no_eps'):
                    return ProviderResult(
                        success=False,
                        data=None,
                        source=self.name,
                        error=data.get('reason', 'SEC has no EPS data for this company')
                    )
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="Empty EPS history from SEC"
                )

            # Convert to standard format
            standardized_history = []
            for eps in eps_history:
                standardized_history.append({
                    'year': eps.get('year'),
                    'eps': eps.get('eps'),
                    'eps_type': eps.get('eps_type', 'Diluted EPS'),
                    'filed': eps.get('filed'),
                    'period_start': eps.get('start') or eps.get('period_start'),
                    'period_end': eps.get('end') or eps.get('period_end'),
                    'source': self.name
                })

            eps_data = EPSData(
                ticker=ticker,
                source=self.name,
                eps_history=standardized_history,
                company_name=data.get('company_name')
            )

            try:
                from services.activity_log import activity_log
                activity_log.log("success", "sec", f"{ticker} EPS: {len(standardized_history)} years", ticker=ticker)
            except Exception:
                pass

            return ProviderResult(
                success=True,
                data=eps_data,
                source=self.name
            )

        except ImportError as e:
            try:
                from services.activity_log import activity_log
                activity_log.log("error", "sec", f"{ticker} sec_data module not available", ticker=ticker)
            except Exception:
                pass
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=f"sec_data module not available: {e}"
            )
        except Exception as e:
            try:
                from services.activity_log import activity_log
                activity_log.log("error", "sec", f"{ticker} error: {str(e)[:50]}", ticker=ticker)
            except Exception:
                pass
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )

    def fetch_eps_fresh(self, ticker: str) -> ProviderResult:
        """
        Force fetch fresh EPS data from SEC (bypass cache).

        Checks for any new fiscal years not in database.
        """
        ticker = ticker.upper()

        try:
            from services.activity_log import activity_log
            activity_log.log("info", "sec", f"Force refreshing {ticker}...", ticker=ticker)
        except Exception:
            pass

        try:
            import sec_data

            # Use force refresh function
            data, new_years = sec_data.force_refresh_sec_eps(ticker)

            if not data:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="Failed to fetch from SEC"
                )

            eps_history = data.get('eps_history', [])

            if not eps_history:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="No EPS data from SEC"
                )

            # Convert to standard format
            standardized_history = []
            for eps in eps_history:
                standardized_history.append({
                    'year': eps.get('year'),
                    'eps': eps.get('eps'),
                    'eps_type': eps.get('eps_type', 'Diluted EPS'),
                    'filed': eps.get('filed'),
                    'period_start': eps.get('start') or eps.get('period_start'),
                    'period_end': eps.get('end') or eps.get('period_end'),
                    'source': self.name
                })

            eps_data = EPSData(
                ticker=ticker,
                source=self.name,
                eps_history=standardized_history,
                company_name=data.get('company_name')
            )

            result = ProviderResult(
                success=True,
                data=eps_data,
                source=self.name
            )

            # Add metadata about new years found
            result.metadata = {'new_years_added': new_years}

            try:
                from services.activity_log import activity_log
                if new_years:
                    activity_log.log("success", "sec", f"{ticker} refreshed, {new_years} new years", ticker=ticker)
                else:
                    activity_log.log("success", "sec", f"{ticker} refreshed, no new years", ticker=ticker)
            except Exception:
                pass

            return result

        except Exception as e:
            try:
                from services.activity_log import activity_log
                activity_log.log("error", "sec", f"{ticker} refresh error: {str(e)[:50]}", ticker=ticker)
            except Exception:
                pass
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )

    def fetch_metrics(self, ticker: str) -> ProviderResult:
        """
        Fetch SEC metrics (multi-year EPS matrix + dividends).

        Wraps sec_data.get_sec_metrics().
        """
        ticker = ticker.upper()

        try:
            import sec_data
            data = sec_data.get_sec_metrics(ticker)

            if not data:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="No SEC metrics available"
                )

            return ProviderResult(
                success=True,
                data=SECMetricsData(
                    ticker=ticker,
                    source=self.name,
                    eps_matrix=data.get('eps_by_year', []),
                    dividend_history=data.get('dividends', []),
                    company_name=data.get('company_name'),
                    cik=data.get('cik')
                ),
                source=self.name
            )

        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )

    def fetch_filings(self, ticker: str) -> ProviderResult:
        """
        Fetch 10-K filing URLs from SEC.

        Wraps sec_data.get_10k_filings().
        """
        ticker = ticker.upper()

        try:
            import sec_data
            filings = sec_data.get_10k_filings(ticker)

            if not filings:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="No 10-K filings found"
                )

            return ProviderResult(
                success=True,
                data=FilingsData(
                    ticker=ticker,
                    source=self.name,
                    filings=filings
                ),
                source=self.name
            )

        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )

    # Static methods for SEC infrastructure operations
    # These don't follow the provider pattern but are exposed through the orchestrator

    @staticmethod
    def get_cache_status() -> Dict:
        """Get SEC data cache statistics. Wraps sec_data.get_cache_status()."""
        try:
            import sec_data
            return sec_data.get_cache_status()
        except Exception:
            return {}

    @staticmethod
    def get_update_progress() -> Dict:
        """Get SEC background update progress. Wraps sec_data.get_update_progress()."""
        try:
            import sec_data
            return sec_data.get_update_progress()
        except Exception:
            return {'status': 'unknown'}

    @staticmethod
    def start_background_update(tickers: List[str]) -> bool:
        """Start SEC background update for tickers. Wraps sec_data.start_background_update()."""
        try:
            import sec_data
            return sec_data.start_background_update(tickers)
        except Exception:
            return False

    @staticmethod
    def stop_background_update() -> None:
        """Stop SEC background update. Wraps sec_data.stop_update()."""
        try:
            import sec_data
            sec_data.stop_update()
        except Exception:
            pass

    @staticmethod
    def get_eps_update_recommendations() -> Dict:
        """Get recommendations for EPS updates. Wraps sec_data.get_eps_update_recommendations()."""
        try:
            import sec_data
            return sec_data.get_eps_update_recommendations()
        except Exception:
            return {}

    @staticmethod
    def check_and_update_on_startup(tickers: List[str]) -> None:
        """Run SEC startup checks. Wraps sec_data.check_and_update_on_startup()."""
        try:
            import sec_data
            sec_data.check_and_update_on_startup(tickers)
        except Exception:
            pass

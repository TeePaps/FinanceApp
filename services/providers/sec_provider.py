"""
SEC EDGAR provider implementation.

Wraps existing sec_data.py logic to provide authoritative EPS data
from SEC 10-K filings through the standard provider interface.
"""

import sys
import os

# Add parent directory to path for importing sec_data
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from .base import EPSProvider, ProviderResult, EPSData


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

            return ProviderResult(
                success=True,
                data=eps_data,
                source=self.name
            )

        except ImportError as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=f"sec_data module not available: {e}"
            )
        except Exception as e:
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
            result.new_years_added = new_years

            return result

        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )

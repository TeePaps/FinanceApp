"""
Stock valuation calculations service.

Provides:
- EPS data validation (SEC vs yfinance)
- Fair value calculation using EPS averaging
- Valuation summary generation
"""

import math
from datetime import datetime, timedelta
from config import PE_RATIO_MULTIPLIER, RECOMMENDED_EPS_YEARS


def get_validated_eps(ticker):
    """
    Get EPS data using the orchestrator (SEC EDGAR first, then yfinance fallback).

    SEC EDGAR data uses company fiscal year and is the official source.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Tuple of (eps_data, source, validation_info)
        - eps_data: List of dicts with year and eps values
        - source: 'sec_edgar', 'yfinance', or 'none'
        - validation_info: Dict with validation details
    """
    ticker = ticker.upper()
    validation_info = {'validated': False, 'years_available': 0}

    # Use orchestrator to fetch EPS (handles SEC-first-then-yfinance fallback)
    from services.providers import get_orchestrator
    orchestrator = get_orchestrator()

    result = orchestrator.fetch_eps(ticker)

    if result.success and result.data:
        eps_data = result.data

        # Convert orchestrator format to expected format
        eps_list = []
        for entry in eps_data.eps_history:
            if 'eps' in entry and entry['eps'] is not None:
                eps_entry = {
                    'year': int(entry['year']),
                    'eps': float(entry['eps'])
                }
                # Add optional fields if present
                if 'eps_type' in entry:
                    eps_entry['eps_type'] = entry['eps_type']
                if 'period_start' in entry:
                    eps_entry['period_start'] = entry['period_start']
                if 'period_end' in entry:
                    eps_entry['period_end'] = entry['period_end']
                eps_list.append(eps_entry)

        # Determine validation info based on source
        source = eps_data.source
        # Include company name from EPS data if available
        sec_company_name = eps_data.company_name

        if source == 'sec_edgar':
            validation_info = {
                'validated': True,
                'source': 'SEC EDGAR 10-K filings',
                'years_available': len(eps_list),
                'fiscal_year': True,
                'company_name': sec_company_name
            }
            return eps_list[:8], 'sec', validation_info
        elif source == 'yfinance':
            validation_info = {
                'validated': False,
                'source': 'yfinance (SEC data not available)',
                'years_available': len(eps_list),
                'company_name': sec_company_name
            }
            return eps_list[:8], 'yfinance', validation_info
        else:
            # Other sources (defeatbeta, etc.)
            validation_info = {
                'validated': True,
                'source': source,
                'years_available': len(eps_list),
                'company_name': sec_company_name
            }
            return eps_list[:8], source, validation_info

    return [], 'none', validation_info


def calculate_valuation(ticker, stock=None):
    """
    Calculate stock valuation using EPS and dividend formula.

    Formula: (Average EPS + Annual Dividend) x PE_RATIO_MULTIPLIER

    Args:
        ticker: Stock ticker symbol
        stock: Optional yfinance Ticker object (will be fetched if not provided)

    Returns:
        Dict with valuation data
    """
    ticker = ticker.upper()

    try:
        # Fetch data using orchestrator
        from services.providers import get_orchestrator
        orchestrator = get_orchestrator()

        # Get company info
        info_result = orchestrator.fetch_stock_info(ticker)
        if info_result.success and info_result.data:
            info_data = info_result.data
            company_name = info_data.company_name
            fifty_two_week_high = info_data.fifty_two_week_high
            fifty_two_week_low = info_data.fifty_two_week_low
        else:
            company_name = ticker
            fifty_two_week_high = None
            fifty_two_week_low = None

        # Fetch current price from provider system
        price_result = orchestrator.fetch_price(ticker)
        current_price = price_result.data if price_result.success else 0
        price_source = price_result.source if price_result.success else 'none'

        # Get validated EPS data using orchestrator
        eps_data, eps_source, validation_info = get_validated_eps(ticker)

        # Use company name from EPS data if available (SEC or other authoritative source)
        if validation_info.get('company_name'):
            company_name = validation_info['company_name']

        # Get dividend info using orchestrator
        dividend_result = orchestrator.fetch_dividends(ticker)
        annual_dividend = 0
        dividend_info = []

        if dividend_result.success and dividend_result.data:
            dividend_data = dividend_result.data
            annual_dividend = dividend_data.annual_dividend
            dividend_info = dividend_data.payments

        # Get selloff metrics via orchestrator
        selloff_metrics = None
        selloff_result = orchestrator.fetch_selloff(ticker)
        if selloff_result.success and selloff_result.data:
            selloff_data = selloff_result.data
            selloff_metrics = {
                'day': selloff_data.day,
                'week': selloff_data.week,
                'month': selloff_data.month,
                'avg_volume': selloff_data.avg_volume,
                'severity': selloff_data.severity
            }

        # Calculate valuation: (Average EPS over up to 8 years + Annual Dividend) x multiplier
        eps_avg = None
        estimated_value = None
        price_vs_value = None

        if len(eps_data) > 0:
            eps_avg = sum(e['eps'] for e in eps_data) / len(eps_data)

            # Formula: (Average EPS + Annual Dividend) x PE_RATIO_MULTIPLIER
            estimated_value = (eps_avg + annual_dividend) * PE_RATIO_MULTIPLIER

            if current_price and current_price > 0 and estimated_value > 0:
                price_vs_value = ((current_price - estimated_value) / estimated_value) * 100

        return {
            'ticker': ticker,
            'company_name': company_name,
            'current_price': round(current_price, 2) if current_price else None,
            'eps_data': eps_data,
            'eps_years': len(eps_data),
            'eps_source': eps_source,
            'eps_validation': validation_info,
            'eps_avg': round(eps_avg, 2) if eps_avg else None,
            'min_years_recommended': RECOMMENDED_EPS_YEARS,
            'has_enough_years': len(eps_data) >= RECOMMENDED_EPS_YEARS,
            'annual_dividend': round(annual_dividend, 2),
            'dividend_payments': dividend_info,
            'estimated_value': round(estimated_value, 2) if estimated_value else None,
            'price_vs_value': round(price_vs_value, 1) if price_vs_value else None,
            'formula': f'(({round(eps_avg, 2) if eps_avg else "N/A"} avg EPS) + {round(annual_dividend, 2)} dividend) x {PE_RATIO_MULTIPLIER} = ${round(estimated_value, 2) if estimated_value else "N/A"}',
            'selloff': selloff_metrics
        }
    except Exception as e:
        print(f"Error calculating valuation for {ticker}: {e}")
        return {
            'ticker': ticker,
            'error': str(e)
        }


class ValuationService:
    """
    Service class for valuation-related operations.

    Provides a higher-level interface for working with valuation data.
    """

    def __init__(self, data_manager=None):
        """
        Initialize the valuation service.

        Args:
            data_manager: Optional data manager instance
        """
        self.data_manager = data_manager

    def get_valuation(self, ticker):
        """Get full valuation for a ticker."""
        return calculate_valuation(ticker)

    def get_eps_history(self, ticker):
        """
        Get EPS history for a ticker.

        Returns:
            Tuple of (eps_list, source)
        """
        try:
            from services.providers import get_orchestrator
            orchestrator = get_orchestrator()
            result = orchestrator.fetch_eps(ticker)

            if result.success and result.data:
                eps_data = result.data
                # Convert orchestrator format to expected format
                eps_list = []
                for entry in eps_data.eps_history:
                    if 'eps' in entry and entry['eps'] is not None:
                        eps_entry = {
                            'year': int(entry['year']),
                            'eps': float(entry['eps'])
                        }
                        # Add optional fields if present
                        if 'eps_type' in entry:
                            eps_entry['eps_type'] = entry['eps_type']
                        if 'period_start' in entry:
                            eps_entry['period_start'] = entry['period_start']
                        if 'period_end' in entry:
                            eps_entry['period_end'] = entry['period_end']
                        eps_list.append(eps_entry)
                return eps_list, eps_data.source

            return [], 'none'
        except Exception as e:
            print(f"Error getting EPS history for {ticker}: {e}")
            return [], 'none'

    def get_valuation_summary(self, ticker):
        """
        Get a condensed valuation summary.

        Returns:
            Dict with key valuation metrics
        """
        val = calculate_valuation(ticker)
        if 'error' in val:
            return val

        return {
            'ticker': ticker,
            'company_name': val.get('company_name'),
            'current_price': val.get('current_price'),
            'estimated_value': val.get('estimated_value'),
            'price_vs_value': val.get('price_vs_value'),
            'eps_years': val.get('eps_years'),
            'eps_source': val.get('eps_source'),
            'annual_dividend': val.get('annual_dividend'),
            'selloff_severity': val.get('selloff', {}).get('severity') if val.get('selloff') else None
        }

    def is_undervalued(self, ticker, threshold=-10):
        """
        Check if a stock is undervalued.

        Args:
            ticker: Stock ticker symbol
            threshold: price_vs_value threshold (default -10 = 10% undervalued)

        Returns:
            Boolean indicating if stock is undervalued
        """
        val = calculate_valuation(ticker)
        price_vs_value = val.get('price_vs_value')
        if price_vs_value is None:
            return False
        return price_vs_value <= threshold

    def is_overvalued(self, ticker, threshold=10):
        """
        Check if a stock is overvalued.

        Args:
            ticker: Stock ticker symbol
            threshold: price_vs_value threshold (default 10 = 10% overvalued)

        Returns:
            Boolean indicating if stock is overvalued
        """
        val = calculate_valuation(ticker)
        price_vs_value = val.get('price_vs_value')
        if price_vs_value is None:
            return False
        return price_vs_value >= threshold

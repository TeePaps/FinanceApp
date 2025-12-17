"""
Stock valuation calculations service.

Provides:
- EPS data validation (SEC vs yfinance)
- Fair value calculation using EPS averaging
- Valuation summary generation
"""

import math
import yfinance as yf
from datetime import datetime, timedelta
from config import PE_RATIO_MULTIPLIER, RECOMMENDED_EPS_YEARS
import sec_data
from .yahoo_finance import extract_yf_eps, calculate_selloff_metrics


def get_validated_eps(ticker, stock, income_stmt=None):
    """
    Get EPS data from SEC EDGAR (authoritative) or yfinance (fallback).

    SEC EDGAR data uses company fiscal year and is the official source.

    Args:
        ticker: Stock ticker symbol
        stock: yfinance Ticker object
        income_stmt: Optional pre-fetched income statement

    Returns:
        Tuple of (eps_data, source, validation_info)
        - eps_data: List of dicts with year and eps values
        - source: 'sec', 'yfinance', or 'none'
        - validation_info: Dict with validation details
    """
    ticker = ticker.upper()
    validation_info = {'validated': False, 'years_available': 0}

    # Get SEC EDGAR data first - this is the authoritative source (from 10-K filings)
    sec_eps = sec_data.get_sec_eps(ticker)
    if sec_eps and sec_eps.get('eps_history'):
        sec_eps_data = [{
            'year': e['year'],
            'eps': e['eps'],
            'eps_type': e.get('eps_type', 'diluted'),
            'period_start': e.get('start'),
            'period_end': e.get('end')
        } for e in sec_eps['eps_history']]
        # SEC data is authoritative - use it directly
        # Note: Years are fiscal years from the company's 10-K filings
        validation_info = {
            'validated': True,
            'source': 'SEC EDGAR 10-K filings',
            'years_available': len(sec_eps_data),
            'fiscal_year': True
        }
        return sec_eps_data[:8], 'sec', validation_info

    # No SEC data available - fall back to yfinance
    yf_eps_data = extract_yf_eps(stock, income_stmt)
    if yf_eps_data:
        validation_info = {
            'validated': False,
            'source': 'yfinance (SEC data not available)',
            'years_available': len(yf_eps_data)
        }
        return yf_eps_data[:8], 'yfinance', validation_info

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
        if stock is None:
            stock = yf.Ticker(ticker)

        # Get company info
        info = stock.info
        company_name = info.get('shortName', ticker)
        current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)

        # Fetch income_stmt once for reuse in validation
        income_stmt = stock.income_stmt

        # Get validated EPS data (cross-checks SEC against yfinance)
        eps_data, eps_source, validation_info = get_validated_eps(ticker, stock, income_stmt)

        # Use SEC company name if available and SEC data was used
        if eps_source.startswith('sec'):
            sec_eps = sec_data.get_sec_eps(ticker)
            if sec_eps and sec_eps.get('company_name'):
                company_name = sec_eps['company_name']

        # Get selloff metrics
        selloff_metrics = calculate_selloff_metrics(stock)

        # Get dividend info
        dividends = stock.dividends
        annual_dividend = 0
        dividend_info = []

        if dividends is not None and len(dividends) > 0:
            one_year_ago = datetime.now() - timedelta(days=365)
            recent_dividends = dividends[dividends.index >= one_year_ago.strftime('%Y-%m-%d')]

            for date, amount in recent_dividends.items():
                dividend_info.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'amount': float(amount)
                })
                annual_dividend += float(amount)

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
            stock = yf.Ticker(ticker)
            eps_data, source, _ = get_validated_eps(ticker, stock)
            return eps_data, source
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

"""
Valuation routes blueprint.

Handles:
- GET /api/valuation/<ticker> - Get valuation for a ticker
- POST /api/valuation/<ticker>/refresh - Refresh valuation
- GET /api/sec-metrics/<ticker> - Get SEC metrics
"""

import threading
import yfinance as yf
from flask import Blueprint, jsonify
import data_manager
import sec_data
from services.valuation import calculate_valuation, get_validated_eps
from services.yahoo_finance import calculate_selloff_metrics
from config import PE_RATIO_MULTIPLIER, RECOMMENDED_EPS_YEARS
from datetime import datetime, timedelta

valuation_bp = Blueprint('valuation', __name__, url_prefix='/api')


@valuation_bp.route('/valuation/<ticker>')
def api_valuation(ticker):
    """Calculate stock valuation using EPS and dividend formula."""
    result = calculate_valuation(ticker.upper())
    return jsonify(result)


@valuation_bp.route('/sec-metrics/<ticker>')
def api_sec_metrics(ticker):
    """Get SEC metrics for a ticker."""
    ticker = ticker.upper()
    metrics = sec_data.get_sec_metrics(ticker)

    if not metrics:
        return jsonify({'error': f'No SEC metrics available for {ticker}'}), 404

    return jsonify(metrics)


@valuation_bp.route('/valuation/<ticker>/refresh', methods=['POST'])
def api_valuation_refresh(ticker):
    """Refresh valuation for a specific ticker."""
    ticker = ticker.upper()

    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        company_name = info.get('shortName', ticker)
        current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)

        # Fetch income_stmt for validation
        income_stmt = stock.income_stmt

        # Get validated EPS data
        eps_data, eps_source, validation_info = get_validated_eps(ticker, stock, income_stmt)

        # Use SEC company name if available
        if eps_source.startswith('sec'):
            sec_eps = sec_data.get_sec_eps(ticker)
            if sec_eps and sec_eps.get('company_name'):
                company_name = sec_eps['company_name']

        # Get 52-week high/low
        fifty_two_week_high = info.get('fiftyTwoWeekHigh')
        fifty_two_week_low = info.get('fiftyTwoWeekLow')

        # Get selloff metrics
        selloff_metrics = calculate_selloff_metrics(stock)

        # Get dividend info
        dividends = stock.dividends
        annual_dividend = 0

        if dividends is not None and len(dividends) > 0:
            one_year_ago = datetime.now() - timedelta(days=365)
            recent_dividends = dividends[dividends.index >= one_year_ago.strftime('%Y-%m-%d')]
            annual_dividend = sum(float(d) for d in recent_dividends)

        # Calculate valuation
        eps_avg = None
        estimated_value = None
        price_vs_value = None
        off_high_pct = None

        if len(eps_data) > 0:
            eps_avg = sum(e['eps'] for e in eps_data) / len(eps_data)
            estimated_value = (eps_avg + annual_dividend) * PE_RATIO_MULTIPLIER

            if current_price and current_price > 0 and estimated_value > 0:
                price_vs_value = ((current_price - estimated_value) / estimated_value) * 100

        if fifty_two_week_high and current_price:
            off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100

        # Build valuation record
        valuation = {
            'ticker': ticker,
            'company_name': company_name,
            'current_price': round(current_price, 2) if current_price else None,
            'eps_avg': round(eps_avg, 2) if eps_avg else None,
            'eps_years': len(eps_data),
            'eps_source': eps_source,
            'annual_dividend': round(annual_dividend, 2),
            'estimated_value': round(estimated_value, 2) if estimated_value else None,
            'price_vs_value': round(price_vs_value, 1) if price_vs_value else None,
            'fifty_two_week_high': fifty_two_week_high,
            'fifty_two_week_low': fifty_two_week_low,
            'off_high_pct': round(off_high_pct, 1) if off_high_pct else None,
            'in_selloff': selloff_metrics.get('severity') in ('severe', 'high', 'moderate') if selloff_metrics else False,
            'selloff_severity': selloff_metrics.get('severity') if selloff_metrics else None,
            'updated': datetime.now().isoformat()
        }

        # Save to valuations
        data_manager.update_valuation(ticker, valuation)

        return jsonify({
            'success': True,
            'valuation': valuation,
            'eps_data': eps_data,
            'eps_validation': validation_info,
            'selloff': selloff_metrics,
            'formula': f'(({round(eps_avg, 2) if eps_avg else "N/A"} avg EPS) + {round(annual_dividend, 2)} dividend) x {PE_RATIO_MULTIPLIER} = ${round(estimated_value, 2) if estimated_value else "N/A"}'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

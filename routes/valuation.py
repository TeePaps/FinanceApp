"""
Valuation routes blueprint.

Handles:
- GET /api/valuation/<ticker> - Get valuation for a ticker
- POST /api/valuation/<ticker>/refresh - Refresh valuation
- GET /api/sec-metrics/<ticker> - Get SEC metrics
"""

import threading
from flask import Blueprint, jsonify
import data_manager
from services.valuation import calculate_valuation, get_validated_eps
from services.providers import get_orchestrator
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
    orchestrator = get_orchestrator()
    result = orchestrator.fetch_sec_metrics(ticker)

    if not result.success or not result.data:
        return jsonify({'error': f'No SEC metrics available for {ticker}'}), 404

    # Convert SECMetricsData to dict format for JSON response
    metrics = {
        'ticker': result.data.ticker,
        'company_name': result.data.company_name,
        'cik': result.data.cik,
        'eps_by_year': result.data.eps_matrix,
        'dividends': result.data.dividend_history
    }

    return jsonify(metrics)


@valuation_bp.route('/valuation/<ticker>/refresh', methods=['POST'])
def api_valuation_refresh(ticker):
    """Refresh valuation for a specific ticker."""
    ticker = ticker.upper()

    try:
        # Use provider system for price
        from services.activity_log import activity_log

        orchestrator = get_orchestrator()
        activity_log.log("info", "valuation", f"Refreshing {ticker}...")
        price_result = orchestrator.fetch_price(ticker, skip_cache=True)
        current_price = price_result.data if price_result.success else 0
        price_source = price_result.source if price_result.success else None

        # Get company info and 52-week high/low
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

        # Get validated EPS data using orchestrator
        eps_data, eps_source, validation_info = get_validated_eps(ticker)

        # Use SEC company name if available
        if eps_source.startswith('sec'):
            sec_result = orchestrator.fetch_eps(ticker)
            if sec_result.success and sec_result.data and sec_result.data.company_name:
                company_name = sec_result.data.company_name

        # Get dividend info using orchestrator
        dividend_result = orchestrator.fetch_dividends(ticker)
        annual_dividend = 0

        if dividend_result.success and dividend_result.data:
            annual_dividend = dividend_result.data.annual_dividend

        # Get selloff metrics via orchestrator
        selloff_metrics = None
        selloff_result = orchestrator.fetch_selloff(ticker)
        if selloff_result.success and selloff_result.data:
            sd = selloff_result.data
            selloff_metrics = {
                'day': sd.day, 'week': sd.week, 'month': sd.month,
                'avg_volume': sd.avg_volume, 'severity': sd.severity
            }

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
            'price_source': price_source,
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
        return jsonify({'error': str(e), 'ticker': ticker}), 500

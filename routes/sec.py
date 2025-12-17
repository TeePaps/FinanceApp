"""
SEC data routes blueprint.

Handles:
- GET /api/sec/status - SEC cache status
- POST /api/sec/update - Start SEC data update
- POST /api/sec/stop - Stop SEC update
- GET /api/sec/progress - Get SEC update progress
- GET /api/sec/eps/<ticker> - Get SEC EPS for ticker
- GET /api/sec/compare/<ticker> - Compare SEC vs yfinance EPS
"""

import yfinance as yf
from flask import Blueprint, jsonify, request
import sec_data
import data_manager
from config import VALID_INDICES

sec_bp = Blueprint('sec', __name__, url_prefix='/api')


@sec_bp.route('/sec/status')
def api_sec_status():
    """Get SEC cache status."""
    status = sec_data.get_cache_status()
    return jsonify(status)


@sec_bp.route('/sec/update', methods=['POST'])
def api_sec_update():
    """Start SEC data update for tickers."""
    req_data = request.get_json() or {}
    tickers = req_data.get('tickers')

    if not tickers:
        # If no tickers specified, use all tracked tickers
        tickers = list(data_manager.load_valuations().get('valuations', {}).keys())

    if not tickers:
        return jsonify({'error': 'No tickers to update'}), 400

    success = sec_data.start_background_update(tickers)
    if success:
        return jsonify({'status': 'started', 'ticker_count': len(tickers)})
    else:
        return jsonify({'error': 'Update already running'}), 400


@sec_bp.route('/sec/stop', methods=['POST'])
def api_sec_stop():
    """Stop SEC data update."""
    sec_data.stop_update()
    return jsonify({'status': 'stopped'})


@sec_bp.route('/sec/progress')
def api_sec_progress():
    """Get SEC update progress."""
    progress = sec_data.get_update_progress()
    return jsonify(progress)


@sec_bp.route('/sec/eps/<ticker>')
def api_sec_eps(ticker):
    """Get SEC EPS data for a ticker."""
    ticker = ticker.upper()
    eps_data = sec_data.get_sec_eps(ticker)

    if not eps_data:
        return jsonify({'error': f'No SEC EPS data for {ticker}'}), 404

    return jsonify(eps_data)


@sec_bp.route('/sec/compare/<ticker>')
def api_sec_compare(ticker):
    """Compare SEC vs yfinance EPS data."""
    ticker = ticker.upper()

    # Get SEC EPS
    sec_eps = sec_data.get_sec_eps(ticker)

    # Get yfinance EPS
    try:
        stock = yf.Ticker(ticker)
        income_stmt = stock.income_stmt

        yf_eps = {}
        if income_stmt is not None and not income_stmt.empty:
            eps_row = None
            if 'Diluted EPS' in income_stmt.index:
                eps_row = income_stmt.loc['Diluted EPS']
            elif 'Basic EPS' in income_stmt.index:
                eps_row = income_stmt.loc['Basic EPS']

            if eps_row is not None:
                import math
                for date, eps in eps_row.items():
                    if eps is not None and not (isinstance(eps, float) and math.isnan(eps)):
                        year = date.year if hasattr(date, 'year') else int(str(date)[:4])
                        yf_eps[int(year)] = float(eps)
    except Exception as e:
        yf_eps = {}

    # Build comparison
    comparison = []
    all_years = set()

    if sec_eps and sec_eps.get('eps_history'):
        for e in sec_eps['eps_history']:
            all_years.add(e['year'])

    all_years.update(yf_eps.keys())

    for year in sorted(all_years, reverse=True):
        sec_val = None
        if sec_eps and sec_eps.get('eps_history'):
            for e in sec_eps['eps_history']:
                if e['year'] == year:
                    sec_val = e['eps']
                    break

        yf_val = yf_eps.get(year)

        row = {
            'year': year,
            'sec_eps': sec_val,
            'yf_eps': yf_val,
            'match': sec_val is not None and yf_val is not None and abs(sec_val - yf_val) < 0.01,
            'diff': round(sec_val - yf_val, 2) if sec_val is not None and yf_val is not None else None
        }

        if sec_val is not None and yf_val is not None and yf_val != 0:
            row['diff_pct'] = round((row['diff'] / abs(yf_val)) * 100, 1)

        comparison.append(row)

    # Calculate match statistics
    years_with_both = sum(1 for c in comparison if c['sec_eps'] is not None and c['yf_eps'] is not None)
    matched_years = sum(1 for c in comparison if c.get('match'))

    return jsonify({
        'ticker': ticker,
        'company_name': sec_eps.get('company_name') if sec_eps else None,
        'comparison': comparison,
        'stats': {
            'sec_years': len([c for c in comparison if c['sec_eps'] is not None]),
            'yf_years': len([c for c in comparison if c['yf_eps'] is not None]),
            'years_with_both': years_with_both,
            'matched_years': matched_years,
            'match_rate': round(matched_years / years_with_both * 100, 1) if years_with_both > 0 else 0
        }
    })

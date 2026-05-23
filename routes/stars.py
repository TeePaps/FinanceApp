"""
Star Scoring System routes blueprint.

Endpoints:
- GET  /api/stars                  - All ratings split into holdings / watchlist
- POST /api/stars/recalculate      - Recompute star ratings on demand
                                      (Phase 5 also runs after the screener)
"""

import threading
from flask import Blueprint, jsonify

import database as db
from services.activity_log import activity_log


stars_bp = Blueprint('stars', __name__, url_prefix='/api')

_recalc_running = False
_recalc_lock = threading.Lock()


def _shape_row(row):
    """Normalize a star_ratings row for JSON response."""
    return {
        'ticker': row.get('ticker'),
        'company_name': row.get('company_name') or row.get('ticker'),
        'current_price': row.get('current_price'),
        'estimated_value': row.get('estimated_value'),
        'price_vs_value': row.get('price_vs_value'),
        'annual_dividend': row.get('annual_dividend'),
        'total_stars': row.get('total_stars', 0),
        'is_holding': bool(row.get('is_holding')),
        'criteria': {
            'earnings_beat': bool(row.get('earnings_beat')),
            'fair_value_up': bool(row.get('fair_value_up')),
            'dividend_up': bool(row.get('dividend_up')),
            'debt_to_capital_low': bool(row.get('debt_to_capital_low')),
            'shares_buyback': bool(row.get('shares_buyback')),
            'undervalued': bool(row.get('undervalued')),
        },
        'updated': row.get('updated'),
    }


@stars_bp.route('/stars', methods=['GET'])
def api_stars():
    """Return star ratings split into holdings (max 6) and watchlist (max 4)."""
    rows = db.get_star_ratings()
    holdings = []
    watchlist = []
    for row in rows:
        shaped = _shape_row(row)
        if shaped['is_holding']:
            holdings.append(shaped)
        else:
            watchlist.append(shaped)
    # get_star_ratings already orders by total_stars DESC, ticker ASC
    return jsonify({
        'success': True,
        'data': {
            'holdings': holdings,
            'watchlist': watchlist,
            'counts': {
                'holdings': len(holdings),
                'watchlist': len(watchlist),
            },
        }
    })


def _run_recalc():
    """Background worker for /api/stars/recalculate."""
    global _recalc_running
    try:
        from services.stars import calculate_all_star_ratings
        rated = calculate_all_star_ratings()
        activity_log.log("success", "stars", f"On-demand recalculation rated {rated} tickers")
    except Exception as e:
        activity_log.log("error", "stars", f"On-demand recalculation failed: {str(e)[:120]}")
    finally:
        with _recalc_lock:
            _recalc_running = False


@stars_bp.route('/stars/recalculate', methods=['POST'])
def api_stars_recalculate():
    """Kick off an on-demand star rating recalculation in the background."""
    global _recalc_running
    with _recalc_lock:
        if _recalc_running:
            return jsonify({'success': False, 'error': 'Recalculation already in progress'}), 409
        _recalc_running = True
    thread = threading.Thread(target=_run_recalc, daemon=True)
    thread.start()
    return jsonify({'success': True, 'message': 'Recalculation started'})


@stars_bp.route('/stars/status', methods=['GET'])
def api_stars_status():
    """Report whether an on-demand recalc is in progress."""
    return jsonify({'success': True, 'running': _recalc_running})


@stars_bp.route('/stars/<ticker>/explanation', methods=['GET'])
def api_stars_explanation(ticker):
    """Return the 6-criterion breakdown with underlying numbers for one ticker."""
    from services.stars import explain_stars
    try:
        data = explain_stars(ticker.upper())
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

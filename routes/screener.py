"""
Screener routes blueprint.

Handles:
- GET /api/screener - Get screener results
- POST /api/screener/start - Start full screener
- POST /api/screener/quick-update - Start quick price update
- POST /api/screener/smart-update - Start smart selective update
- POST /api/screener/stop - Stop screener
- GET /api/screener/progress - Get progress
- GET /api/indices - Get available indices
- POST /api/refresh - Global refresh
- GET /api/recommendations - Get stock recommendations
"""

import threading
from flask import Blueprint, jsonify, request, Response
import data_manager
from config import VALID_INDICES
from services.recommendations import get_top_recommendations
from services import screener as screener_service
from services.activity_log import activity_log

screener_bp = Blueprint('screener', __name__, url_prefix='/api')


@screener_bp.route('/indices')
def api_indices():
    """Get available indices with metadata."""
    from config import INDEX_DISPLAY_NAMES

    indices = []
    for idx in VALID_INDICES:
        if idx == 'all':
            continue
        display_names = INDEX_DISPLAY_NAMES.get(idx, (idx, idx))
        indices.append({
            'id': idx,
            'name': display_names[0],
            'short_name': display_names[1]
        })

    return jsonify(indices)


@screener_bp.route('/screener')
def api_screener():
    """Get cached screener results for specified index."""
    index_name = request.args.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    data = data_manager.get_index_data(index_name)

    # Filter to undervalued stocks (price_vs_value < -20)
    undervalued = []
    all_valuations = []

    for ticker, val in data.get('valuations', {}).items():
        all_valuations.append(val)
        if val.get('price_vs_value') is not None and val['price_vs_value'] < -20:
            undervalued.append(val)

    # Sort undervalued by most undervalued first
    undervalued.sort(key=lambda x: x['price_vs_value'])
    all_valuations.sort(key=lambda x: x.get('price_vs_value') or 999)

    total_tickers = len(data.get('tickers', []))
    valuations_count = len(data.get('valuations', {}))
    missing_count = total_tickers - valuations_count

    return jsonify({
        'index': index_name,
        'index_name': data.get('short_name', index_name),
        'last_updated': data.get('last_updated'),
        'total_tickers': total_tickers,
        'valuations_count': valuations_count,
        'missing_count': missing_count,
        'undervalued': undervalued,
        'all_valuations': all_valuations
    })


@screener_bp.route('/screener/start', methods=['POST'])
def api_screener_start():
    """Start full screener update."""
    if screener_service.is_running():
        return jsonify({'error': 'Screener already running'}), 400

    req_data = request.get_json() or {}
    index_name = req_data.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    thread = threading.Thread(target=screener_service.run_screener, args=(index_name,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'index': index_name})


@screener_bp.route('/screener/quick-update', methods=['POST'])
def api_screener_quick_update():
    """Start quick price-only update."""
    if screener_service.is_running():
        return jsonify({'error': 'Screener already running'}), 400

    req_data = request.get_json() or {}
    index_name = req_data.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    thread = threading.Thread(target=screener_service.run_quick_price_update, args=(index_name,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'index': index_name, 'mode': 'quick'})


@screener_bp.route('/screener/smart-update', methods=['POST'])
def api_screener_smart_update():
    """Start smart selective update."""
    if screener_service.is_running():
        return jsonify({'error': 'Screener already running'}), 400

    req_data = request.get_json() or {}
    index_name = req_data.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    thread = threading.Thread(target=screener_service.run_smart_update, args=(index_name,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'index': index_name, 'mode': 'smart'})


@screener_bp.route('/screener/stop', methods=['POST'])
def api_screener_stop():
    """Stop running screener."""
    screener_service.stop()
    return jsonify({'status': 'stopped'})


@screener_bp.route('/activity-stream')
def api_activity_stream():
    """Stream activity logs via Server-Sent Events."""
    def generate():
        for event in activity_log.subscribe():
            yield event

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'  # Disable nginx buffering
        }
    )


@screener_bp.route('/screener/progress')
def api_screener_progress():
    """Get screener progress."""
    return jsonify(screener_service.get_progress())


@screener_bp.route('/activity-logs')
def api_activity_logs():
    """Get recent activity logs."""
    count = request.args.get('count', 20, type=int)
    return jsonify(activity_log.get_recent(count))


@screener_bp.route('/refresh', methods=['POST'])
def api_global_refresh():
    """Start global refresh of all data."""
    if screener_service.is_running():
        return jsonify({'error': 'Refresh already running'}), 400

    thread = threading.Thread(target=screener_service.run_global_refresh)
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'mode': 'global'})


@screener_bp.route('/recommendations')
def api_recommendations():
    """Get top stock recommendations."""
    all_valuations = data_manager.load_valuations().get('valuations', {})

    if not all_valuations:
        return jsonify({'recommendations': [], 'error': 'No valuation data available'})

    # Get ticker-to-index mapping from data_manager
    ticker_indexes = data_manager.get_all_ticker_indexes()

    result = get_top_recommendations(all_valuations, ticker_indexes, limit=10, filter_by_index=True)
    return jsonify(result)


# =============================================================================
# Per-Ticker Refresh
# =============================================================================

@screener_bp.route('/ticker/<symbol>/refresh', methods=['POST'])
def api_refresh_ticker(symbol):
    """Refresh data for a single ticker."""
    from services.screener import refresh_single_ticker
    result = refresh_single_ticker(symbol.upper())
    return jsonify(result)


# =============================================================================
# Data Freshness / Staleness Dashboard
# =============================================================================

def get_freshness_status(age, data_type):
    """Determine freshness status based on age and data type."""
    import config

    if age is None:
        return 'stale'

    if data_type == 'price':
        if age < config.STALENESS_PRICE_FRESH_MINUTES:
            return 'fresh'
        elif age < config.STALENESS_PRICE_STALE_HOURS * 60:
            return 'aging'
        return 'stale'
    elif data_type == 'dividend':
        if age < config.STALENESS_DIVIDEND_FRESH_DAYS:
            return 'fresh'
        elif age < config.STALENESS_DIVIDEND_STALE_DAYS:
            return 'aging'
        return 'stale'

    return 'unknown'


@screener_bp.route('/data-freshness')
def api_data_freshness():
    """Get staleness info for dashboard."""
    import database as db
    from datetime import datetime

    # Get last update timestamps
    last_price = db.get_metadata('last_price_update')
    last_dividend = db.get_metadata('last_dividend_update')

    # Count EPS completeness
    total_tickers = db.count_tickers_in_enabled_indexes()
    tickers_with_eps = db.count_tickers_with_eps()

    def calc_age(timestamp, unit='minutes'):
        if not timestamp:
            return None
        try:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            # Make both datetimes naive for comparison
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            diff = datetime.now() - dt
            if unit == 'minutes':
                return int(diff.total_seconds() / 60)
            elif unit == 'hours':
                return int(diff.total_seconds() / 3600)
            elif unit == 'days':
                return diff.days
            return int(diff.total_seconds())
        except (ValueError, AttributeError):
            return None

    price_age = calc_age(last_price, 'minutes')
    dividend_age = calc_age(last_dividend, 'days')

    return jsonify({
        'prices': {
            'last_update': last_price,
            'age_minutes': price_age,
            'status': get_freshness_status(price_age, 'price')
        },
        'eps': {
            'total': total_tickers,
            'complete': tickers_with_eps,
            'missing': total_tickers - tickers_with_eps,
            'percent': round(tickers_with_eps / total_tickers * 100) if total_tickers > 0 else 0
        },
        'dividends': {
            'last_update': last_dividend,
            'age_days': dividend_age,
            'status': get_freshness_status(dividend_age, 'dividend')
        }
    })


# =============================================================================
# Scheduler Control
# =============================================================================

@screener_bp.route('/scheduler/status')
def api_scheduler_status():
    """Get auto-refresh scheduler status."""
    from services.scheduler import get_status
    return jsonify(get_status())


@screener_bp.route('/scheduler/toggle', methods=['POST'])
def api_scheduler_toggle():
    """Toggle auto-refresh scheduler."""
    from services.scheduler import toggle
    data = request.get_json() or {}
    result = toggle(data.get('enabled'))
    return jsonify(result)

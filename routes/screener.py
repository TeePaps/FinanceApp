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
from flask import Blueprint, jsonify, request
import data_manager
from config import VALID_INDICES
from services.recommendations import get_top_recommendations

# Import these from app.py for now - will be fully migrated later
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

screener_bp = Blueprint('screener', __name__, url_prefix='/api')

# Reference to app.py functions (will be migrated to services)
_app_module = None

def _get_app_module():
    """Lazy import of app module to avoid circular imports."""
    global _app_module
    if _app_module is None:
        import app as app_module
        _app_module = app_module
    return _app_module


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
    """Get screener results for an index."""
    index_name = request.args.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    # Get valuations for the index
    if index_name == 'all':
        valuations = data_manager.load_valuations().get('valuations', {})
    else:
        tickers = data_manager.get_index_tickers(index_name)
        all_valuations = data_manager.load_valuations().get('valuations', {})
        valuations = {t: all_valuations[t] for t in tickers if t in all_valuations}

    return jsonify({
        'index': index_name,
        'valuations': valuations,
        'count': len(valuations)
    })


@screener_bp.route('/screener/start', methods=['POST'])
def api_screener_start():
    """Start full screener update."""
    app = _get_app_module()

    if app.screener_running:
        return jsonify({'error': 'Screener already running'}), 400

    req_data = request.get_json() or {}
    index_name = req_data.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    thread = threading.Thread(target=app.run_screener, args=(index_name,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'index': index_name})


@screener_bp.route('/screener/quick-update', methods=['POST'])
def api_screener_quick_update():
    """Start quick price-only update."""
    app = _get_app_module()

    if app.screener_running:
        return jsonify({'error': 'Screener already running'}), 400

    req_data = request.get_json() or {}
    index_name = req_data.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    thread = threading.Thread(target=app.run_quick_price_update, args=(index_name,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'index': index_name, 'mode': 'quick'})


@screener_bp.route('/screener/smart-update', methods=['POST'])
def api_screener_smart_update():
    """Start smart selective update."""
    app = _get_app_module()

    if app.screener_running:
        return jsonify({'error': 'Screener already running'}), 400

    req_data = request.get_json() or {}
    index_name = req_data.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    thread = threading.Thread(target=app.run_smart_update, args=(index_name,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'index': index_name, 'mode': 'smart'})


@screener_bp.route('/screener/stop', methods=['POST'])
def api_screener_stop():
    """Stop running screener."""
    app = _get_app_module()
    app.screener_running = False
    return jsonify({'status': 'stopped'})


@screener_bp.route('/screener/progress')
def api_screener_progress():
    """Get screener progress."""
    app = _get_app_module()
    return jsonify(app.screener_progress)


@screener_bp.route('/refresh', methods=['POST'])
def api_global_refresh():
    """Start global refresh of all data."""
    app = _get_app_module()

    if app.screener_running:
        return jsonify({'error': 'Refresh already running'}), 400

    thread = threading.Thread(target=app.run_global_refresh)
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'mode': 'global'})


@screener_bp.route('/recommendations')
def api_recommendations():
    """Get top stock recommendations."""
    all_valuations = data_manager.load_valuations().get('valuations', {})

    if not all_valuations:
        return jsonify({'recommendations': [], 'error': 'No valuation data available'})

    # Get ticker-to-index mapping
    app = _get_app_module()
    ticker_indexes = app.get_all_ticker_indexes()

    result = get_top_recommendations(all_valuations, ticker_indexes, limit=10)
    return jsonify(result)

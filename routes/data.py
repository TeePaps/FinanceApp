"""
Data routes blueprint.

Handles:
- GET /api/data-status - Comprehensive data status
- GET /api/excluded-tickers - Get excluded tickers
- POST /api/excluded-tickers/clear - Clear excluded tickers
- GET /api/eps-recommendations - Get EPS update recommendations
- POST /api/screener/update-dividends - Update dividend data
"""

import os
import json
import threading
import time
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
import database as db
import data_manager
import sec_data
from config import (
    DATA_DIR, EXCLUDED_TICKERS_FILE, TICKER_FAILURES_FILE,
    FAILURE_THRESHOLD, VALID_INDICES
)
from services.providers import get_orchestrator

data_bp = Blueprint('data', __name__, url_prefix='/api')


def load_excluded_tickers():
    """Load excluded tickers from cache file."""
    if os.path.exists(EXCLUDED_TICKERS_FILE):
        try:
            with open(EXCLUDED_TICKERS_FILE, 'r') as f:
                data = json.load(f)
                return set(data.get('tickers', []))
        except Exception:
            pass
    return set()


def get_excluded_tickers_info():
    """Get info about excluded tickers."""
    result = {'tickers': [], 'count': 0, 'reason': 'none', 'updated': None, 'pending_failures': 0}
    if os.path.exists(EXCLUDED_TICKERS_FILE):
        try:
            with open(EXCLUDED_TICKERS_FILE, 'r') as f:
                data = json.load(f)
                result.update(data)
        except Exception:
            pass

    # Also report pending failures
    if os.path.exists(TICKER_FAILURES_FILE):
        try:
            with open(TICKER_FAILURES_FILE, 'r') as f:
                failures = json.load(f)
                pending = sum(1 for t, c in failures.items() if c < FAILURE_THRESHOLD)
                result['pending_failures'] = pending
        except Exception:
            pass

    return result


def clear_excluded_tickers():
    """Clear the excluded tickers list and failure counts."""
    if os.path.exists(EXCLUDED_TICKERS_FILE):
        os.remove(EXCLUDED_TICKERS_FILE)
    if os.path.exists(TICKER_FAILURES_FILE):
        os.remove(TICKER_FAILURES_FILE)


@data_bp.route('/data-status')
def api_data_status():
    """Get comprehensive data status report."""
    valuations_data = data_manager.load_valuations()
    all_valuations = valuations_data.get('valuations', {})
    last_updated = valuations_data.get('last_updated')

    # Get SEC cache status
    sec_status = sec_data.get_cache_status()

    # Get excluded tickers info
    excluded_info = get_excluded_tickers_info()

    # Calculate stats per index
    index_stats = {}
    for index_name in VALID_INDICES:
        if index_name == 'all':
            continue

        tickers = data_manager.get_index_tickers(index_name)
        if not tickers:
            continue

        total_tickers = len(tickers)
        valuations_count = sum(1 for t in tickers if t in all_valuations)
        with_eps = sum(1 for t in tickers if t in all_valuations and all_valuations[t].get('eps_avg'))
        with_sec = sum(1 for t in tickers if t in all_valuations and all_valuations[t].get('eps_source', '').startswith('sec'))

        index_stats[index_name] = {
            'total_tickers': total_tickers,
            'valuations_count': valuations_count,
            'coverage_pct': round((valuations_count / total_tickers * 100) if total_tickers > 0 else 0, 1),
            'with_eps': with_eps,
            'with_sec_data': with_sec
        }

    # Overall stats
    total_valuations = len(all_valuations)
    with_eps = sum(1 for v in all_valuations.values() if v.get('eps_avg'))
    with_sec = sum(1 for v in all_valuations.values() if v.get('eps_source', '').startswith('sec'))

    return jsonify({
        'last_updated': last_updated,
        'total_valuations': total_valuations,
        'with_eps': with_eps,
        'with_sec_data': with_sec,
        'sec_cache': sec_status,
        'excluded_tickers': excluded_info,
        'index_stats': index_stats
    })


@data_bp.route('/excluded-tickers')
def api_get_excluded_tickers():
    """Get excluded tickers info."""
    return jsonify(get_excluded_tickers_info())


@data_bp.route('/excluded-tickers/clear', methods=['POST'])
def api_clear_excluded_tickers():
    """Clear excluded tickers list."""
    clear_excluded_tickers()
    return jsonify({'success': True, 'message': 'Excluded tickers cleared'})


@data_bp.route('/eps-recommendations')
def api_eps_recommendations():
    """Get recommendations for which tickers need EPS updates."""
    recommendations = sec_data.get_eps_update_recommendations()
    return jsonify(recommendations)


@data_bp.route('/refresh-summary')
def api_refresh_summary():
    """Get summary of the last refresh operation."""
    try:
        summary_str = db.get_metadata('refresh_summary')
        if summary_str:
            return jsonify(json.loads(summary_str))
    except Exception:
        pass
    return jsonify({
        'last_refresh': None,
        'total_tickers': 0,
        'no_price_data': 0,
        'full_data': 0
    })


@data_bp.route('/screener/update-dividends', methods=['POST'])
def api_screener_update_dividends():
    """Quick update of just dividend data for cached stocks."""
    # Import app module for screener state
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    import app as app_module

    if app_module.screener_running:
        return jsonify({'error': 'Screener already running'}), 400

    req_data = request.get_json() or {}
    index_name = req_data.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    def update_dividends(idx):
        app_module.screener_running = True

        if idx == 'all':
            valuations = data_manager.load_valuations().get('valuations', {})
            tickers = list(valuations.keys())
        else:
            tickers = list(data_manager.get_index_tickers(idx) or [])

        app_module.screener_progress = {
            'current': 0, 'total': len(tickers),
            'ticker': '', 'status': 'running', 'index': idx
        }

        updates = {}
        for i, ticker in enumerate(tickers):
            if not app_module.screener_running:
                app_module.screener_progress['status'] = 'cancelled'
                break

            app_module.screener_progress['current'] = i + 1
            app_module.screener_progress['ticker'] = ticker

            try:
                orchestrator = get_orchestrator()
                result = orchestrator.fetch_dividends(ticker)

                if result.success and result.data:
                    dividend_data_obj = result.data
                    annual_dividend = dividend_data_obj.annual_dividend

                    # Get existing valuation to update
                    existing = data_manager.load_valuations().get('valuations', {}).get(ticker, {})
                    if existing:
                        eps_avg = existing.get('eps_avg', 0)
                        updates[ticker] = {
                            **existing,
                            'annual_dividend': round(annual_dividend, 2),
                            'estimated_value': round((eps_avg + annual_dividend) * 10, 2) if eps_avg else None,
                            'updated': datetime.now().isoformat()
                        }
            except Exception as e:
                print(f"Error updating dividend for {ticker}: {e}")

            time.sleep(0.3)

        if updates:
            data_manager.bulk_update_valuations(updates)

        app_module.screener_progress['status'] = 'complete'
        app_module.screener_running = False

    thread = threading.Thread(target=update_dividends, args=(index_name,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'index': index_name})

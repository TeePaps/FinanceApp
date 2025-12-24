"""
Data routes blueprint.

Handles:
- GET /api/data-status - Comprehensive data status
- GET /api/excluded-tickers - Get excluded tickers
- POST /api/excluded-tickers/clear - Clear excluded tickers
- GET /api/eps-recommendations - Get EPS update recommendations
- POST /api/screener/update-dividends - Update dividend data
"""

import json
import threading
import time
from datetime import datetime
from flask import Blueprint, jsonify, request
import database as db
import data_manager
from config import FAILURE_THRESHOLD, VALID_INDICES, DIVIDEND_FETCH_DELAY
from services.indexes import INDEX_NAMES
from services.providers import get_orchestrator
from services import screener as screener_service
from data_manager import get_index_data
from services.activity_log import activity_log

data_bp = Blueprint('data', __name__, url_prefix='/api')


def get_excluded_tickers_info():
    """Get info about excluded tickers from database."""
    excluded = db.get_excluded_tickers(threshold=FAILURE_THRESHOLD)

    # Count pending failures (tickers with some failures but not yet excluded)
    pending_count = db.get_ticker_failure_count(threshold=FAILURE_THRESHOLD)

    return {
        'tickers': excluded,
        'count': len(excluded),
        'pending_failures': pending_count
    }


def clear_excluded_tickers():
    """Clear the excluded tickers list and failure counts in database."""
    db.clear_ticker_failures()


@data_bp.route('/data-status')
def api_data_status():
    """Get comprehensive data status for all datasets."""
    # Get consolidated stats from data manager
    dm_stats = data_manager.get_data_stats()

    # SEC data status (from orchestrator for CIK info)
    orchestrator = get_orchestrator()
    sec_status = orchestrator.get_sec_cache_status()

    # Index data status - use consolidated data
    indices = []
    for index_name in VALID_INDICES:
        try:
            # Get tickers for this index from status
            index_tickers = data_manager.get_index_tickers(index_name)
            total_tickers = len(index_tickers) if index_tickers else 0

            # If no tickers in status, fall back to old index file
            if total_tickers == 0:
                data = get_index_data(index_name)
                total_tickers = len(data.get('tickers', []))
                index_tickers = data.get('tickers', [])

            # Get valuations from consolidated storage
            valuations = data_manager.get_valuations_for_index(index_name, index_tickers)
            valuations_count = len(valuations)

            # Count by EPS source
            sec_source_count = sum(1 for v in valuations if v.get('eps_source') == 'sec')
            yf_source_count = sum(1 for v in valuations if v.get('eps_source') == 'yfinance')

            # Average EPS years
            eps_years = [v.get('eps_years', 0) for v in valuations if v.get('eps_years')]
            avg_eps_years = sum(eps_years) / len(eps_years) if eps_years else 0

            # Get last updated from consolidated data
            last_updated = None
            if valuations:
                updates = [v.get('updated') for v in valuations if v.get('updated')]
                if updates:
                    last_updated = max(updates)

            indices.append({
                'id': index_name,
                'name': INDEX_NAMES.get(index_name, (index_name, index_name))[0],
                'short_name': INDEX_NAMES.get(index_name, (index_name, index_name))[1],
                'total_tickers': total_tickers,
                'valuations_count': valuations_count,
                'coverage_pct': round((valuations_count / total_tickers * 100) if total_tickers > 0 else 0, 1),
                'sec_source_count': sec_source_count,
                'yf_source_count': yf_source_count,
                'avg_eps_years': round(avg_eps_years, 1),
                'last_updated': last_updated
            })
        except Exception as e:
            print(f"[DataStatus] Error loading {index_name}: {e}")

    # Current refresh status
    refresh_status = {
        'running': screener_service.is_running(),
        'progress': screener_service.get_progress()
    }

    # Load refresh summary from database
    refresh_summary = None
    try:
        summary_str = db.get_metadata('refresh_summary')
        if summary_str:
            refresh_summary = json.loads(summary_str)
    except Exception:
        pass

    # Get excluded tickers info
    excluded_info = get_excluded_tickers_info()

    return jsonify({
        'sec': {
            'companies_cached': dm_stats['sec_available'],
            'sec_unavailable': dm_stats['sec_unavailable'],
            'sec_unknown': dm_stats['sec_unknown'],
            'cik_mappings': sec_status.get('cik_mapping', {}).get('count', 0),
            'cik_updated': sec_status.get('cik_mapping', {}).get('updated'),
            'last_full_update': dm_stats.get('status_last_updated')
        },
        'indices': indices,
        'consolidated': {
            'total_tickers': dm_stats['total_tickers'],
            'with_valuation': dm_stats['with_valuation'],
            'status_updated': dm_stats['status_last_updated'],
            'valuations_updated': dm_stats['valuations_last_updated']
        },
        'refresh': refresh_status,
        'refresh_summary': refresh_summary,
        'excluded_tickers': excluded_info
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
    orchestrator = get_orchestrator()
    recommendations = orchestrator.get_eps_update_recommendations()
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
    if screener_service.is_running():
        return jsonify({'error': 'Screener already running'}), 400

    req_data = request.get_json() or {}
    index_name = req_data.get('index', 'all')
    if index_name not in VALID_INDICES:
        index_name = 'all'

    def update_dividends(idx):
        # Use screener service state
        screener_service._running = True
        screener_service._progress.update({
            'current': 0, 'total': 0,
            'ticker': '', 'status': 'running',
            'phase': 'dividends', 'index': idx
        })

        if idx == 'all':
            valuations = data_manager.load_valuations().get('valuations', {})
            tickers = list(valuations.keys())
        else:
            tickers = list(data_manager.get_index_tickers(idx) or [])

        screener_service._progress['total'] = len(tickers)
        activity_log.log("info", "screener", f"Dividend Update: {len(tickers)} tickers")

        updates = {}
        for i, ticker in enumerate(tickers):
            if not screener_service._running:
                screener_service._progress['status'] = 'cancelled'
                break

            screener_service._progress['current'] = i + 1
            screener_service._progress['ticker'] = ticker

            try:
                orchestrator = get_orchestrator()
                result = orchestrator.fetch_dividends(ticker)

                if result.success and result.data:
                    dividend_data_obj = result.data
                    annual_dividend = dividend_data_obj.annual_dividend

                    existing = data_manager.load_valuations().get('valuations', {}).get(ticker, {})
                    if existing:
                        eps_avg = existing.get('eps_avg', 0)
                        updates[ticker] = {
                            **existing,
                            'annual_dividend': round(annual_dividend, 2),
                            'estimated_value': round((eps_avg + annual_dividend) * 10, 2) if eps_avg else None,
                            'updated': datetime.now().isoformat()
                        }
                        if (i + 1) % 50 == 0:
                            activity_log.log("info", "screener", f"Dividends: {i + 1}/{len(tickers)} processed...")
            except Exception as e:
                print(f"Error updating dividend for {ticker}: {e}")

            time.sleep(DIVIDEND_FETCH_DELAY)

        if updates:
            data_manager.bulk_update_valuations(updates)

        activity_log.log("success", "screener", f"✓ Dividend Update complete: {len(updates)} updated")
        screener_service._progress['status'] = 'complete'
        screener_service._running = False

    thread = threading.Thread(target=update_dividends, args=(index_name,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'index': index_name})

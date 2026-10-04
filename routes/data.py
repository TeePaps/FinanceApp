"""
Data routes blueprint.

Handles:
- GET /api/data-status - Comprehensive data status
- GET /api/ticker/<symbol> - Read-only per-ticker status (database only, never fetches)
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

    # Index data status - use consolidated data.
    # Load the valuations table ONCE and reuse it for every index; this loop
    # previously triggered a full table scan per index (6-9 per request).
    all_valuations = db.get_all_valuations()

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

            # Get valuations from consolidated storage (pre-loaded above)
            valuations = data_manager.get_valuations_for_index(
                index_name, index_tickers, all_valuations=all_valuations)
            valuations_count = len(valuations)

            # Count by EPS source
            # eps_source is stored as 'sec', 'sec_edgar' or 'sec_cache' - all SEC-derived
            eps_source_counts = {}
            for v in valuations:
                src = v.get('eps_source') or 'none'
                eps_source_counts[src] = eps_source_counts.get(src, 0) + 1
            sec_source_count = sum(n for src, n in eps_source_counts.items()
                                   if src.startswith('sec'))
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
                'eps_source_counts': eps_source_counts,
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

        # Load the valuations table ONCE for the whole run. The per-ticker
        # lookup below used to call load_valuations() inside the loop, so an
        # N-ticker update read the entire table N times (O(N^2) row reads).
        all_valuations = data_manager.load_valuations().get('valuations', {})

        if idx == 'all':
            tickers = list(all_valuations.keys())
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

                    existing = all_valuations.get(ticker, {})
                    if existing:
                        # Use the canonical helper so the sanity rules and the
                        # configured multiplier apply, and recompute
                        # price_vs_value so it stays consistent with the new
                        # estimated_value instead of carrying the stale one.
                        from services.valuation import compute_estimated_value
                        estimated_value, price_vs_value = compute_estimated_value(
                            existing.get('eps_avg'), annual_dividend,
                            existing.get('current_price')
                        )
                        updates[ticker] = {
                            **existing,
                            'annual_dividend': round(annual_dividend, 2),
                            'estimated_value': estimated_value,
                            'price_vs_value': price_vs_value,
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


def _age_minutes(timestamp):
    """Whole minutes since an ISO timestamp, or None if absent/unparseable."""
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(str(timestamp).replace('Z', '+00:00'))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return max(0, int((datetime.now() - dt).total_seconds() / 60))
    except (ValueError, TypeError):
        return None


@data_bp.route('/ticker/<symbol>')
def api_ticker_status(symbol):
    """Read-only per-ticker status. Database reads only - never calls a provider."""
    ticker = symbol.upper()
    val = db.get_valuation(ticker)
    info = db.get_ticker_info(ticker)
    if not val and not info:
        return jsonify({'success': False, 'error': f'Unknown ticker: {ticker}'}), 404

    val = val or {}
    info = info or {}
    sec = db.get_sec_company(ticker) or {}
    failure = db.get_ticker_failure(ticker) or {}

    price_updated = val.get('price_updated') or val.get('updated')
    return jsonify({
        'success': True,
        'ticker': ticker,
        'company_name': val.get('company_name') or info.get('company_name'),
        'indexes': info.get('indexes', []),
        'enabled': bool(info['enabled']) if info.get('enabled') is not None else None,
        'delisted': bool(info['delisted']) if info.get('delisted') is not None else None,
        'price': {
            'value': val.get('current_price'),
            'source': val.get('price_source'),
            'updated': price_updated,
            'age_minutes': _age_minutes(price_updated),
        },
        'eps': {
            'avg': val.get('eps_avg'),
            'years': val.get('eps_years'),
            'source': val.get('eps_source'),
            'updated': sec.get('updated'),
            'sec_status': info.get('sec_status'),
        },
        'dividend': {
            'annual': val.get('annual_dividend'),
            'updated': val.get('dividend_updated'),
        },
        'valuation': {
            'estimated_value': val.get('estimated_value'),
            'price_vs_value': val.get('price_vs_value'),
            'updated': val.get('updated'),
            'fifty_two_week_high': val.get('fifty_two_week_high'),
            'fifty_two_week_low': val.get('fifty_two_week_low'),
            'off_high_pct': val.get('off_high_pct'),
            'price_change_1m': val.get('price_change_1m'),
            'price_change_3m': val.get('price_change_3m'),
            'in_selloff': val.get('in_selloff'),
            'selloff_severity': val.get('selloff_severity'),
        },
        'fetch_checks': db.get_ticker_fetch_checks(ticker),
        'failures': {
            'count': failure.get('failure_count', 0),
            'last_failure': failure.get('last_failure'),
            'reason': failure.get('reason'),
            'delist_strikes': info.get('delist_strikes') or 0,
        },
    })

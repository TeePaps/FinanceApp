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

from flask import Blueprint, jsonify, request
import data_manager
from config import VALID_INDICES
from services.providers import get_orchestrator

sec_bp = Blueprint('sec', __name__, url_prefix='/api')


@sec_bp.route('/sec/status')
def api_sec_status():
    """Get SEC data cache status."""
    orchestrator = get_orchestrator()
    status = orchestrator.get_sec_cache_status()
    progress = orchestrator.get_sec_update_progress()
    return jsonify({
        'cache': status,
        'update': progress
    })


@sec_bp.route('/sec/eps-frames', methods=['POST'])
def api_sec_eps_frames():
    """Bulk-fill missing annual EPS using the SEC XBRL frames API.

    One request per concept per year covers every filer, replacing a
    per-company companyfacts crawl. Gap-filling only - years already stored
    from per-company data are left untouched.

    Body (all optional): {"tickers": [...], "years": 8}
    """
    import sec_data

    req_data = request.get_json(silent=True) or {}
    tickers = req_data.get('tickers')
    years = req_data.get('years')

    if not tickers:
        tickers = list(data_manager.load_valuations().get('valuations', {}).keys())
    if not tickers:
        return jsonify({'success': False, 'error': 'No tickers to update'}), 400

    stats = sec_data.refresh_eps_from_frames(tickers=tickers, years=years)
    return jsonify({'success': True, **stats})


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

    orchestrator = get_orchestrator()
    success = orchestrator.start_sec_background_update(tickers)
    if success:
        return jsonify({'status': 'started', 'ticker_count': len(tickers)})
    else:
        return jsonify({'error': 'Update already running'}), 400


@sec_bp.route('/sec/stop', methods=['POST'])
def api_sec_stop():
    """Stop SEC data update."""
    orchestrator = get_orchestrator()
    orchestrator.stop_sec_background_update()
    return jsonify({'status': 'stopped'})


@sec_bp.route('/sec/progress')
def api_sec_progress():
    """Get SEC update progress."""
    orchestrator = get_orchestrator()
    progress = orchestrator.get_sec_update_progress()
    return jsonify(progress)


@sec_bp.route('/sec/eps/<ticker>')
def api_sec_eps(ticker):
    """Get SEC EPS data for a ticker."""
    ticker = ticker.upper()
    orchestrator = get_orchestrator()
    result = orchestrator.fetch_eps(ticker)

    if not result.success or not result.data:
        return jsonify({'error': f'No SEC EPS data for {ticker}'}), 404

    # Convert EPSData to dict format
    eps_data = {
        'ticker': result.data.ticker,
        'company_name': result.data.company_name,
        'eps_history': result.data.eps_history
    }

    return jsonify(eps_data)


@sec_bp.route('/sec/compare/<ticker>')
def api_sec_compare(ticker):
    """Compare SEC vs yfinance EPS data."""
    ticker = ticker.upper()
    orchestrator = get_orchestrator()

    def _provider_of(cls):
        """Find a registered EPS provider of a specific class."""
        from services.providers.base import DataType
        candidates = orchestrator.registry._by_type.get(DataType.EPS, [])
        return next((p for p in candidates if isinstance(p, cls)), None)

    # Fetch each side from its OWN provider — the old code called
    # orchestrator.fetch_eps(ticker) for BOTH sides, which returns the same
    # cached fallback-chain result, so every row reported sec_eps == yf_eps
    # and a 100% match regardless of reality.
    from services.providers.sec_provider import SECEPSProvider
    from services.providers.yfinance_provider import YFinanceEPSProvider

    sec_eps = None
    sec_provider = _provider_of(SECEPSProvider)
    if sec_provider:
        sec_result = sec_provider.fetch_eps(ticker)
        if sec_result.success and sec_result.data:
            sec_eps = {
                'company_name': sec_result.data.company_name,
                'eps_history': sec_result.data.eps_history
            }

    # yfinance side from the yfinance provider directly
    yf_eps = {}
    yf_provider = _provider_of(YFinanceEPSProvider)
    if yf_provider:
        try:
            result = yf_provider.fetch_eps(ticker)
            if result.success and result.data:
                for entry in result.data.eps_history:
                    if 'eps' in entry and entry['eps'] is not None:
                        yf_eps[int(entry['year'])] = float(entry['eps'])
        except Exception:
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

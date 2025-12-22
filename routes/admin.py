"""
Admin routes blueprint.

Handles:
- POST /api/admin/backfill-company-names - Backfill company names in valuations table
"""

from flask import Blueprint, request, jsonify
from services.providers import get_orchestrator
import sec_data
import database as db

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')


@admin_bp.route('/backfill-company-names', methods=['POST'])
def api_backfill_company_names():
    """
    Backfill company names for tickers where company_name equals ticker.

    Request body (optional):
    {
        "limit": 100,          # Number of records to process (default: 100)
        "index_name": "all"    # Index to filter by (default: "all")
    }

    Returns:
    {
        "success": true,
        "updated": 5,          # Number of records updated
        "failed": 2,           # Number of records that failed
        "skipped": 3,          # Number of records skipped (already have proper names)
        "details": [...]       # List of updates made
    }
    """
    req_data = request.get_json() or {}
    limit = req_data.get('limit', 100)
    index_name = req_data.get('index_name', 'all')

    # Validate limit
    try:
        limit = int(limit)
        if limit <= 0:
            return jsonify({'success': False, 'error': 'limit must be positive'}), 400
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'limit must be a number'}), 400

    # Query valuations where company_name equals ticker OR is NULL
    try:
        with db.get_public_db() as conn:
            cursor = conn.cursor()

            if index_name == 'all':
                # Get all valuations where company_name = ticker or is NULL
                cursor.execute('''
                    SELECT ticker, company_name
                    FROM valuations
                    WHERE company_name = ticker OR company_name IS NULL
                    LIMIT ?
                ''', (limit,))
            else:
                # Get valuations for specific index where company_name = ticker or is NULL
                cursor.execute('''
                    SELECT v.ticker, v.company_name
                    FROM valuations v
                    JOIN ticker_indexes ti ON v.ticker = ti.ticker
                    WHERE (v.company_name = v.ticker OR v.company_name IS NULL)
                    AND ti.index_name = ?
                    LIMIT ?
                ''', (index_name, limit))

            rows = cursor.fetchall()
    except Exception as e:
        return jsonify({'success': False, 'error': f'Database query failed: {str(e)}'}), 500

    if not rows:
        return jsonify({
            'success': True,
            'updated': 0,
            'failed': 0,
            'skipped': 0,
            'message': 'No records found where company_name equals ticker'
        })

    # Process each ticker
    orchestrator = get_orchestrator()
    updated = 0
    failed = 0
    details = []

    for row in rows:
        ticker = row['ticker']
        old_name = row['company_name']
        company_name = None
        source = None

        # Try yfinance first via orchestrator
        try:
            result = orchestrator.fetch_stock_info(ticker)
            if result.success and result.data:
                company_name = result.data.company_name
                source = 'yfinance'
        except Exception as e:
            print(f"[Backfill] yfinance failed for {ticker}: {e}")

        # If yfinance failed, try SEC data
        if not company_name or company_name == ticker:
            try:
                sec_result = sec_data.get_sec_eps(ticker)
                if sec_result and sec_result.get('company_name'):
                    company_name = sec_result['company_name']
                    source = 'sec'
            except Exception as e:
                print(f"[Backfill] SEC failed for {ticker}: {e}")

        # Update database if we found a different company name
        if company_name and company_name != ticker:
            try:
                # Update just the company_name field
                with db.get_public_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE valuations
                        SET company_name = ?
                        WHERE ticker = ?
                    ''', (company_name, ticker))

                updated += 1
                details.append({
                    'ticker': ticker,
                    'old_name': old_name,
                    'new_name': company_name,
                    'source': source
                })
                print(f"[Backfill] Updated {ticker}: {old_name} -> {company_name} (source: {source})")
            except Exception as e:
                failed += 1
                details.append({
                    'ticker': ticker,
                    'error': str(e)
                })
                print(f"[Backfill] Failed to update {ticker}: {e}")
        else:
            failed += 1
            details.append({
                'ticker': ticker,
                'error': 'Could not fetch company name from any source'
            })
            print(f"[Backfill] Failed to find company name for {ticker}")

    return jsonify({
        'success': True,
        'updated': updated,
        'failed': failed,
        'skipped': 0,  # Not tracking skips in this implementation
        'total_processed': len(rows),
        'details': details
    })

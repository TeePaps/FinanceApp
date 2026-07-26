"""
Summary routes blueprint.

Handles:
- GET /api/summary - Portfolio summary
- GET /api/prices - Current prices for holdings
- GET /api/profit-timeline - Profit timeline
- GET /api/performance - Historical performance
"""

from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
import data_manager
import database as db
from services.holdings import (
    calculate_holdings, calculate_fifo_cost_basis, get_transactions, get_stocks,
)
from services.stock_utils import fetch_multiple_prices
from config import PRICE_CACHE_DURATION

summary_bp = Blueprint('summary', __name__, url_prefix='/api')


@summary_bp.route('/prices')
def api_prices():
    """Fetch current prices for confirmed holdings only."""
    holdings = calculate_holdings(confirmed_only=True)
    tickers = [t for t, h in holdings.items() if h['shares'] > 0]
    prices = fetch_multiple_prices(tickers)

    # Cached valuations for the `updated` timestamps. Keyed to the held
    # tickers - this used to load the entire valuations table (~1,500 rows) to
    # read one field for a handful of holdings.
    all_valuations = db.get_valuations_for_tickers(tickers)

    # Calculate unrealized gains
    results = {}
    total_value = 0
    total_cost = 0
    total_gain = 0

    for ticker in tickers:
        if ticker in prices:
            holding = holdings[ticker]
            current_price = prices[ticker]
            current_value = current_price * holding['shares']
            cost_basis = holding['total_cost']
            unrealized_gain = current_value - cost_basis
            unrealized_pct = (unrealized_gain / cost_basis * 100) if cost_basis > 0 else 0

            total_value += current_value
            total_cost += cost_basis
            total_gain += unrealized_gain

            val = all_valuations.get(ticker, {})

            results[ticker] = {
                'price': round(current_price, 2),
                'name': holding['name'],
                'shares': holding['shares'],
                'current_value': round(current_value, 2),
                'cost_basis': round(cost_basis, 2),
                'unrealized_gain': round(unrealized_gain, 2),
                'unrealized_pct': round(unrealized_pct, 1),
                'updated': val.get('updated')
            }

    total_pct = (total_gain / total_cost * 100) if total_cost > 0 else 0

    return jsonify({
        'prices': results,
        'totals': {
            'current_value': round(total_value, 2),
            'cost_basis': round(total_cost, 2),
            'unrealized_gain': round(total_gain, 2),
            'unrealized_pct': round(total_pct, 1)
        },
        'cache_duration': PRICE_CACHE_DURATION
    })


# NOTE: /api/summary is handled by app.py for full compatibility with frontend
# The blueprint version was removed due to response format differences


def _parse_date(date_str):
    """Parse YYYY-MM-DD to a date; None for missing/unparseable."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return None


@summary_bp.route('/profit-timeline')
def api_profit_timeline():
    """
    Realized profit (FIFO) within a date range.

    Response shape must match renderProfitTimeline() in static/app.js:
    {date_range: {start, end}, totals: {profit, revenue, sales_count},
     by_ticker, by_month, sales}. The previous blueprint version returned a
    different shape ({timeline, totals.gain}), which crashed the tab.
    """
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    start = _parse_date(start_date)
    end = _parse_date(end_date)

    stocks = {s['ticker']: s for s in get_stocks()}
    transactions = get_transactions()

    # FIFO basis for every sell. All sells must be processed in order
    # (basis depends on earlier sells), so compute first and filter the
    # reporting window afterwards. Buys count only when confirmed (done).
    txns_by_ticker = {}
    for txn in transactions:
        status = (txn.get('status') or '').lower()
        if txn['action'] == 'buy' and status != 'done':
            continue
        txns_by_ticker.setdefault(txn['ticker'], []).append(txn)

    sell_basis = {}
    for ticker, txns in txns_by_ticker.items():
        basis, _lots = calculate_fifo_cost_basis(ticker, txns)
        sell_basis.update(basis)

    sales_in_range = []
    total_profit = 0
    total_revenue = 0
    by_ticker = {}
    by_month = {}

    for txn in transactions:
        if txn['action'] != 'sell':
            continue
        if (txn.get('status') or '').lower() != 'done':
            continue

        txn_date = _parse_date(txn.get('date'))
        if txn_date:
            if start and txn_date < start:
                continue
            if end and txn_date > end:
                continue
        elif start or end:
            # An undated sell can't be placed inside a date window
            continue
        # Undated done sells stay in the unfiltered totals so this endpoint
        # agrees with /api/performance (which has no date concept); they
        # just can't be bucketed by month below.

        ticker = txn['ticker']
        shares = int(txn['shares']) if txn['shares'] else 0
        price = float(txn['price']) if txn['price'] else 0
        revenue = shares * price
        cost = sell_basis.get(txn['id'], {}).get('cost_basis', 0)
        profit = revenue - cost

        total_profit += profit
        total_revenue += revenue

        if ticker not in by_ticker:
            by_ticker[ticker] = {
                'ticker': ticker,
                'name': stocks.get(ticker, {}).get('name', ticker),
                'shares_sold': 0,
                'revenue': 0,
                'profit': 0,
                'sales': []
            }
        by_ticker[ticker]['shares_sold'] += shares
        by_ticker[ticker]['revenue'] += revenue
        by_ticker[ticker]['profit'] += profit
        by_ticker[ticker]['sales'].append({
            'date': txn['date'],
            'shares': shares,
            'price': price,
            'revenue': revenue,
            'profit': round(profit, 2)
        })

        if txn_date:
            month_key = txn_date.strftime('%Y-%m')
            if month_key not in by_month:
                by_month[month_key] = {'month': month_key, 'profit': 0, 'revenue': 0, 'sales_count': 0}
            by_month[month_key]['profit'] += profit
            by_month[month_key]['revenue'] += revenue
            by_month[month_key]['sales_count'] += 1

        sales_in_range.append({
            'date': txn['date'],
            'ticker': ticker,
            'shares': shares,
            'price': price,
            'profit': round(profit, 2)
        })

    sales_in_range.sort(key=lambda x: x['date'] or '')
    by_ticker_list = sorted(by_ticker.values(), key=lambda x: x['profit'], reverse=True)
    for t in by_ticker_list:
        t['revenue'] = round(t['revenue'], 2)
        t['profit'] = round(t['profit'], 2)

    by_month_list = sorted(by_month.values(), key=lambda x: x['month'])
    for m in by_month_list:
        m['profit'] = round(m['profit'], 2)
        m['revenue'] = round(m['revenue'], 2)

    return jsonify({
        'date_range': {
            'start': start_date or 'all time',
            'end': end_date or 'now'
        },
        'totals': {
            'profit': round(total_profit, 2),
            'revenue': round(total_revenue, 2),
            'sales_count': len(sales_in_range)
        },
        'by_ticker': by_ticker_list,
        'by_month': by_month_list,
        'sales': sales_in_range
    })


@summary_bp.route('/performance')
def api_performance():
    """Get historical performance metrics."""
    transactions = get_transactions()
    holdings = calculate_holdings(confirmed_only=True)
    valuations_data = data_manager.load_valuations()
    all_valuations = valuations_data.get('valuations', {})

    # Realized gains from completed sells, FIFO. (Back-computing cost from the
    # stored gain_pct loses precision — gain_pct is rounded to whole percents —
    # and disagreed with /api/summary and /api/profit-timeline by ~$150.)
    txns_by_ticker = {}
    for txn in transactions:
        status = (txn.get('status') or '').lower()
        if txn['action'] == 'buy' and status != 'done':
            continue
        txns_by_ticker.setdefault(txn['ticker'], []).append(txn)

    sell_basis = {}
    for ticker, txns in txns_by_ticker.items():
        basis, _lots = calculate_fifo_cost_basis(ticker, txns)
        sell_basis.update(basis)

    realized_gain = 0
    for txn in transactions:
        if txn['action'] != 'sell':
            continue
        if (txn.get('status') or '').lower() != 'done':
            continue
        shares = int(txn['shares']) if txn['shares'] else 0
        price = float(txn['price']) if txn['price'] else 0
        cost = sell_basis.get(txn['id'], {}).get('cost_basis', 0)
        realized_gain += shares * price - cost

    # Calculate unrealized gains
    unrealized_gain = 0
    total_invested = 0

    for ticker, holding in holdings.items():
        if holding['shares'] <= 0:
            continue

        val = all_valuations.get(ticker, {})
        current_price = val.get('current_price', 0)
        if current_price:
            current_value = current_price * holding['shares']
            cost_basis = holding['total_cost']
            unrealized_gain += current_value - cost_basis
            total_invested += cost_basis

    total_gain = realized_gain + unrealized_gain

    return jsonify({
        'realized_gain': round(realized_gain, 2),
        'unrealized_gain': round(unrealized_gain, 2),
        'total_gain': round(total_gain, 2),
        'total_invested': round(total_invested, 2),
        'total_return_pct': round((total_gain / total_invested * 100), 1) if total_invested > 0 else 0
    })

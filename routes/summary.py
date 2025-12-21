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
from services.holdings import calculate_holdings, get_transactions
from services.stock_utils import fetch_multiple_prices
from config import PRICE_CACHE_DURATION

summary_bp = Blueprint('summary', __name__, url_prefix='/api')


@summary_bp.route('/prices')
def api_prices():
    """Fetch current prices for confirmed holdings only."""
    holdings = calculate_holdings(confirmed_only=True)
    tickers = [t for t, h in holdings.items() if h['shares'] > 0]
    prices = fetch_multiple_prices(tickers)

    # Get cached valuations for updated timestamps
    all_valuations = data_manager.load_valuations().get('valuations', {})

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


@summary_bp.route('/profit-timeline')
def api_profit_timeline():
    """Get timeline of realized profits."""
    transactions = get_transactions()

    # Get date range from query params
    start_date = request.args.get('start')
    end_date = request.args.get('end')

    # Filter to completed sells
    sells = [t for t in transactions if t['action'] == 'sell' and t.get('gain_pct')]

    # Apply date filter if provided
    if start_date:
        sells = [t for t in sells if t.get('date', '') >= start_date]
    if end_date:
        sells = [t for t in sells if t.get('date', '') <= end_date]

    # Sort by date
    sells.sort(key=lambda x: x.get('date', ''))

    # Calculate monthly totals
    monthly_totals = {}
    for txn in sells:
        date = txn.get('date', '')
        if len(date) >= 7:
            month = date[:7]  # YYYY-MM
            if month not in monthly_totals:
                monthly_totals[month] = {'gain': 0, 'count': 0}

            shares = int(txn.get('shares', 0))
            price = float(txn.get('price', 0))
            gain_pct = float(txn.get('gain_pct', 0))

            # Estimate gain (price * shares * gain_pct / (100 + gain_pct))
            total_sale = shares * price
            cost_basis = total_sale / (1 + gain_pct / 100)
            gain = total_sale - cost_basis

            monthly_totals[month]['gain'] += gain
            monthly_totals[month]['count'] += 1

    # Convert to sorted list
    timeline = [
        {'month': k, 'gain': round(v['gain'], 2), 'transactions': v['count']}
        for k, v in sorted(monthly_totals.items())
    ]

    # Calculate totals
    total_gain = sum(m['gain'] for m in timeline)
    total_transactions = sum(m['transactions'] for m in timeline)

    return jsonify({
        'timeline': timeline,
        'totals': {
            'gain': round(total_gain, 2),
            'transactions': total_transactions
        },
        'transactions': sells
    })


@summary_bp.route('/performance')
def api_performance():
    """Get historical performance metrics."""
    transactions = get_transactions()
    holdings = calculate_holdings(confirmed_only=True)
    valuations_data = data_manager.load_valuations()
    all_valuations = valuations_data.get('valuations', {})

    # Calculate realized gains from completed sells
    realized_gain = 0
    for txn in transactions:
        if txn['action'] == 'sell' and txn.get('gain_pct'):
            shares = int(txn.get('shares', 0))
            price = float(txn.get('price', 0))
            gain_pct = float(txn.get('gain_pct', 0))

            total_sale = shares * price
            cost_basis = total_sale / (1 + gain_pct / 100)
            realized_gain += total_sale - cost_basis

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

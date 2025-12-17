"""
Holdings routes blueprint.

Handles:
- GET /api/holdings - Get all holdings
- GET /api/holdings-analysis - Get holdings with analysis and sell recommendations
"""

from flask import Blueprint, jsonify
import data_manager
from services.holdings import calculate_holdings

holdings_bp = Blueprint('holdings', __name__, url_prefix='/api')


@holdings_bp.route('/holdings')
def api_holdings():
    """Get holdings separated by confirmed vs pending."""
    holdings = calculate_holdings()

    def has_confirmed_shares(holding):
        """Check if holding has any confirmed (done) buy transactions"""
        for txn in holding['transactions']:
            if txn['action'] == 'buy':
                status = (txn.get('status') or '').lower()
                if status == 'done':
                    return True
        return False

    # Separate into confirmed holdings vs pending/watchlist
    confirmed = {}
    pending = {}

    for ticker, holding in holdings.items():
        if has_confirmed_shares(holding):
            confirmed[ticker] = holding
        else:
            pending[ticker] = holding

    # Separate stocks and index funds for confirmed holdings
    stocks = {k: v for k, v in confirmed.items() if v['type'] == 'stock'}
    index_funds = {k: v for k, v in confirmed.items() if v['type'] == 'index'}

    # Separate pending by type as well
    pending_stocks = {k: v for k, v in pending.items() if v['type'] == 'stock'}
    pending_index = {k: v for k, v in pending.items() if v['type'] == 'index'}

    return jsonify({
        'stocks': stocks,
        'index_funds': index_funds,
        'pending_stocks': pending_stocks,
        'pending_index': pending_index
    })


@holdings_bp.route('/holdings-analysis')
def api_holdings_analysis():
    """Get holdings with current prices, valuations, and sell recommendations."""
    holdings = calculate_holdings()
    valuations_data = data_manager.load_valuations()
    all_valuations = valuations_data.get('valuations', {})
    last_updated = valuations_data.get('last_updated')

    # Enrich holdings with current price and valuation data
    enriched_holdings = {}
    sell_candidates = []

    for ticker, holding in holdings.items():
        # Only process confirmed holdings (with done buy transactions)
        has_confirmed = any(
            txn['action'] == 'buy' and (txn.get('status') or '').lower() == 'done'
            for txn in holding['transactions']
        )
        if not has_confirmed:
            continue

        val = all_valuations.get(ticker, {})
        current_price = val.get('current_price')
        price_vs_value = val.get('price_vs_value')
        estimated_value = val.get('estimated_value')

        # Calculate average cost basis from remaining lots
        avg_cost = None
        total_cost = 0
        total_shares = 0
        if holding.get('remaining_lots'):
            for lot in holding['remaining_lots']:
                total_cost += lot['shares'] * lot['price']
                total_shares += lot['shares']
            if total_shares > 0:
                avg_cost = total_cost / total_shares

        # Calculate gain percentage if we have current price and cost basis
        gain_pct = None
        if current_price and avg_cost and avg_cost > 0:
            gain_pct = ((current_price - avg_cost) / avg_cost) * 100

        enriched = {
            **holding,
            'current_price': current_price,
            'estimated_value': estimated_value,
            'price_vs_value': price_vs_value,
            'avg_cost': round(avg_cost, 2) if avg_cost else None,
            'gain_pct': round(gain_pct, 1) if gain_pct else None,
            'annual_dividend': val.get('annual_dividend', 0),
            'dividend_yield': round((val.get('annual_dividend', 0) / current_price * 100), 2) if current_price and current_price > 0 else 0,
            'updated': val.get('updated')
        }
        enriched_holdings[ticker] = enriched

        # Check if this is a sell candidate
        is_overvalued = price_vs_value is not None and price_vs_value > 10
        has_big_gain = gain_pct is not None and gain_pct > 30

        if is_overvalued or has_big_gain:
            reasons = []
            if is_overvalued:
                reasons.append(f"Trading {price_vs_value:.0f}% above estimated value")
            if has_big_gain:
                reasons.append(f"Up {gain_pct:.0f}% from your cost basis of ${avg_cost:.2f}")

            sell_candidates.append({
                'ticker': ticker,
                'name': holding.get('name', ticker),
                'shares': holding.get('shares', 0),
                'current_price': current_price,
                'avg_cost': avg_cost,
                'gain_pct': gain_pct,
                'price_vs_value': price_vs_value,
                'estimated_value': estimated_value,
                'reasons': reasons,
                'priority': (price_vs_value or 0) + (gain_pct or 0) / 2
            })

    # Sort sell candidates by priority
    sell_candidates.sort(key=lambda x: x['priority'], reverse=True)

    return jsonify({
        'holdings': enriched_holdings,
        'sell_recommendations': sell_candidates[:5],
        'last_updated': last_updated
    })

"""
Holdings calculations service.

Provides:
- FIFO cost basis calculation
- Holdings aggregation from transactions
- Sell candidate identification
"""

import csv
import os
from config import DATA_DIR, STOCKS_FILE, TRANSACTIONS_FILE


def read_csv(filename):
    """Read CSV file from data directory."""
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        return list(reader)


def get_stocks():
    """Load stocks from user_data directory."""
    with open(STOCKS_FILE, 'r') as f:
        reader = csv.DictReader(f)
        return list(reader)


def get_transactions():
    """Load transactions from user_data directory."""
    with open(TRANSACTIONS_FILE, 'r') as f:
        reader = csv.DictReader(f)
        return list(reader)


def calculate_fifo_cost_basis(ticker, transactions):
    """
    Calculate FIFO cost basis for sells.

    Args:
        ticker: Stock ticker symbol
        transactions: List of transaction dicts

    Returns:
        Tuple of (sell_basis dict, remaining_lots list)
        - sell_basis: Dict mapping transaction id to cost basis info for sells
        - remaining_lots: List of lots with remaining shares
    """
    # Build list of lots (buys) in order
    lots = []  # Each lot: {'shares': n, 'price': p, 'remaining': n}
    sell_basis = {}  # txn_id -> {'cost_basis': total_cost, 'shares': n, 'avg_cost_per_share': p}

    for txn in transactions:
        if txn['ticker'] != ticker:
            continue

        shares = int(txn['shares']) if txn['shares'] else 0
        price = float(txn['price']) if txn['price'] else 0

        if txn['action'] == 'buy':
            lots.append({'shares': shares, 'price': price, 'remaining': shares})
        elif txn['action'] == 'sell':
            # Use FIFO to determine cost basis
            shares_to_sell = shares
            total_cost = 0
            lots_used = []

            for lot in lots:
                if shares_to_sell <= 0:
                    break
                if lot['remaining'] <= 0:
                    continue

                take = min(lot['remaining'], shares_to_sell)
                total_cost += take * lot['price']
                lot['remaining'] -= take
                shares_to_sell -= take
                lots_used.append({'shares': take, 'price': lot['price']})

            avg_cost = total_cost / shares if shares > 0 else 0
            sell_basis[txn['id']] = {
                'cost_basis': total_cost,
                'shares': shares,
                'avg_cost_per_share': avg_cost,
                'lots_used': lots_used
            }

    return sell_basis, lots


def calculate_holdings(confirmed_only=False):
    """
    Calculate current holdings from transactions with FIFO lot tracking.

    Args:
        confirmed_only: If True, only include buys with status='done'

    Returns:
        Dict mapping ticker to holding info
    """
    stocks = {s['ticker']: s for s in get_stocks()}
    transactions = get_transactions()

    # Group transactions by ticker, optionally filtering buys by status
    by_ticker = {}
    for txn in transactions:
        # If confirmed_only, skip buy transactions that aren't 'done'
        if confirmed_only and txn['action'] == 'buy':
            status = (txn.get('status') or '').lower()
            if status != 'done':
                continue

        ticker = txn['ticker']
        if ticker not in by_ticker:
            by_ticker[ticker] = []
        by_ticker[ticker].append(txn)

    holdings = {}
    for ticker, ticker_txns in by_ticker.items():
        # Calculate FIFO cost basis for this ticker
        sell_basis, remaining_lots = calculate_fifo_cost_basis(ticker, ticker_txns)

        # Calculate remaining shares and cost basis
        total_shares = sum(lot['remaining'] for lot in remaining_lots)
        total_cost = sum(lot['remaining'] * lot['price'] for lot in remaining_lots)

        holdings[ticker] = {
            'ticker': ticker,
            'name': stocks.get(ticker, {}).get('name', ticker),
            'type': stocks.get(ticker, {}).get('type', 'stock'),
            'shares': total_shares,
            'total_cost': total_cost,
            'avg_cost': total_cost / total_shares if total_shares > 0 else 0,
            'remaining_lots': [{'shares': l['remaining'], 'price': l['price']} for l in remaining_lots if l['remaining'] > 0],
            'transactions': []
        }

        # Add transactions with computed gain percentages for sells
        for txn in ticker_txns:
            txn_copy = dict(txn)
            if txn['action'] == 'sell' and txn['id'] in sell_basis:
                basis = sell_basis[txn['id']]
                sell_price = float(txn['price']) if txn['price'] else 0
                if basis['avg_cost_per_share'] > 0:
                    gain_pct = ((sell_price - basis['avg_cost_per_share']) / basis['avg_cost_per_share']) * 100
                    txn_copy['computed_gain_pct'] = round(gain_pct, 1)
                    txn_copy['fifo_cost_basis'] = round(basis['avg_cost_per_share'], 2)
            holdings[ticker]['transactions'].append(txn_copy)

    return holdings


class HoldingsService:
    """
    Service class for holdings-related operations.

    Provides a higher-level interface for working with holdings data.
    """

    def __init__(self, data_manager=None):
        """
        Initialize the holdings service.

        Args:
            data_manager: Optional data manager instance for valuation data
        """
        self.data_manager = data_manager

    def get_holdings(self, confirmed_only=False):
        """Get current holdings."""
        return calculate_holdings(confirmed_only)

    def get_holdings_summary(self):
        """Get a summary of all holdings."""
        holdings = calculate_holdings(confirmed_only=True)

        total_cost = 0
        total_shares_by_ticker = {}

        for ticker, holding in holdings.items():
            if holding['shares'] > 0:
                total_cost += holding['total_cost']
                total_shares_by_ticker[ticker] = holding['shares']

        return {
            'total_tickers': len([h for h in holdings.values() if h['shares'] > 0]),
            'total_cost_basis': round(total_cost, 2),
            'holdings': {
                ticker: {
                    'shares': h['shares'],
                    'avg_cost': round(h['avg_cost'], 2),
                    'total_cost': round(h['total_cost'], 2)
                }
                for ticker, h in holdings.items() if h['shares'] > 0
            }
        }

    def get_sell_candidates(self, valuations, threshold_overvalued=10, threshold_gain=30):
        """
        Identify stocks that should be considered for selling.

        Args:
            valuations: Dict of valuation data by ticker
            threshold_overvalued: % above fair value to flag
            threshold_gain: % gain from cost basis to flag

        Returns:
            List of sell candidate dicts
        """
        holdings = calculate_holdings(confirmed_only=True)
        candidates = []

        for ticker, holding in holdings.items():
            if holding['shares'] <= 0:
                continue

            valuation = valuations.get(ticker, {})
            current_price = valuation.get('current_price')
            estimated_value = valuation.get('estimated_value')
            price_vs_value = valuation.get('price_vs_value')

            if not current_price:
                continue

            reasons = []

            # Check if overvalued
            if price_vs_value and price_vs_value > threshold_overvalued:
                reasons.append(f"Overvalued by {price_vs_value:.1f}%")

            # Check if significant gain from cost basis
            if holding['avg_cost'] > 0:
                gain_pct = ((current_price - holding['avg_cost']) / holding['avg_cost']) * 100
                if gain_pct > threshold_gain:
                    reasons.append(f"Gained {gain_pct:.1f}% from cost basis")

            if reasons:
                candidates.append({
                    'ticker': ticker,
                    'name': holding['name'],
                    'shares': holding['shares'],
                    'avg_cost': round(holding['avg_cost'], 2),
                    'current_price': round(current_price, 2),
                    'estimated_value': round(estimated_value, 2) if estimated_value else None,
                    'price_vs_value': round(price_vs_value, 1) if price_vs_value else None,
                    'reasons': reasons
                })

        # Sort by most compelling sell reasons
        candidates.sort(key=lambda x: len(x['reasons']), reverse=True)
        return candidates

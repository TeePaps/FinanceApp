"""
Transactions routes blueprint.

Handles:
- GET /api/transactions - Get all transactions
- POST /api/transactions - Create new transaction
- PUT /api/transactions/<id> - Update transaction
- DELETE /api/transactions/<id> - Delete transaction
- GET /api/stocks - Get stock list
- POST /api/stocks - Add new stock
"""

import csv
import os
from flask import Blueprint, jsonify, request
from config import DATA_DIR, STOCKS_FILE, TRANSACTIONS_FILE

transactions_bp = Blueprint('transactions', __name__, url_prefix='/api')


def read_csv(filename):
    """Read CSV file from data directory."""
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        return list(reader)


def write_user_csv(filepath, data, fieldnames):
    """Write CSV file to user_data directory."""
    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)


def get_transactions():
    """Load transactions from user_data directory."""
    with open(TRANSACTIONS_FILE, 'r') as f:
        reader = csv.DictReader(f)
        return list(reader)


def get_stocks():
    """Load stocks from user_data directory."""
    with open(STOCKS_FILE, 'r') as f:
        reader = csv.DictReader(f)
        return list(reader)


@transactions_bp.route('/transactions')
def api_transactions():
    """Get all transactions."""
    return jsonify(get_transactions())


@transactions_bp.route('/transactions', methods=['POST'])
def add_transaction():
    """Create a new transaction."""
    data = request.json
    transactions = get_transactions()

    # Generate new ID
    max_id = max(int(t['id']) for t in transactions) if transactions else 0
    data['id'] = str(max_id + 1)

    transactions.append(data)
    fieldnames = ['id', 'ticker', 'action', 'shares', 'price', 'gain_pct', 'date', 'status']
    write_user_csv(TRANSACTIONS_FILE, transactions, fieldnames)

    return jsonify({'success': True, 'id': data['id']})


@transactions_bp.route('/transactions/<int:txn_id>', methods=['PUT'])
def update_transaction(txn_id):
    """Update an existing transaction."""
    data = request.json
    transactions = get_transactions()

    for i, txn in enumerate(transactions):
        if int(txn['id']) == txn_id:
            transactions[i].update(data)
            break

    fieldnames = ['id', 'ticker', 'action', 'shares', 'price', 'gain_pct', 'date', 'status']
    write_user_csv(TRANSACTIONS_FILE, transactions, fieldnames)
    return jsonify({'success': True})


@transactions_bp.route('/transactions/<int:txn_id>', methods=['DELETE'])
def delete_transaction(txn_id):
    """Delete a transaction."""
    transactions = get_transactions()
    transactions = [t for t in transactions if int(t['id']) != txn_id]

    fieldnames = ['id', 'ticker', 'action', 'shares', 'price', 'gain_pct', 'date', 'status']
    write_user_csv(TRANSACTIONS_FILE, transactions, fieldnames)
    return jsonify({'success': True})


@transactions_bp.route('/stocks')
def api_stocks():
    """Get all stocks."""
    return jsonify(get_stocks())


@transactions_bp.route('/stocks', methods=['POST'])
def add_stock():
    """Add a new stock to the registry."""
    data = request.json
    stocks = get_stocks()

    # Check if ticker already exists
    if any(s['ticker'] == data.get('ticker') for s in stocks):
        return jsonify({'success': False, 'error': 'Ticker already exists'}), 400

    stocks.append({
        'ticker': data.get('ticker', '').upper(),
        'name': data.get('name', ''),
        'type': data.get('type', 'stock')
    })

    fieldnames = ['ticker', 'name', 'type']
    write_user_csv(STOCKS_FILE, stocks, fieldnames)

    return jsonify({'success': True})

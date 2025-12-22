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

from flask import Blueprint, jsonify, request
import database as db

transactions_bp = Blueprint('transactions', __name__, url_prefix='/api')


@transactions_bp.route('/transactions')
def api_transactions():
    """Get all transactions."""
    return jsonify(db.get_transactions())


@transactions_bp.route('/transactions', methods=['POST'])
def add_transaction():
    """Create a new transaction."""
    data = request.json
    txn_id = db.add_transaction(
        ticker=data.get('ticker', ''),
        action=data.get('action', ''),
        shares=int(data.get('shares', 0)) if data.get('shares') else 0,
        price=float(data.get('price', 0)) if data.get('price') else 0,
        gain_pct=float(data.get('gain_pct')) if data.get('gain_pct') else None,
        date=data.get('date'),
        status=data.get('status')
    )
    return jsonify({'success': True, 'id': txn_id})


@transactions_bp.route('/transactions/<int:txn_id>', methods=['PUT'])
def update_transaction(txn_id):
    """Update an existing transaction."""
    data = request.json
    db.update_transaction(txn_id, data)
    return jsonify({'success': True})


@transactions_bp.route('/transactions/<int:txn_id>', methods=['DELETE'])
def delete_transaction(txn_id):
    """Delete a transaction."""
    db.delete_transaction(txn_id)
    return jsonify({'success': True})


@transactions_bp.route('/stocks')
def api_stocks():
    """Get all stocks."""
    return jsonify(db.get_stocks())


@transactions_bp.route('/stocks', methods=['POST'])
def add_stock():
    """Add a new stock to the registry."""
    data = request.json
    ticker = data.get('ticker', '').upper()

    # Check if ticker already exists
    existing = db.get_stocks()
    if any(s['ticker'] == ticker for s in existing):
        return jsonify({'error': 'Ticker already exists'}), 400

    db.add_stock(
        ticker=ticker,
        name=data.get('name', ''),
        stock_type=data.get('type', 'stock')
    )
    return jsonify({'success': True})

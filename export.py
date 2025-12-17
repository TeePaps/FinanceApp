#!/usr/bin/env python3
"""Export stock data to plain text format."""

import csv
import os
from collections import defaultdict
from config import STOCKS_FILE, TRANSACTIONS_FILE


def read_user_csv(filepath):
    """Read CSV file from user_data directory."""
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        return list(reader)

def format_date(date_str):
    """Convert YYYY-MM-DD to M/D/YYYY format."""
    if not date_str or date_str in ('START', ''):
        return date_str
    try:
        parts = date_str.split('-')
        if len(parts) == 3:
            year, month, day = parts
            return f"{int(month)}/{int(day)}/{year}"
    except:
        pass
    return date_str

def export():
    stocks = {s['ticker']: s for s in read_user_csv(STOCKS_FILE)}
    transactions = read_user_csv(TRANSACTIONS_FILE)

    # Group transactions by ticker
    by_ticker = defaultdict(list)
    for txn in transactions:
        by_ticker[txn['ticker']].append(txn)

    # Separate stocks and index funds
    stock_tickers = [t for t in by_ticker if stocks.get(t, {}).get('type') != 'index']
    index_tickers = [t for t in by_ticker if stocks.get(t, {}).get('type') == 'index']

    output = []
    output.append("Stock trading")
    output.append("")

    for ticker in stock_tickers:
        txns = by_ticker[ticker]
        stock_info = stocks.get(ticker, {})
        name = stock_info.get('name', '')

        # Calculate current shares and estimate cost basis
        shares = 0
        for txn in txns:
            s = int(txn['shares']) if txn['shares'] else 0
            if txn['action'] == 'buy':
                shares += s
            else:
                shares -= s

        # Find a representative cost basis from first buy
        cost_basis = ''
        for txn in txns:
            if txn['action'] == 'buy' and txn['price']:
                cost_basis = f"${txn['price']}"
                break

        # Header line: TICKER Name SHARES $COST
        header_parts = [ticker]
        if name and name != ticker:
            header_parts.append(name)
        header_parts.append(str(shares))
        if cost_basis:
            header_parts.append(cost_basis)
        output.append(' '.join(header_parts))

        # Transaction lines
        for txn in txns:
            line_parts = []

            # Action and shares
            if txn['action'] == 'buy':
                line_parts.append(f"+{txn['shares']}")
            else:
                line_parts.append(f"-{txn['shares']}")

            # Price
            if txn['price']:
                line_parts.append(f"${txn['price']}")

            # Gain percentage (for sells)
            if txn['gain_pct']:
                line_parts.append(f"{txn['gain_pct']}%")

            # Date
            date = format_date(txn['date'])
            if date and date != 'START':
                line_parts.append(date)

            # Status
            status = txn['status'].upper() if txn['status'] else ''
            if status == 'START' or (not date and txn['action'] == 'buy' and not txn['gain_pct']):
                if 'START' not in line_parts:
                    line_parts.append('START')
            elif status:
                line_parts.append(status)

            output.append(' '.join(line_parts))

        output.append("")

    # Index funds section
    if index_tickers:
        output.append("---")
        output.append("")
        output.append("Index funds")

        for ticker in index_tickers:
            txns = by_ticker[ticker]
            stock_info = stocks.get(ticker, {})
            name = stock_info.get('name', ticker)

            output.append(ticker)
            for txn in txns:
                line_parts = []
                if txn['action'] == 'buy':
                    line_parts.append(f"+{txn['shares']}")
                else:
                    line_parts.append(f"-{txn['shares']}")

                if txn['price']:
                    line_parts.append(f"${txn['price']}")

                output.append(' '.join(line_parts))
            output.append("")

    return '\n'.join(output)

if __name__ == '__main__':
    print(export())

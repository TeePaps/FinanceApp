#!/usr/bin/env python3
"""
Migration Script: Flat Files to SQLite Database

This script migrates all existing flat file data (JSON and CSV) to the SQLite database.
After migration, the flat files are moved to an archive folder.

Usage:
    python migrate_to_db.py [--dry-run] [--skip-archive]

Options:
    --dry-run       Show what would be migrated without actually doing it
    --skip-archive  Don't move files to archive after migration
"""

import os
import sys
import json
import csv
import shutil
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

import database as db

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
USER_DATA_DIR = os.path.join(BASE_DIR, 'user_data')
SEC_DIR = os.path.join(DATA_DIR, 'sec')
COMPANIES_DIR = os.path.join(SEC_DIR, 'companies')
ARCHIVE_DIR = os.path.join(BASE_DIR, 'archive')


def load_json_file(filepath):
    """Load a JSON file, return None if doesn't exist or invalid."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  Warning: Could not load {filepath}: {e}")
        return None


def load_csv_file(filepath):
    """Load a CSV file, return empty list if doesn't exist."""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            return list(reader)
    except IOError as e:
        print(f"  Warning: Could not load {filepath}: {e}")
        return []


def migrate_ticker_status(dry_run=False):
    """Migrate ticker_status.json to database."""
    print("\n[1/7] Migrating ticker status...")

    filepath = os.path.join(DATA_DIR, 'ticker_status.json')
    data = load_json_file(filepath)

    if not data or not data.get('tickers'):
        print("  No ticker status data found, skipping.")
        return 0

    tickers = data['tickers']
    print(f"  Found {len(tickers)} tickers to migrate")

    if dry_run:
        print("  [DRY RUN] Would migrate ticker status")
        return len(tickers)

    # Prepare bulk update
    updates = {}
    for ticker, info in tickers.items():
        updates[ticker] = {
            'company_name': info.get('company_name'),
            'sec_status': info.get('sec_status', 'unknown'),
            'sec_checked': info.get('sec_checked'),
            'valuation_updated': info.get('valuation_updated'),
            'indexes': info.get('indexes', [])
        }

    db.bulk_update_ticker_status(updates)
    print(f"  Migrated {len(updates)} ticker statuses")
    return len(updates)


def migrate_valuations(dry_run=False):
    """Migrate valuations.json to database."""
    print("\n[2/7] Migrating valuations...")

    filepath = os.path.join(DATA_DIR, 'valuations.json')
    data = load_json_file(filepath)

    if not data or not data.get('valuations'):
        print("  No valuations data found, skipping.")
        return 0

    valuations = data['valuations']
    print(f"  Found {len(valuations)} valuations to migrate")

    if dry_run:
        print("  [DRY RUN] Would migrate valuations")
        return len(valuations)

    db.bulk_update_valuations(valuations)
    print(f"  Migrated {len(valuations)} valuations")
    return len(valuations)


def migrate_cik_mapping(dry_run=False):
    """Migrate cik_mapping.json to database."""
    print("\n[3/7] Migrating CIK mapping...")

    filepath = os.path.join(SEC_DIR, 'cik_mapping.json')
    data = load_json_file(filepath)

    if not data or not data.get('tickers'):
        print("  No CIK mapping data found, skipping.")
        return 0

    tickers = data['tickers']
    print(f"  Found {len(tickers)} CIK mappings to migrate")

    if dry_run:
        print("  [DRY RUN] Would migrate CIK mappings")
        return len(tickers)

    db.save_cik_mapping(data)
    print(f"  Migrated {len(tickers)} CIK mappings")
    return len(tickers)


def migrate_sec_companies(dry_run=False):
    """Migrate SEC company JSON files to database."""
    print("\n[4/7] Migrating SEC company data...")

    if not os.path.exists(COMPANIES_DIR):
        print("  No SEC companies directory found, skipping.")
        return 0

    files = [f for f in os.listdir(COMPANIES_DIR) if f.endswith('.json')]
    print(f"  Found {len(files)} SEC company files to migrate")

    if dry_run:
        print("  [DRY RUN] Would migrate SEC company data")
        return len(files)

    migrated = 0
    for filename in files:
        ticker = filename.replace('.json', '')
        filepath = os.path.join(COMPANIES_DIR, filename)
        data = load_json_file(filepath)

        if data:
            db.save_sec_company(ticker, data)
            migrated += 1

        # Progress indicator
        if migrated % 500 == 0:
            print(f"    Migrated {migrated}/{len(files)} companies...")

    print(f"  Migrated {migrated} SEC company records")
    return migrated


def migrate_ticker_failures(dry_run=False):
    """Migrate ticker_failures.json to database."""
    print("\n[5/7] Migrating ticker failures...")

    filepath = os.path.join(DATA_DIR, 'ticker_failures.json')
    data = load_json_file(filepath)

    if not data:
        print("  No ticker failures data found, skipping.")
        return 0

    # The failures file can have different structures, handle both
    failures = data if isinstance(data, dict) else {}
    print(f"  Found {len(failures)} ticker failures to migrate")

    if dry_run:
        print("  [DRY RUN] Would migrate ticker failures")
        return len(failures)

    migrated = 0
    for ticker, info in failures.items():
        if isinstance(info, dict):
            count = info.get('failure_count', info.get('count', 1))
            reason = info.get('reason')
            for _ in range(count):
                db.record_ticker_failure(ticker, reason)
            migrated += 1
        elif isinstance(info, int):
            for _ in range(info):
                db.record_ticker_failure(ticker)
            migrated += 1

    print(f"  Migrated {migrated} ticker failure records")
    return migrated


def migrate_user_stocks(dry_run=False):
    """Migrate stocks.csv to database."""
    print("\n[6/7] Migrating user stocks...")

    filepath = os.path.join(USER_DATA_DIR, 'stocks.csv')
    stocks = load_csv_file(filepath)

    if not stocks:
        print("  No stocks data found, skipping.")
        return 0

    print(f"  Found {len(stocks)} stocks to migrate")

    if dry_run:
        print("  [DRY RUN] Would migrate user stocks")
        return len(stocks)

    for stock in stocks:
        db.add_stock(
            ticker=stock.get('ticker', ''),
            name=stock.get('name', ''),
            stock_type=stock.get('type', 'stock')
        )

    print(f"  Migrated {len(stocks)} stocks")
    return len(stocks)


def migrate_user_transactions(dry_run=False):
    """Migrate transactions.csv to database."""
    print("\n[7/7] Migrating user transactions...")

    filepath = os.path.join(USER_DATA_DIR, 'transactions.csv')
    transactions = load_csv_file(filepath)

    if not transactions:
        print("  No transactions data found, skipping.")
        return 0

    print(f"  Found {len(transactions)} transactions to migrate")

    if dry_run:
        print("  [DRY RUN] Would migrate user transactions")
        return len(transactions)

    with db.get_db() as conn:
        cursor = conn.cursor()
        for txn in transactions:
            # Parse values carefully
            txn_id = int(txn.get('id', 0)) if txn.get('id') else None
            shares = int(txn.get('shares', 0)) if txn.get('shares') else 0
            price = float(txn.get('price', 0)) if txn.get('price') else 0
            gain_pct = float(txn.get('gain_pct')) if txn.get('gain_pct') else None

            cursor.execute('''
                INSERT OR REPLACE INTO transactions (id, ticker, action, shares, price, gain_pct, date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                txn_id,
                txn.get('ticker', '').upper(),
                txn.get('action', ''),
                shares,
                price,
                gain_pct,
                txn.get('date'),
                txn.get('status')
            ))

    print(f"  Migrated {len(transactions)} transactions")
    return len(transactions)


def archive_flat_files(dry_run=False):
    """Move flat files to archive folder."""
    print("\n[Archive] Moving flat files to archive...")

    if dry_run:
        print("  [DRY RUN] Would move files to archive/")
        return

    # Create archive directories
    archive_data = os.path.join(ARCHIVE_DIR, 'data')
    archive_user_data = os.path.join(ARCHIVE_DIR, 'user_data')
    archive_sec = os.path.join(archive_data, 'sec')
    archive_companies = os.path.join(archive_sec, 'companies')

    os.makedirs(archive_data, exist_ok=True)
    os.makedirs(archive_user_data, exist_ok=True)
    os.makedirs(archive_sec, exist_ok=True)
    os.makedirs(archive_companies, exist_ok=True)

    # Files to archive from data/
    data_files = [
        'ticker_status.json',
        'valuations.json',
        'ticker_failures.json',
        'refresh_summary.json',
        'excluded_tickers.json',
        'sp500.json',
        'nasdaq100.json',
        'dow30.json',
        'sp600.json',
        'russell2000.json'
    ]

    archived = 0
    for filename in data_files:
        src = os.path.join(DATA_DIR, filename)
        dst = os.path.join(archive_data, filename)
        if os.path.exists(src):
            shutil.move(src, dst)
            archived += 1
            print(f"  Archived: data/{filename}")

    # Archive SEC files
    sec_files = ['metadata.json', 'cik_mapping.json']
    for filename in sec_files:
        src = os.path.join(SEC_DIR, filename)
        dst = os.path.join(archive_sec, filename)
        if os.path.exists(src):
            shutil.move(src, dst)
            archived += 1
            print(f"  Archived: data/sec/{filename}")

    # Archive company files
    if os.path.exists(COMPANIES_DIR):
        company_files = [f for f in os.listdir(COMPANIES_DIR) if f.endswith('.json')]
        for filename in company_files:
            src = os.path.join(COMPANIES_DIR, filename)
            dst = os.path.join(archive_companies, filename)
            shutil.move(src, dst)
            archived += 1

        # Remove empty companies directory
        try:
            os.rmdir(COMPANIES_DIR)
        except OSError:
            pass

        print(f"  Archived: {len(company_files)} SEC company files")

    # Archive user data files
    user_files = ['stocks.csv', 'transactions.csv']
    for filename in user_files:
        src = os.path.join(USER_DATA_DIR, filename)
        dst = os.path.join(archive_user_data, filename)
        if os.path.exists(src):
            shutil.move(src, dst)
            archived += 1
            print(f"  Archived: user_data/{filename}")

    print(f"\n  Total files archived: {archived}")
    print(f"  Archive location: {ARCHIVE_DIR}")


def main():
    """Run the migration."""
    dry_run = '--dry-run' in sys.argv
    skip_archive = '--skip-archive' in sys.argv

    print("=" * 60)
    print("Finance App: Flat File to SQLite Migration")
    print("=" * 60)

    if dry_run:
        print("\n*** DRY RUN MODE - No changes will be made ***\n")

    # Initialize database
    if not dry_run:
        print("\nInitializing database schema...")
        db.init_database()

    # Run migrations
    stats = {
        'ticker_status': migrate_ticker_status(dry_run),
        'valuations': migrate_valuations(dry_run),
        'cik_mapping': migrate_cik_mapping(dry_run),
        'sec_companies': migrate_sec_companies(dry_run),
        'ticker_failures': migrate_ticker_failures(dry_run),
        'user_stocks': migrate_user_stocks(dry_run),
        'user_transactions': migrate_user_transactions(dry_run)
    }

    # Archive flat files
    if not skip_archive:
        archive_flat_files(dry_run)

    # Summary
    print("\n" + "=" * 60)
    print("Migration Summary")
    print("=" * 60)
    print(f"  Ticker statuses:    {stats['ticker_status']:,}")
    print(f"  Valuations:         {stats['valuations']:,}")
    print(f"  CIK mappings:       {stats['cik_mapping']:,}")
    print(f"  SEC companies:      {stats['sec_companies']:,}")
    print(f"  Ticker failures:    {stats['ticker_failures']:,}")
    print(f"  User stocks:        {stats['user_stocks']:,}")
    print(f"  User transactions:  {stats['user_transactions']:,}")
    print("=" * 60)

    if dry_run:
        print("\n*** DRY RUN COMPLETE - Run without --dry-run to perform migration ***")
    else:
        print(f"\nMigration complete! Database: {db.DATABASE_PATH}")
        if not skip_archive:
            print(f"Flat files archived to: {ARCHIVE_DIR}")

    return 0


if __name__ == '__main__':
    sys.exit(main())

"""
Consolidated data manager for the Finance App.

Provides a single source of truth for:
- Ticker status (SEC availability, last updated, etc.)
- Valuations (consolidated across all indexes)
- Index membership

This module now uses SQLite database for all data storage.
Legacy file-based operations have been replaced with database calls.
"""

from datetime import datetime
from typing import Dict, List, Optional, Set

# Import database module for all operations
import database as db

# Import index definitions from central registry
from services.indexes import VALID_INDICES, INDEX_NAMES, INDIVIDUAL_INDICES


def ensure_data_dir():
    """Ensure database is initialized (legacy compatibility)."""
    db.init_database()


# --- Ticker Status ---

def load_ticker_status() -> Dict:
    """
    Load the ticker status data.

    Returns dict with structure:
    {
        'tickers': {ticker: {info}},
        'last_updated': timestamp,
        'version': 1
    }
    """
    # Build compatible structure from database
    with db.get_db() as conn:
        cursor = conn.cursor()

        # Get all tickers with their info
        cursor.execute('SELECT * FROM tickers')
        tickers = {}
        for row in cursor.fetchall():
            ticker = row['ticker']
            tickers[ticker] = {
                'ticker': ticker,
                'company_name': row['company_name'],
                'sec_status': row['sec_status'] or 'unknown',
                'sec_checked': row['sec_checked'],
                'valuation_updated': row['valuation_updated'],
                'updated': row['updated'],
                'indexes': []
            }

        # Get index memberships
        cursor.execute('SELECT ticker, index_name FROM ticker_indexes')
        for row in cursor.fetchall():
            ticker = row['ticker']
            if ticker in tickers:
                tickers[ticker]['indexes'].append(row['index_name'])

        # Get last updated timestamp
        cursor.execute('SELECT MAX(updated) as latest FROM tickers')
        latest = cursor.fetchone()['latest']

        return {
            'tickers': tickers,
            'last_updated': latest,
            'version': 1
        }


def get_ticker_info(ticker: str) -> Optional[Dict]:
    """Get status info for a single ticker."""
    return db.get_ticker_info(ticker)


def update_ticker_status(ticker: str, updates: Dict):
    """Update status for a single ticker."""
    db.update_ticker_status(ticker, updates)


def bulk_update_ticker_status(updates: Dict[str, Dict]):
    """Bulk update multiple tickers at once (more efficient)."""
    db.bulk_update_ticker_status(updates)


def set_ticker_indexes(ticker: str, indexes: List[str]):
    """Set which indexes a ticker belongs to."""
    db.update_ticker_status(ticker, {'indexes': indexes})


def get_tickers_by_status(sec_status: str) -> List[str]:
    """Get all tickers with a specific SEC status."""
    return db.get_tickers_by_status(sec_status)


def get_tickers_needing_sec_check() -> List[str]:
    """Get tickers that haven't been checked for SEC data."""
    return db.get_tickers_needing_sec_check()


def get_all_tracked_tickers() -> Set[str]:
    """Get all tickers we're tracking."""
    return db.get_all_tracked_tickers()


# --- Valuations ---

def load_valuations() -> Dict:
    """
    Load consolidated valuations.

    Returns dict with structure:
    {
        'valuations': {ticker: {valuation}},
        'last_updated': timestamp,
        'version': 1
    }
    """
    valuations = db.get_all_valuations()

    # Get last updated
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(updated) as latest FROM valuations')
        latest = cursor.fetchone()['latest']

    return {
        'valuations': valuations,
        'last_updated': latest,
        'version': 1
    }


def get_valuation(ticker: str) -> Optional[Dict]:
    """Get valuation for a single ticker."""
    return db.get_valuation(ticker)


def update_valuation(ticker: str, valuation: Dict):
    """Update valuation for a single ticker."""
    db.update_valuation(ticker, valuation)


def bulk_update_valuations(valuations: Dict[str, Dict]):
    """Bulk update multiple valuations at once."""
    db.bulk_update_valuations(valuations)


def get_valuations_for_index(index_name: str, index_tickers: List[str] = None) -> List[Dict]:
    """Get valuations for tickers in a specific index."""
    if index_tickers:
        # Filter by provided ticker list
        all_vals = db.get_all_valuations()
        return [all_vals[t] for t in index_tickers if t in all_vals]
    return db.get_valuations_for_index(index_name)


def get_undervalued_tickers(threshold: float = -20.0) -> List[Dict]:
    """Get all tickers that are undervalued by more than threshold %."""
    return db.get_undervalued_tickers(threshold)


# --- Statistics ---

def get_data_stats() -> Dict:
    """Get comprehensive statistics about the data."""
    return db.get_data_stats()


# --- Migration ---

def migrate_from_old_structure():
    """
    Migrate data from the old structure (per-index valuations) to the new structure.
    This is now handled by migrate_to_db.py script.
    """
    print("[Migration] Run 'python migrate_to_db.py' to migrate flat files to database")
    return {'tickers_migrated': 0, 'valuations_migrated': 0}


# --- Index Management ---

def get_index_tickers(index_name: str, include_delisted: bool = False) -> List[str]:
    """Get list of tickers for an index, optionally excluding delisted."""
    if include_delisted:
        return db.get_index_tickers(index_name)
    return db.get_active_index_tickers(index_name)


def sync_index_membership(index_name: str, tickers: List[str]):
    """
    Sync index membership for a list of tickers.
    Adds index to tickers that should have it, creates new ticker entries as needed.
    """
    db.sync_index_membership(index_name, tickers)


def refresh_index_membership(index_name: str, current_tickers: List[str]) -> Dict:
    """
    Refresh index membership from authoritative source.
    Marks removed tickers as inactive, adds new ones.
    Returns dict with 'added', 'removed', 'total' counts.
    """
    return db.refresh_index_membership(index_name, current_tickers)

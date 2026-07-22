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
    latest = db.get_latest_valuation_timestamp()

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


def save_single_valuation(ticker: str, valuation: Dict):
    """Save a single ticker's valuation to database.

    Only writes fields the caller actually computed. update_valuation is a
    column-scoped upsert, so a None here NULLs that column — building the
    dict with unconditional .get() calls meant a transient EPS/price failure
    (calculate_valuation returns None for eps_avg/estimated_value/...) wiped
    the ticker's good cached EPS, fair value, and company name.
    """
    fields = ('current_price', 'eps_avg', 'eps_years', 'eps_source',
              'annual_dividend', 'estimated_value', 'price_vs_value',
              'company_name')
    update_data = {k: valuation[k] for k in fields
                   if valuation.get(k) is not None}
    if not update_data:
        return  # nothing worth writing
    update_data['updated'] = datetime.now().isoformat()
    db.update_valuation(ticker, update_data)


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


# --- Helper Functions for Index Data Access ---

def get_all_unique_tickers() -> List[str]:
    """Get all unique tickers across all enabled indexes (deduplicated)."""
    all_tickers = set()
    enabled_indexes = db.get_enabled_indexes()
    for index_name in INDIVIDUAL_INDICES:
        if index_name in enabled_indexes:
            tickers = db.get_active_index_tickers(index_name)
            all_tickers.update(tickers)
    return sorted(list(all_tickers))


def get_index_data(index_name: str = 'all') -> Dict:
    """
    Load index data from database.
    Uses centralized valuations from database.
    Index ticker lists are stored in ticker_indexes table.

    Returns dict with: name, short_name, tickers, valuations, last_updated
    """
    from services.utils import sanitize_for_json

    if index_name not in VALID_INDICES:
        index_name = 'all'

    # Always load from centralized valuations storage
    valuations_data = load_valuations()
    all_valuations = valuations_data.get('valuations', {})
    last_updated = valuations_data.get('last_updated')

    # Special handling for 'all' - combine all indexes
    if index_name == 'all':
        all_tickers = get_all_unique_tickers()
        # Filter to the active/enabled universe like the per-index branch —
        # the valuations table still holds rows for delisted / disabled /
        # orphaned tickers, and returning them all leaked those into the
        # 'all' view (and its recommendations) even though they're excluded
        # from every specific index.
        universe = set(all_tickers)
        filtered = {t: v for t, v in all_valuations.items() if t in universe}
        return {
            'name': 'All Indexes',
            'short_name': 'All',
            'tickers': all_tickers,
            'valuations': filtered,
            'last_updated': last_updated
        }

    # Get tickers from database (excludes inactive/delisted)
    tickers = db.get_active_index_tickers(index_name)

    # If no tickers in database, fetch from web and store
    if not tickers:
        print(f"[Index] No tickers in database for {index_name}, fetching from web...")
        from services.indexes import fetch_index_tickers
        tickers = fetch_index_tickers(index_name)
        if tickers:
            db.refresh_index_membership(index_name, tickers)

    # Get index display names
    name, short_name = INDEX_NAMES.get(index_name, (index_name, index_name))

    # Filter centralized valuations to only include this index's tickers
    index_tickers = set(tickers)
    filtered_valuations = {
        ticker: val for ticker, val in all_valuations.items()
        if ticker in index_tickers
    }

    # Per-index freshness: the max 'updated' among THIS index's rows, not the
    # global MAX (which made a stale index look fresh whenever any other
    # index updated).
    index_updated = max(
        (v.get('updated') for v in filtered_valuations.values() if v.get('updated')),
        default=None,
    )

    # Return with centralized valuations filtered by index
    result = {
        'name': name,
        'short_name': short_name,
        'tickers': tickers,
        'valuations': filtered_valuations,
        'last_updated': index_updated if index_updated else last_updated
    }

    return sanitize_for_json(result)


# Cache for ticker-to-index mapping (rebuilt when enabled indexes OR
# membership change)
_ticker_index_cache = None
_ticker_index_cache_enabled = None  # Track which indexes were enabled when cache was built
_ticker_index_cache_version = None  # db membership version when cache was built


def get_all_ticker_indexes() -> Dict[str, List[str]]:
    """
    Get a mapping of all tickers to their enabled indexes (cached).

    Returns dict mapping ticker -> list of short index names.
    """
    global _ticker_index_cache, _ticker_index_cache_enabled, _ticker_index_cache_version
    enabled_indexes = db.get_enabled_indexes()
    membership_version = db.get_membership_version()

    # Rebuild cache if the enabled-index SET changed OR membership changed.
    # The old code keyed only on the enabled set, so members added/removed by
    # refresh_index_membership() during a screener run were never reflected —
    # stale index badges/filters until the process restarted.
    if (_ticker_index_cache is None
            or _ticker_index_cache_enabled != enabled_indexes
            or _ticker_index_cache_version != membership_version):
        _ticker_index_cache = {}
        _ticker_index_cache_enabled = enabled_indexes
        _ticker_index_cache_version = membership_version
        for index_name in INDIVIDUAL_INDICES:
            if index_name in enabled_indexes:
                tickers = db.get_active_index_tickers(index_name)
                short_name = INDEX_NAMES.get(index_name, (index_name, index_name))[1]
                for ticker in tickers:
                    if ticker not in _ticker_index_cache:
                        _ticker_index_cache[ticker] = []
                    _ticker_index_cache[ticker].append(short_name)

    return _ticker_index_cache

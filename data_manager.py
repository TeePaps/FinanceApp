"""
Consolidated data manager for the Finance App.

Provides a single source of truth for:
- Ticker status (SEC availability, last updated, etc.)
- Valuations (consolidated across all indexes)
- Index membership

Structure:
- data/ticker_status.json: Status and metadata for each ticker
- data/valuations.json: All valuation data in one place
- data/indexes/: Index membership lists only (no duplicate valuations)
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
TICKER_STATUS_FILE = os.path.join(DATA_DIR, 'ticker_status.json')
VALUATIONS_FILE = os.path.join(DATA_DIR, 'valuations.json')

# Valid indexes
VALID_INDICES = ['sp500', 'nasdaq100', 'dow30', 'sp600', 'russell2000']
INDEX_NAMES = {
    'sp500': ('S&P 500', 'S&P 500'),
    'nasdaq100': ('NASDAQ 100', 'NASDAQ'),
    'dow30': ('Dow Jones Industrial Average', 'DJIA'),
    'sp600': ('S&P SmallCap 600', 'S&P 600'),
    'russell2000': ('Russell 2000', 'Russell 2000')
}


def ensure_data_dir():
    """Ensure data directory exists"""
    os.makedirs(DATA_DIR, exist_ok=True)


# --- Ticker Status ---

def load_ticker_status() -> Dict:
    """Load the ticker status file"""
    if not os.path.exists(TICKER_STATUS_FILE):
        return {
            'tickers': {},
            'last_updated': None,
            'version': 1
        }

    try:
        with open(TICKER_STATUS_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {
            'tickers': {},
            'last_updated': None,
            'version': 1
        }


def save_ticker_status(data: Dict):
    """Save the ticker status file"""
    ensure_data_dir()
    data['last_updated'] = datetime.now().isoformat()
    with open(TICKER_STATUS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def get_ticker_info(ticker: str) -> Optional[Dict]:
    """Get status info for a single ticker"""
    status = load_ticker_status()
    return status['tickers'].get(ticker.upper())


def update_ticker_status(ticker: str, updates: Dict):
    """Update status for a single ticker"""
    ticker = ticker.upper()
    status = load_ticker_status()

    if ticker not in status['tickers']:
        status['tickers'][ticker] = {
            'ticker': ticker,
            'indexes': [],
            'sec_status': 'unknown',  # available, unavailable, unknown
            'sec_checked': None,
            'valuation_updated': None,
            'company_name': None
        }

    status['tickers'][ticker].update(updates)
    status['tickers'][ticker]['updated'] = datetime.now().isoformat()
    save_ticker_status(status)


def bulk_update_ticker_status(updates: Dict[str, Dict]):
    """Bulk update multiple tickers at once (more efficient)"""
    status = load_ticker_status()

    for ticker, ticker_updates in updates.items():
        ticker = ticker.upper()
        if ticker not in status['tickers']:
            status['tickers'][ticker] = {
                'ticker': ticker,
                'indexes': [],
                'sec_status': 'unknown',
                'sec_checked': None,
                'valuation_updated': None,
                'company_name': None
            }
        status['tickers'][ticker].update(ticker_updates)
        status['tickers'][ticker]['updated'] = datetime.now().isoformat()

    save_ticker_status(status)


def set_ticker_indexes(ticker: str, indexes: List[str]):
    """Set which indexes a ticker belongs to"""
    update_ticker_status(ticker, {'indexes': indexes})


def get_tickers_by_status(sec_status: str) -> List[str]:
    """Get all tickers with a specific SEC status"""
    status = load_ticker_status()
    return [
        t for t, info in status['tickers'].items()
        if info.get('sec_status') == sec_status
    ]


def get_tickers_needing_sec_check() -> List[str]:
    """Get tickers that haven't been checked for SEC data"""
    status = load_ticker_status()
    return [
        t for t, info in status['tickers'].items()
        if info.get('sec_status') == 'unknown'
    ]


def get_all_tracked_tickers() -> Set[str]:
    """Get all tickers we're tracking"""
    status = load_ticker_status()
    return set(status['tickers'].keys())


# --- Valuations ---

def load_valuations() -> Dict:
    """Load consolidated valuations"""
    if not os.path.exists(VALUATIONS_FILE):
        return {
            'valuations': {},
            'last_updated': None,
            'version': 1
        }

    try:
        with open(VALUATIONS_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {
            'valuations': {},
            'last_updated': None,
            'version': 1
        }


def save_valuations(data: Dict):
    """Save consolidated valuations"""
    ensure_data_dir()
    data['last_updated'] = datetime.now().isoformat()
    with open(VALUATIONS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def get_valuation(ticker: str) -> Optional[Dict]:
    """Get valuation for a single ticker"""
    data = load_valuations()
    return data['valuations'].get(ticker.upper())


def update_valuation(ticker: str, valuation: Dict):
    """Update valuation for a single ticker"""
    ticker = ticker.upper()
    data = load_valuations()

    valuation['ticker'] = ticker
    valuation['updated'] = datetime.now().isoformat()
    data['valuations'][ticker] = valuation

    save_valuations(data)


def bulk_update_valuations(valuations: Dict[str, Dict]):
    """Bulk update multiple valuations at once"""
    data = load_valuations()
    now = datetime.now().isoformat()

    for ticker, valuation in valuations.items():
        ticker = ticker.upper()
        valuation['ticker'] = ticker
        valuation['updated'] = now
        data['valuations'][ticker] = valuation

    save_valuations(data)


def get_valuations_for_index(index_name: str, index_tickers: List[str]) -> List[Dict]:
    """Get valuations for tickers in a specific index"""
    data = load_valuations()
    result = []

    for ticker in index_tickers:
        ticker = ticker.upper()
        if ticker in data['valuations']:
            result.append(data['valuations'][ticker])

    return result


def get_undervalued_tickers(threshold: float = -20.0) -> List[Dict]:
    """Get all tickers that are undervalued by more than threshold %"""
    data = load_valuations()
    undervalued = []

    for ticker, val in data['valuations'].items():
        price_vs_value = val.get('price_vs_value')
        if price_vs_value is not None and price_vs_value < threshold:
            undervalued.append(val)

    # Sort by most undervalued first
    undervalued.sort(key=lambda x: x.get('price_vs_value', 0))
    return undervalued


# --- Statistics ---

def get_data_stats() -> Dict:
    """Get comprehensive statistics about the data"""
    status = load_ticker_status()
    valuations = load_valuations()

    tickers = status.get('tickers', {})
    vals = valuations.get('valuations', {})

    # Count by SEC status
    sec_available = len([t for t, i in tickers.items() if i.get('sec_status') == 'available'])
    sec_unavailable = len([t for t, i in tickers.items() if i.get('sec_status') == 'unavailable'])
    sec_unknown = len([t for t, i in tickers.items() if i.get('sec_status') == 'unknown'])

    # Count by index
    index_counts = {}
    for index_name in VALID_INDICES:
        count = len([t for t, i in tickers.items() if index_name in i.get('indexes', [])])
        index_counts[index_name] = count

    # Valuation stats
    with_valuation = len([v for v in vals.values() if v.get('estimated_value') is not None])

    return {
        'total_tickers': len(tickers),
        'sec_available': sec_available,
        'sec_unavailable': sec_unavailable,
        'sec_unknown': sec_unknown,
        'index_counts': index_counts,
        'with_valuation': with_valuation,
        'status_last_updated': status.get('last_updated'),
        'valuations_last_updated': valuations.get('last_updated')
    }


# --- Migration ---

def migrate_from_old_structure():
    """
    Migrate data from the old structure (per-index valuations) to the new structure.
    This should be run once to consolidate existing data.
    """
    import sec_data

    print("[Migration] Starting migration to consolidated data structure...")

    # Load existing index files
    old_index_files = {
        'sp500': os.path.join(DATA_DIR, 'sp500.json'),
        'nasdaq100': os.path.join(DATA_DIR, 'nasdaq100.json'),
        'dow30': os.path.join(DATA_DIR, 'dow30.json')
    }

    # Collect all tickers and their index memberships
    all_tickers = {}  # ticker -> {indexes: [], valuation: {}}

    for index_name, filepath in old_index_files.items():
        if not os.path.exists(filepath):
            continue

        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
        except:
            continue

        tickers = data.get('tickers', [])
        valuations = data.get('valuations', {})

        for ticker in tickers:
            ticker = ticker.upper()
            if ticker not in all_tickers:
                all_tickers[ticker] = {
                    'indexes': [],
                    'valuation': None
                }

            all_tickers[ticker]['indexes'].append(index_name)

            # Use valuation if we don't have one yet
            if ticker in valuations and all_tickers[ticker]['valuation'] is None:
                all_tickers[ticker]['valuation'] = valuations[ticker]

    print(f"[Migration] Found {len(all_tickers)} unique tickers across all indexes")

    # Check SEC status for each ticker
    ticker_updates = {}
    valuation_updates = {}

    for ticker, info in all_tickers.items():
        # Determine SEC status
        sec_status = 'unknown'
        sec_cache = sec_data.load_company_cache(ticker)

        if sec_cache:
            if sec_cache.get('eps_history'):
                sec_status = 'available'
            elif sec_cache.get('sec_no_eps'):
                sec_status = 'unavailable'

        # Get company name
        company_name = None
        if info['valuation']:
            company_name = info['valuation'].get('company_name')
        if not company_name and sec_cache:
            company_name = sec_cache.get('company_name')

        ticker_updates[ticker] = {
            'indexes': info['indexes'],
            'sec_status': sec_status,
            'sec_checked': datetime.now().isoformat() if sec_status != 'unknown' else None,
            'company_name': company_name
        }

        if info['valuation']:
            valuation_updates[ticker] = info['valuation']

    # Save consolidated data
    print(f"[Migration] Saving {len(ticker_updates)} ticker statuses...")
    bulk_update_ticker_status(ticker_updates)

    print(f"[Migration] Saving {len(valuation_updates)} valuations...")
    bulk_update_valuations(valuation_updates)

    print("[Migration] Migration complete!")

    return {
        'tickers_migrated': len(ticker_updates),
        'valuations_migrated': len(valuation_updates)
    }


# --- Index Management ---

def get_index_tickers(index_name: str) -> List[str]:
    """Get list of tickers for an index from status file"""
    status = load_ticker_status()
    return [
        t for t, info in status['tickers'].items()
        if index_name in info.get('indexes', [])
    ]


def sync_index_membership(index_name: str, tickers: List[str]):
    """
    Sync index membership for a list of tickers.
    Adds index to tickers that should have it, creates new ticker entries as needed.
    """
    status = load_ticker_status()

    for ticker in tickers:
        ticker = ticker.upper()
        if ticker not in status['tickers']:
            status['tickers'][ticker] = {
                'ticker': ticker,
                'indexes': [index_name],
                'sec_status': 'unknown',
                'sec_checked': None,
                'valuation_updated': None,
                'company_name': None,
                'updated': datetime.now().isoformat()
            }
        elif index_name not in status['tickers'][ticker].get('indexes', []):
            status['tickers'][ticker]['indexes'].append(index_name)
            status['tickers'][ticker]['updated'] = datetime.now().isoformat()

    save_ticker_status(status)

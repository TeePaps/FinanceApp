"""
SQLite Database Module for the Finance App.

Provides database connection, schema initialization, and CRUD operations.

Two separate databases:
- data_private/private.db: User's personal data (holdings, transactions)
- data_public/public.db: Public market data (SEC, indexes, valuations)

Private Database Tables:
- stocks: User's stock registry
- transactions: User's transaction history

Public Database Tables:
- indexes: Index definitions (sp500, nasdaq100, etc.)
- tickers: Ticker metadata and status
- ticker_indexes: Many-to-many relationship
- valuations: Valuation data per ticker
- sec_companies: SEC company data
- eps_history: EPS history per ticker
- cik_mapping: Ticker to CIK mapping
- ticker_failures: Failed ticker tracking
- sec_filings: 10-K filing URLs
- metadata: Cache/system metadata
"""

import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple, Any
from contextlib import contextmanager

# Database paths
BASE_DIR = os.path.dirname(__file__)
PRIVATE_DB_PATH = os.path.join(BASE_DIR, 'data_private', 'private.db')
PUBLIC_DB_PATH = os.path.join(BASE_DIR, 'data_public', 'public.db')

# Import index definitions from central registry
from services.index_registry import VALID_INDICES, INDIVIDUAL_INDICES, INDEX_NAMES


def _get_connection(db_path: str) -> sqlite3.Connection:
    """Get a database connection with row factory enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_public_db():
    """Context manager for public database connections."""
    conn = _get_connection(PUBLIC_DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_private_db():
    """Context manager for private database connections."""
    conn = _get_connection(PRIVATE_DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Backward compatibility alias - defaults to public database
@contextmanager
def get_db():
    """Context manager for database connections (defaults to public)."""
    with get_public_db() as conn:
        yield conn


def init_database():
    """Initialize both database schemas."""
    _init_public_database()
    _init_private_database()
    print("[Database] Both schemas initialized successfully")


def _init_public_database():
    """Initialize the public database schema."""
    with get_public_db() as conn:
        cursor = conn.cursor()

        # Indexes table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS indexes (
                name TEXT PRIMARY KEY,
                display_name TEXT,
                short_name TEXT,
                enabled INTEGER DEFAULT 1
            )
        ''')

        # Tickers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickers (
                ticker TEXT PRIMARY KEY,
                company_name TEXT,
                sec_status TEXT DEFAULT 'unknown',
                sec_checked TEXT,
                valuation_updated TEXT,
                updated TEXT,
                cik TEXT,
                delisted INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1
            )
        ''')

        # Ticker-Index relationship (many-to-many)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ticker_indexes (
                ticker TEXT,
                index_name TEXT,
                active INTEGER DEFAULT 1,
                PRIMARY KEY (ticker, index_name),
                FOREIGN KEY (ticker) REFERENCES tickers(ticker) ON DELETE CASCADE,
                FOREIGN KEY (index_name) REFERENCES indexes(name) ON DELETE CASCADE
            )
        ''')

        # Valuations table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS valuations (
                ticker TEXT PRIMARY KEY,
                company_name TEXT,
                current_price REAL,
                price_source TEXT,
                eps_avg REAL,
                eps_years INTEGER,
                eps_source TEXT,
                annual_dividend REAL,
                estimated_value REAL,
                price_vs_value REAL,
                fifty_two_week_high REAL,
                fifty_two_week_low REAL,
                off_high_pct REAL,
                price_change_1m REAL,
                price_change_3m REAL,
                in_selloff INTEGER DEFAULT 0,
                selloff_severity TEXT,
                updated TEXT
            )
        ''')

        # SEC Companies table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sec_companies (
                ticker TEXT PRIMARY KEY,
                cik TEXT,
                company_name TEXT,
                sec_no_eps INTEGER DEFAULT 0,
                reason TEXT,
                updated TEXT
            )
        ''')

        # EPS History table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS eps_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                year INTEGER NOT NULL,
                eps REAL,
                filed TEXT,
                period_start TEXT,
                period_end TEXT,
                eps_type TEXT,
                FOREIGN KEY (ticker) REFERENCES sec_companies(ticker) ON DELETE CASCADE,
                UNIQUE(ticker, year)
            )
        ''')

        # CIK Mapping table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cik_mapping (
                ticker TEXT PRIMARY KEY,
                cik TEXT,
                name TEXT,
                updated TEXT
            )
        ''')

        # Ticker Failures table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ticker_failures (
                ticker TEXT PRIMARY KEY,
                failure_count INTEGER DEFAULT 0,
                last_failure TEXT,
                reason TEXT
            )
        ''')

        # Metadata table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated TEXT
            )
        ''')

        # SEC Filings table (10-K document URLs)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sec_filings (
                ticker TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                form_type TEXT NOT NULL,
                accession_number TEXT NOT NULL,
                filing_date TEXT NOT NULL,
                document_url TEXT NOT NULL,
                updated TEXT NOT NULL,
                PRIMARY KEY (ticker, fiscal_year, form_type)
            )
        ''')

        # Create indexes
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ticker_indexes_ticker ON ticker_indexes(ticker)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ticker_indexes_index ON ticker_indexes(index_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_eps_history_ticker ON eps_history(ticker)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_valuations_price_vs_value ON valuations(price_vs_value)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sec_filings_ticker ON sec_filings(ticker)')

        # Migrations - add columns if they don't exist
        cursor.execute('PRAGMA table_info(valuations)')
        existing_columns = {row[1] for row in cursor.fetchall()}
        if 'price_source' not in existing_columns:
            cursor.execute('ALTER TABLE valuations ADD COLUMN price_source TEXT')

        # Add delisted column to tickers if it doesn't exist
        cursor.execute('PRAGMA table_info(tickers)')
        ticker_columns = {row[1] for row in cursor.fetchall()}
        if 'delisted' not in ticker_columns:
            cursor.execute('ALTER TABLE tickers ADD COLUMN delisted INTEGER DEFAULT 0')

        # Add enabled column to tickers if it doesn't exist
        cursor.execute('PRAGMA table_info(tickers)')
        ticker_columns = {row[1] for row in cursor.fetchall()}
        if 'enabled' not in ticker_columns:
            cursor.execute('ALTER TABLE tickers ADD COLUMN enabled INTEGER DEFAULT 1')

        # Add active column to ticker_indexes if it doesn't exist
        cursor.execute('PRAGMA table_info(ticker_indexes)')
        ticker_index_columns = {row[1] for row in cursor.fetchall()}
        if 'active' not in ticker_index_columns:
            cursor.execute('ALTER TABLE ticker_indexes ADD COLUMN active INTEGER DEFAULT 1')

        # Add enabled column to indexes if it doesn't exist
        cursor.execute('PRAGMA table_info(indexes)')
        index_columns = {row[1] for row in cursor.fetchall()}
        if 'enabled' not in index_columns:
            cursor.execute('ALTER TABLE indexes ADD COLUMN enabled INTEGER DEFAULT 1')

        # Insert default indexes
        for name, (display_name, short_name) in INDEX_NAMES.items():
            cursor.execute('''
                INSERT OR IGNORE INTO indexes (name, display_name, short_name)
                VALUES (?, ?, ?)
            ''', (name, display_name, short_name))


def _init_private_database():
    """Initialize the private database schema."""
    with get_private_db() as conn:
        cursor = conn.cursor()

        # Stocks table (user holdings)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stocks (
                ticker TEXT PRIMARY KEY,
                name TEXT,
                type TEXT DEFAULT 'stock'
            )
        ''')

        # Transactions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,
                shares INTEGER,
                price REAL,
                gain_pct REAL,
                date TEXT,
                status TEXT
            )
        ''')

        # Create indexes
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_ticker ON transactions(ticker)')


# =============================================================================
# Ticker Status Operations
# =============================================================================

def get_ticker_info(ticker: str) -> Optional[Dict]:
    """Get status info for a single ticker."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM tickers WHERE ticker = ?', (ticker.upper(),))
        row = cursor.fetchone()
        if not row:
            return None

        result = dict(row)

        # Get indexes for this ticker
        cursor.execute('SELECT index_name FROM ticker_indexes WHERE ticker = ?', (ticker.upper(),))
        result['indexes'] = [r['index_name'] for r in cursor.fetchall()]

        return result


def update_ticker_status(ticker: str, updates: Dict):
    """Update status for a single ticker."""
    ticker = ticker.upper()
    updates['updated'] = datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()

        # Check if ticker exists
        cursor.execute('SELECT ticker FROM tickers WHERE ticker = ?', (ticker,))
        exists = cursor.fetchone() is not None

        if not exists:
            # Insert new ticker
            cursor.execute('''
                INSERT INTO tickers (ticker, company_name, sec_status, sec_checked,
                                    valuation_updated, updated, cik)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                ticker,
                updates.get('company_name'),
                updates.get('sec_status', 'unknown'),
                updates.get('sec_checked'),
                updates.get('valuation_updated'),
                updates['updated'],
                updates.get('cik')
            ))
        else:
            # Build dynamic update query
            set_parts = []
            values = []
            for key, value in updates.items():
                if key != 'indexes' and key != 'ticker':
                    set_parts.append(f"{key} = ?")
                    values.append(value)

            if set_parts:
                values.append(ticker)
                cursor.execute(f'''
                    UPDATE tickers SET {', '.join(set_parts)} WHERE ticker = ?
                ''', values)

        # Handle indexes if provided
        if 'indexes' in updates:
            cursor.execute('DELETE FROM ticker_indexes WHERE ticker = ?', (ticker,))
            for index_name in updates['indexes']:
                cursor.execute('''
                    INSERT OR IGNORE INTO ticker_indexes (ticker, index_name)
                    VALUES (?, ?)
                ''', (ticker, index_name))


def bulk_update_ticker_status(updates: Dict[str, Dict]):
    """Bulk update multiple tickers at once."""
    now = datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()

        for ticker, ticker_updates in updates.items():
            ticker = ticker.upper()
            ticker_updates['updated'] = now

            # Upsert ticker
            cursor.execute('''
                INSERT INTO tickers (ticker, company_name, sec_status, sec_checked,
                                    valuation_updated, updated, cik)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    company_name = COALESCE(excluded.company_name, company_name),
                    sec_status = COALESCE(excluded.sec_status, sec_status),
                    sec_checked = COALESCE(excluded.sec_checked, sec_checked),
                    valuation_updated = COALESCE(excluded.valuation_updated, valuation_updated),
                    updated = excluded.updated,
                    cik = COALESCE(excluded.cik, cik)
            ''', (
                ticker,
                ticker_updates.get('company_name'),
                ticker_updates.get('sec_status'),
                ticker_updates.get('sec_checked'),
                ticker_updates.get('valuation_updated'),
                ticker_updates['updated'],
                ticker_updates.get('cik')
            ))

            # Handle indexes if provided
            if 'indexes' in ticker_updates:
                cursor.execute('DELETE FROM ticker_indexes WHERE ticker = ?', (ticker,))
                for index_name in ticker_updates['indexes']:
                    cursor.execute('''
                        INSERT OR IGNORE INTO ticker_indexes (ticker, index_name)
                        VALUES (?, ?)
                    ''', (ticker, index_name))


def get_tickers_by_status(sec_status: str) -> List[str]:
    """Get all tickers with a specific SEC status."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT ticker FROM tickers WHERE sec_status = ?', (sec_status,))
        return [row['ticker'] for row in cursor.fetchall()]


def get_tickers_needing_sec_check() -> List[str]:
    """Get tickers that haven't been checked for SEC data."""
    return get_tickers_by_status('unknown')


def get_all_tracked_tickers() -> Set[str]:
    """Get all tickers we're tracking."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT ticker FROM tickers')
        return {row['ticker'] for row in cursor.fetchall()}


def get_index_tickers(index_name: str) -> List[str]:
    """Get list of tickers for an index."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT ticker FROM ticker_indexes WHERE index_name = ?
        ''', (index_name,))
        return [row['ticker'] for row in cursor.fetchall()]


def get_all_indexes() -> List[Dict]:
    """Get all indexes with their enabled state."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, display_name, short_name, COALESCE(enabled, 1) as enabled
            FROM indexes
            ORDER BY name
        ''')
        return [dict(row) for row in cursor.fetchall()]


def get_enabled_indexes() -> List[str]:
    """Get list of enabled index names."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name FROM indexes WHERE COALESCE(enabled, 1) = 1
        ''')
        return [row['name'] for row in cursor.fetchall()]


def set_index_enabled(index_name: str, enabled: bool) -> bool:
    """Enable or disable an index. Returns True if successful."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE indexes SET enabled = ? WHERE name = ?
        ''', (1 if enabled else 0, index_name))
        return cursor.rowcount > 0


def set_indexes_enabled(index_states: Dict[str, bool]) -> int:
    """Bulk update index enabled states. Returns count of updated indexes."""
    with get_db() as conn:
        cursor = conn.cursor()
        updated = 0
        for index_name, enabled in index_states.items():
            cursor.execute('''
                UPDATE indexes SET enabled = ? WHERE name = ?
            ''', (1 if enabled else 0, index_name))
            updated += cursor.rowcount
        return updated


def sync_index_membership(index_name: str, tickers: List[str]):
    """Sync index membership for a list of tickers."""
    now = datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()

        for ticker in tickers:
            ticker = ticker.upper()

            # Ensure ticker exists
            cursor.execute('''
                INSERT OR IGNORE INTO tickers (ticker, sec_status, updated)
                VALUES (?, 'unknown', ?)
            ''', (ticker, now))

            # Add index membership
            cursor.execute('''
                INSERT OR IGNORE INTO ticker_indexes (ticker, index_name)
                VALUES (?, ?)
            ''', (ticker, index_name))


def refresh_index_membership(index_name: str, current_tickers: List[str]) -> Dict:
    """
    Refresh index membership from authoritative source.
    - Adds new tickers (active=1)
    - Marks removed tickers (active=0)
    - Does NOT affect tickers.delisted (that's for truly delisted companies)

    Returns dict with 'added', 'removed', 'total' counts.
    """
    current_set = set(t.upper() for t in current_tickers)
    now = datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()

        # Get existing tickers for this index (both active and inactive)
        cursor.execute('SELECT ticker FROM ticker_indexes WHERE index_name = ?', (index_name,))
        existing = set(row['ticker'] for row in cursor.fetchall())

        # Mark removed tickers as inactive in this index
        removed = existing - current_set
        for ticker in removed:
            cursor.execute('''
                UPDATE ticker_indexes SET active = 0
                WHERE ticker = ? AND index_name = ?
            ''', (ticker, index_name))

        # Add/reactivate current tickers
        added = 0
        for ticker in current_set:
            ticker = ticker.upper()
            # Ensure ticker exists in tickers table
            cursor.execute('''
                INSERT OR IGNORE INTO tickers (ticker, sec_status, updated)
                VALUES (?, 'unknown', ?)
            ''', (ticker, now))

            # Add or update index membership (set active=1)
            cursor.execute('''
                INSERT INTO ticker_indexes (ticker, index_name, active)
                VALUES (?, ?, 1)
                ON CONFLICT(ticker, index_name) DO UPDATE SET active = 1
            ''', (ticker, index_name))

            if ticker not in existing:
                added += 1

        return {'added': added, 'removed': len(removed), 'total': len(current_set)}


# =============================================================================
# Valuations Operations
# =============================================================================

def get_valuation(ticker: str) -> Optional[Dict]:
    """Get valuation for a single ticker."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM valuations WHERE ticker = ?', (ticker.upper(),))
        row = cursor.fetchone()
        if row:
            result = dict(row)
            result['in_selloff'] = bool(result['in_selloff'])
            return result
        return None


def update_valuation(ticker: str, valuation: Dict):
    """Update valuation for a single ticker."""
    ticker = ticker.upper()
    valuation['ticker'] = ticker
    valuation['updated'] = datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO valuations (ticker, company_name, current_price, price_source, eps_avg, eps_years,
                                   eps_source, annual_dividend, estimated_value, price_vs_value,
                                   fifty_two_week_high, fifty_two_week_low, off_high_pct,
                                   price_change_1m, price_change_3m, in_selloff, selloff_severity, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                company_name = excluded.company_name,
                current_price = excluded.current_price,
                price_source = excluded.price_source,
                eps_avg = excluded.eps_avg,
                eps_years = excluded.eps_years,
                eps_source = excluded.eps_source,
                annual_dividend = excluded.annual_dividend,
                estimated_value = excluded.estimated_value,
                price_vs_value = excluded.price_vs_value,
                fifty_two_week_high = excluded.fifty_two_week_high,
                fifty_two_week_low = excluded.fifty_two_week_low,
                off_high_pct = excluded.off_high_pct,
                price_change_1m = excluded.price_change_1m,
                price_change_3m = excluded.price_change_3m,
                in_selloff = excluded.in_selloff,
                selloff_severity = excluded.selloff_severity,
                updated = excluded.updated
        ''', (
            ticker,
            valuation.get('company_name'),
            valuation.get('current_price'),
            valuation.get('price_source'),
            valuation.get('eps_avg'),
            valuation.get('eps_years'),
            valuation.get('eps_source'),
            valuation.get('annual_dividend'),
            valuation.get('estimated_value'),
            valuation.get('price_vs_value'),
            valuation.get('fifty_two_week_high'),
            valuation.get('fifty_two_week_low'),
            valuation.get('off_high_pct'),
            valuation.get('price_change_1m'),
            valuation.get('price_change_3m'),
            1 if valuation.get('in_selloff') else 0,
            valuation.get('selloff_severity'),
            valuation['updated']
        ))


def bulk_update_valuations(valuations: Dict[str, Dict]):
    """Bulk update multiple valuations at once."""
    now = datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()

        for ticker, valuation in valuations.items():
            ticker = ticker.upper()
            valuation['ticker'] = ticker
            valuation['updated'] = now

            cursor.execute('''
                INSERT INTO valuations (ticker, company_name, current_price, price_source, eps_avg, eps_years,
                                       eps_source, annual_dividend, estimated_value, price_vs_value,
                                       fifty_two_week_high, fifty_two_week_low, off_high_pct,
                                       price_change_1m, price_change_3m, in_selloff, selloff_severity, updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    company_name = excluded.company_name,
                    current_price = excluded.current_price,
                    price_source = excluded.price_source,
                    eps_avg = excluded.eps_avg,
                    eps_years = excluded.eps_years,
                    eps_source = excluded.eps_source,
                    annual_dividend = excluded.annual_dividend,
                    estimated_value = excluded.estimated_value,
                    price_vs_value = excluded.price_vs_value,
                    fifty_two_week_high = excluded.fifty_two_week_high,
                    fifty_two_week_low = excluded.fifty_two_week_low,
                    off_high_pct = excluded.off_high_pct,
                    price_change_1m = excluded.price_change_1m,
                    price_change_3m = excluded.price_change_3m,
                    in_selloff = excluded.in_selloff,
                    selloff_severity = excluded.selloff_severity,
                    updated = excluded.updated
            ''', (
                ticker,
                valuation.get('company_name'),
                valuation.get('current_price'),
                valuation.get('price_source'),
                valuation.get('eps_avg'),
                valuation.get('eps_years'),
                valuation.get('eps_source'),
                valuation.get('annual_dividend'),
                valuation.get('estimated_value'),
                valuation.get('price_vs_value'),
                valuation.get('fifty_two_week_high'),
                valuation.get('fifty_two_week_low'),
                valuation.get('off_high_pct'),
                valuation.get('price_change_1m'),
                valuation.get('price_change_3m'),
                1 if valuation.get('in_selloff') else 0,
                valuation.get('selloff_severity'),
                valuation['updated']
            ))


def get_valuations_for_index(index_name: str) -> List[Dict]:
    """Get valuations for tickers in a specific index."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT v.* FROM valuations v
            JOIN ticker_indexes ti ON v.ticker = ti.ticker
            WHERE ti.index_name = ?
        ''', (index_name,))
        results = []
        for row in cursor.fetchall():
            result = dict(row)
            result['in_selloff'] = bool(result['in_selloff'])
            results.append(result)
        return results


def get_all_valuations() -> Dict[str, Dict]:
    """Get all valuations as a dictionary keyed by ticker."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM valuations')
        result = {}
        for row in cursor.fetchall():
            val = dict(row)
            val['in_selloff'] = bool(val['in_selloff'])
            result[val['ticker']] = val
        return result


def get_undervalued_tickers(threshold: float = -20.0) -> List[Dict]:
    """Get all tickers that are undervalued by more than threshold %."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM valuations
            WHERE price_vs_value IS NOT NULL AND price_vs_value < ?
            ORDER BY price_vs_value ASC
        ''', (threshold,))
        results = []
        for row in cursor.fetchall():
            result = dict(row)
            result['in_selloff'] = bool(result['in_selloff'])
            results.append(result)
        return results


def get_orphan_tickers() -> List[str]:
    """
    Get tickers that have valuations but are not active members of any index.

    An orphan is a ticker with a valuation record where:
    - It has no entries in ticker_indexes at all, OR
    - All its ticker_indexes entries have active=0
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT v.ticker FROM valuations v
            WHERE NOT EXISTS (
                SELECT 1 FROM ticker_indexes ti
                WHERE ti.ticker = v.ticker AND ti.active = 1
            )
            ORDER BY v.ticker
        ''')
        return [row['ticker'] for row in cursor.fetchall()]


def remove_orphan_valuations() -> Dict:
    """
    Remove valuations for tickers that are not active members of any index.

    Also removes related data:
    - eps_history entries
    - sec_companies entries
    - ticker_indexes entries (inactive ones)
    - tickers entries (if no other references)

    Returns dict with counts of removed records.
    """
    orphans = get_orphan_tickers()
    if not orphans:
        return {'orphans_found': 0, 'valuations_removed': 0, 'eps_removed': 0,
                'sec_companies_removed': 0, 'ticker_indexes_removed': 0, 'tickers_removed': 0}

    with get_db() as conn:
        cursor = conn.cursor()

        # Count before deletion for reporting
        valuations_count = len(orphans)

        # Delete EPS history for orphans
        cursor.execute(f'''
            DELETE FROM eps_history WHERE ticker IN ({','.join('?' * len(orphans))})
        ''', orphans)
        eps_count = cursor.rowcount

        # Delete SEC companies for orphans
        cursor.execute(f'''
            DELETE FROM sec_companies WHERE ticker IN ({','.join('?' * len(orphans))})
        ''', orphans)
        sec_count = cursor.rowcount

        # Delete valuations for orphans
        cursor.execute(f'''
            DELETE FROM valuations WHERE ticker IN ({','.join('?' * len(orphans))})
        ''', orphans)

        # Delete inactive ticker_indexes entries for orphans
        cursor.execute(f'''
            DELETE FROM ticker_indexes WHERE ticker IN ({','.join('?' * len(orphans))})
            AND (active = 0 OR active IS NULL)
        ''', orphans)
        ti_count = cursor.rowcount

        # Delete tickers that have no remaining references
        # (not in any active index, no valuations, no holdings)
        cursor.execute(f'''
            DELETE FROM tickers WHERE ticker IN ({','.join('?' * len(orphans))})
            AND NOT EXISTS (
                SELECT 1 FROM ticker_indexes ti WHERE ti.ticker = tickers.ticker
            )
        ''', orphans)
        tickers_count = cursor.rowcount

        return {
            'orphans_found': len(orphans),
            'valuations_removed': valuations_count,
            'eps_removed': eps_count,
            'sec_companies_removed': sec_count,
            'ticker_indexes_removed': ti_count,
            'tickers_removed': tickers_count
        }


# =============================================================================
# SEC Company Operations
# =============================================================================

def get_sec_company(ticker: str) -> Optional[Dict]:
    """Get SEC company data for a ticker."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM sec_companies WHERE ticker = ?', (ticker.upper(),))
        row = cursor.fetchone()
        if not row:
            return None

        result = dict(row)
        result['sec_no_eps'] = bool(result['sec_no_eps'])

        # Get EPS history
        cursor.execute('''
            SELECT year, eps, filed, period_start, period_end, eps_type
            FROM eps_history WHERE ticker = ?
            ORDER BY year DESC
        ''', (ticker.upper(),))
        result['eps_history'] = [dict(r) for r in cursor.fetchall()]

        return result


def save_sec_company(ticker: str, data: Dict):
    """Save SEC company data for a ticker."""
    ticker = ticker.upper()

    with get_db() as conn:
        cursor = conn.cursor()

        # Upsert company record
        cursor.execute('''
            INSERT INTO sec_companies (ticker, cik, company_name, sec_no_eps, reason, updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                cik = excluded.cik,
                company_name = excluded.company_name,
                sec_no_eps = excluded.sec_no_eps,
                reason = excluded.reason,
                updated = excluded.updated
        ''', (
            ticker,
            data.get('cik'),
            data.get('company_name'),
            1 if data.get('sec_no_eps') else 0,
            data.get('reason'),
            data.get('updated', datetime.now().isoformat())
        ))

        # Update EPS history
        if 'eps_history' in data:
            cursor.execute('DELETE FROM eps_history WHERE ticker = ?', (ticker,))
            for eps in data['eps_history']:
                cursor.execute('''
                    INSERT INTO eps_history (ticker, year, eps, filed, period_start, period_end, eps_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    ticker,
                    eps.get('year'),
                    eps.get('eps'),
                    eps.get('filed'),
                    eps.get('start') or eps.get('period_start'),
                    eps.get('end') or eps.get('period_end'),
                    eps.get('eps_type')
                ))


def has_sec_eps(ticker: str) -> bool:
    """Check if we have any SEC EPS data for a ticker."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) as count FROM eps_history WHERE ticker = ?
        ''', (ticker.upper(),))
        return cursor.fetchone()['count'] > 0


def get_sec_company_count() -> int:
    """Get count of SEC companies in database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM sec_companies')
        return cursor.fetchone()['count']


def delete_sec_company(ticker: str):
    """Delete SEC company data for a ticker (for force refresh)."""
    ticker = ticker.upper()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM eps_history WHERE ticker = ?', (ticker,))
        cursor.execute('DELETE FROM sec_companies WHERE ticker = ?', (ticker,))
        conn.commit()


def add_new_eps_years(ticker: str, eps_list: list) -> int:
    """
    Add only NEW EPS years that don't exist in the database.
    Returns the count of new years added.
    """
    ticker = ticker.upper()
    added = 0

    with get_db() as conn:
        cursor = conn.cursor()

        for eps in eps_list:
            # Try to insert, ignore if year already exists
            cursor.execute('''
                INSERT OR IGNORE INTO eps_history
                (ticker, year, eps, filed, period_start, period_end, eps_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                ticker,
                eps.get('year'),
                eps.get('eps'),
                eps.get('filed'),
                eps.get('start') or eps.get('period_start'),
                eps.get('end') or eps.get('period_end'),
                eps.get('eps_type')
            ))
            if cursor.rowcount > 0:
                added += 1

        # Update the company's updated timestamp
        cursor.execute('''
            UPDATE sec_companies SET updated = ? WHERE ticker = ?
        ''', (datetime.now().isoformat(), ticker))

        conn.commit()

    return added


# =============================================================================
# SEC Filings Operations (10-K document URLs)
# =============================================================================

def get_sec_filings(ticker: str) -> List[Dict]:
    """Get SEC 10-K filing URLs for a ticker."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT fiscal_year, form_type, accession_number, filing_date, document_url, updated
            FROM sec_filings WHERE ticker = ?
            ORDER BY fiscal_year DESC
        ''', (ticker.upper(),))
        return [dict(row) for row in cursor.fetchall()]


def save_sec_filings(ticker: str, filings: List[Dict]):
    """Save SEC 10-K filing URLs for a ticker."""
    ticker = ticker.upper()
    now = datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()

        # Clear existing filings for this ticker
        cursor.execute('DELETE FROM sec_filings WHERE ticker = ?', (ticker,))

        # Insert new filings
        for filing in filings:
            cursor.execute('''
                INSERT INTO sec_filings (ticker, fiscal_year, form_type, accession_number,
                                        filing_date, document_url, updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                ticker,
                filing.get('fiscal_year'),
                filing.get('form_type'),
                filing.get('accession_number'),
                filing.get('filing_date'),
                filing.get('document_url'),
                now
            ))


def get_sec_filings_last_updated(ticker: str) -> Optional[str]:
    """Get the last update timestamp for a ticker's filings."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT MAX(updated) as latest FROM sec_filings WHERE ticker = ?
        ''', (ticker.upper(),))
        row = cursor.fetchone()
        return row['latest'] if row else None


def get_latest_filing_year(ticker: str) -> Optional[int]:
    """Get the most recent fiscal year we have a filing for."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT MAX(fiscal_year) as latest_year FROM sec_filings WHERE ticker = ?
        ''', (ticker.upper(),))
        row = cursor.fetchone()
        return row['latest_year'] if row else None


# =============================================================================
# CIK Mapping Operations
# =============================================================================

def get_cik_mapping() -> Dict:
    """Get all CIK mappings."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT ticker, cik, name, updated FROM cik_mapping')
        tickers = {row['ticker']: {'cik': row['cik'], 'name': row['name']} for row in cursor.fetchall()}

        # Get the latest update timestamp
        cursor.execute('SELECT MAX(updated) as latest FROM cik_mapping')
        latest = cursor.fetchone()['latest']

        return {
            'tickers': tickers,
            'updated': latest,
            'count': len(tickers)
        }


def save_cik_mapping(data: Dict):
    """Save CIK mapping data."""
    now = datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()

        for ticker, info in data.get('tickers', {}).items():
            cursor.execute('''
                INSERT INTO cik_mapping (ticker, cik, name, updated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    cik = excluded.cik,
                    name = excluded.name,
                    updated = excluded.updated
            ''', (ticker, info.get('cik'), info.get('name'), now))


def get_cik_for_ticker(ticker: str) -> Optional[str]:
    """Get CIK for a specific ticker."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT cik FROM cik_mapping WHERE ticker = ?', (ticker.upper(),))
        row = cursor.fetchone()
        return row['cik'] if row else None


# =============================================================================
# Ticker Failures Operations
# =============================================================================

def get_ticker_failure(ticker: str) -> Optional[Dict]:
    """Get failure info for a ticker."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM ticker_failures WHERE ticker = ?', (ticker.upper(),))
        row = cursor.fetchone()
        return dict(row) if row else None


def record_ticker_failure(ticker: str, reason: str = None):
    """Record a failure for a ticker."""
    ticker = ticker.upper()
    now = datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO ticker_failures (ticker, failure_count, last_failure, reason)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                failure_count = failure_count + 1,
                last_failure = excluded.last_failure,
                reason = COALESCE(excluded.reason, reason)
        ''', (ticker, now, reason))


def clear_ticker_failure(ticker: str):
    """Clear failure record for a ticker."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM ticker_failures WHERE ticker = ?', (ticker.upper(),))


def get_excluded_tickers(threshold: int = 3) -> List[str]:
    """Get tickers that have exceeded the failure threshold."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT ticker FROM ticker_failures WHERE failure_count >= ?
        ''', (threshold,))
        return [row['ticker'] for row in cursor.fetchall()]


def mark_ticker_delisted(ticker: str, delisted: bool = True):
    """Mark a ticker as delisted (or not)."""
    ticker = ticker.upper()
    with get_db() as conn:
        cursor = conn.cursor()
        # Ensure ticker exists in tickers table first
        cursor.execute('''
            INSERT OR IGNORE INTO tickers (ticker, delisted) VALUES (?, ?)
        ''', (ticker, 1 if delisted else 0))
        cursor.execute('''
            UPDATE tickers SET delisted = ? WHERE ticker = ?
        ''', (1 if delisted else 0, ticker))


def mark_tickers_delisted(tickers: List[str], delisted: bool = True):
    """Mark multiple tickers as delisted (or not)."""
    with get_db() as conn:
        cursor = conn.cursor()
        for ticker in tickers:
            ticker = ticker.upper()
            cursor.execute('''
                INSERT OR IGNORE INTO tickers (ticker, delisted) VALUES (?, ?)
            ''', (ticker, 1 if delisted else 0))
            cursor.execute('''
                UPDATE tickers SET delisted = ? WHERE ticker = ?
            ''', (1 if delisted else 0, ticker))


def is_ticker_delisted(ticker: str) -> bool:
    """Check if a ticker is marked as delisted."""
    ticker = ticker.upper()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT delisted FROM tickers WHERE ticker = ?', (ticker,))
        row = cursor.fetchone()
        return bool(row and row['delisted'])


def get_delisted_tickers() -> List[str]:
    """Get all tickers marked as delisted."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT ticker FROM tickers WHERE delisted = 1')
        return [row['ticker'] for row in cursor.fetchall()]


def get_active_index_tickers(index_name: str) -> List[str]:
    """Get tickers for an index, excluding delisted and disabled ones."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT ti.ticker FROM ticker_indexes ti
            LEFT JOIN tickers t ON ti.ticker = t.ticker
            WHERE ti.index_name = ?
              AND (ti.active IS NULL OR ti.active = 1)
              AND (t.delisted IS NULL OR t.delisted = 0)
              AND (t.enabled IS NULL OR t.enabled = 1)
            ORDER BY ti.ticker
        ''', (index_name,))
        return [row['ticker'] for row in cursor.fetchall()]


# =============================================================================
# Ticker Enabled/Disabled Operations
# =============================================================================

def get_enabled_tickers() -> List[str]:
    """Get list of ticker symbols that are enabled."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT ticker FROM tickers
            WHERE (enabled IS NULL OR enabled = 1)
              AND (delisted IS NULL OR delisted = 0)
            ORDER BY ticker
        ''')
        return [row['ticker'] for row in cursor.fetchall()]


def get_disabled_ticker_count() -> int:
    """Get count of disabled tickers."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM tickers WHERE enabled = 0')
        return cursor.fetchone()['count']


def is_ticker_enabled(symbol: str) -> bool:
    """Check if a specific ticker is enabled."""
    symbol = symbol.upper()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT enabled FROM tickers WHERE ticker = ?
        ''', (symbol,))
        row = cursor.fetchone()
        # If ticker not found or enabled is NULL, assume enabled
        if not row or row['enabled'] is None:
            return True
        return bool(row['enabled'])


def recalculate_ticker_enabled_states() -> Dict[str, int]:
    """
    Recalculate enabled state for all tickers based on their index membership.

    A ticker is enabled if:
    - It belongs to at least one ENABLED index (with active membership), OR
    - It doesn't belong to any index (manually added stocks)

    A ticker is disabled if:
    - It ONLY belongs to DISABLED indexes

    Returns dict with 'enabled' and 'disabled' counts.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # First, get all enabled index names
        cursor.execute('SELECT name FROM indexes WHERE COALESCE(enabled, 1) = 1')
        enabled_indexes = {row['name'] for row in cursor.fetchall()}

        # Get all tickers and their active index memberships
        cursor.execute('''
            SELECT t.ticker,
                   GROUP_CONCAT(ti.index_name) as indexes
            FROM tickers t
            LEFT JOIN ticker_indexes ti ON t.ticker = ti.ticker
                AND (ti.active IS NULL OR ti.active = 1)
            GROUP BY t.ticker
        ''')

        enabled_count = 0
        disabled_count = 0

        for row in cursor.fetchall():
            ticker = row['ticker']
            indexes_str = row['indexes']

            if not indexes_str:
                # Ticker not in any index - keep enabled (manually added)
                cursor.execute('UPDATE tickers SET enabled = 1 WHERE ticker = ?', (ticker,))
                enabled_count += 1
            else:
                # Check if any of the ticker's indexes are enabled
                ticker_indexes = set(indexes_str.split(','))
                has_enabled_index = bool(ticker_indexes & enabled_indexes)

                if has_enabled_index:
                    cursor.execute('UPDATE tickers SET enabled = 1 WHERE ticker = ?', (ticker,))
                    enabled_count += 1
                else:
                    cursor.execute('UPDATE tickers SET enabled = 0 WHERE ticker = ?', (ticker,))
                    disabled_count += 1

        return {'enabled': enabled_count, 'disabled': disabled_count}


# =============================================================================
# User Holdings Operations (Stocks & Transactions) - Uses PRIVATE database
# =============================================================================

def get_stocks() -> List[Dict]:
    """Get all user stocks."""
    with get_private_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM stocks ORDER BY ticker')
        return [dict(row) for row in cursor.fetchall()]


def add_stock(ticker: str, name: str, stock_type: str = 'stock'):
    """Add a stock to the registry."""
    with get_private_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO stocks (ticker, name, type)
            VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                name = excluded.name,
                type = excluded.type
        ''', (ticker.upper(), name, stock_type))


def remove_stock(ticker: str):
    """Remove a stock from the registry."""
    with get_private_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM stocks WHERE ticker = ?', (ticker.upper(),))


def get_transactions() -> List[Dict]:
    """Get all user transactions."""
    with get_private_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM transactions ORDER BY id')
        return [dict(row) for row in cursor.fetchall()]


def get_transactions_for_ticker(ticker: str) -> List[Dict]:
    """Get transactions for a specific ticker."""
    with get_private_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM transactions WHERE ticker = ? ORDER BY id', (ticker.upper(),))
        return [dict(row) for row in cursor.fetchall()]


def add_transaction(ticker: str, action: str, shares: int, price: float,
                   gain_pct: float = None, date: str = None, status: str = None) -> int:
    """Add a transaction and return its ID."""
    with get_private_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO transactions (ticker, action, shares, price, gain_pct, date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (ticker.upper(), action, shares, price, gain_pct, date, status))
        return cursor.lastrowid


def update_transaction(txn_id: int, updates: Dict):
    """Update a transaction."""
    with get_private_db() as conn:
        cursor = conn.cursor()
        set_parts = []
        values = []
        for key, value in updates.items():
            if key != 'id':
                set_parts.append(f"{key} = ?")
                values.append(value)

        if set_parts:
            values.append(txn_id)
            cursor.execute(f'''
                UPDATE transactions SET {', '.join(set_parts)} WHERE id = ?
            ''', values)


def delete_transaction(txn_id: int):
    """Delete a transaction."""
    with get_private_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM transactions WHERE id = ?', (txn_id,))


def get_next_transaction_id() -> int:
    """Get the next available transaction ID."""
    with get_private_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(id) as max_id FROM transactions')
        row = cursor.fetchone()
        return (row['max_id'] or 0) + 1


# =============================================================================
# Metadata Operations
# =============================================================================

def get_metadata(key: str) -> Optional[str]:
    """Get a metadata value."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM metadata WHERE key = ?', (key,))
        row = cursor.fetchone()
        return row['value'] if row else None


def set_metadata(key: str, value: str):
    """Set a metadata value."""
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO metadata (key, value, updated)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated = excluded.updated
        ''', (key, value, now))


# =============================================================================
# Statistics
# =============================================================================

def get_data_stats() -> Dict:
    """Get comprehensive statistics about the data."""
    with get_db() as conn:
        cursor = conn.cursor()

        # Count tickers by SEC status
        cursor.execute('SELECT sec_status, COUNT(*) as count FROM tickers GROUP BY sec_status')
        status_counts = {row['sec_status']: row['count'] for row in cursor.fetchall()}

        # Count by index
        cursor.execute('''
            SELECT index_name, COUNT(*) as count
            FROM ticker_indexes
            GROUP BY index_name
        ''')
        index_counts = {row['index_name']: row['count'] for row in cursor.fetchall()}

        # Total tickers
        cursor.execute('SELECT COUNT(*) as count FROM tickers')
        total_tickers = cursor.fetchone()['count']

        # Valuations with data
        cursor.execute('SELECT COUNT(*) as count FROM valuations WHERE estimated_value IS NOT NULL')
        with_valuation = cursor.fetchone()['count']

        # Get last updated timestamps
        cursor.execute('SELECT MAX(updated) as latest FROM tickers')
        status_updated = cursor.fetchone()['latest']

        cursor.execute('SELECT MAX(updated) as latest FROM valuations')
        valuations_updated = cursor.fetchone()['latest']

        return {
            'total_tickers': total_tickers,
            'sec_available': status_counts.get('available', 0),
            'sec_unavailable': status_counts.get('unavailable', 0),
            'sec_unknown': status_counts.get('unknown', 0),
            'index_counts': index_counts,
            'with_valuation': with_valuation,
            'status_last_updated': status_updated,
            'valuations_last_updated': valuations_updated
        }


# Initialize databases on import if needed
if not os.path.exists(PUBLIC_DB_PATH) or not os.path.exists(PRIVATE_DB_PATH):
    init_database()

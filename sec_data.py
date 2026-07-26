"""
SEC EDGAR Data Module
Handles fetching and caching of SEC financial data (EPS, etc.)

This module now uses SQLite database for all data storage.
Data is stored in the following tables:
- sec_companies: Company info and metadata
- eps_history: EPS records per company
- cik_mapping: Ticker to CIK mapping
"""
import os
import time
import requests
from collections import OrderedDict
from datetime import datetime, timedelta
import threading

# Import database module for all operations
import database as db
from config import (
    SEC_RATE_LIMIT, SEC_REQUEST_TIMEOUT,
    SEC_CIK_CACHE_DAYS, SEC_EPS_CACHE_DAYS,
    SEC_EPS_FILING_LAG_DAYS, SEC_EPS_RECHECK_DAYS, SEC_EPS_MAX_AGE_DAYS,
    SEC_EPS_NO_DATA_RECHECK_DAYS
)

# SEC requires User-Agent with contact info
SEC_HEADERS = {'User-Agent': 'FinanceApp contact@example.com'}


def _build_sec_session():
    """One pooled, retrying HTTPS session for all SEC traffic.

    Every SEC call used a bare requests.get(), so each one paid a fresh TCP +
    TLS handshake and rebuilt an SSL context from the certifi bundle - for a
    full-universe EPS refresh that is hundreds of handshakes against a host we
    talk to continuously. A Session keeps the connection alive and adds
    backoff for SEC's rate-limit responses.
    """
    session = requests.Session()
    session.headers.update(SEC_HEADERS)
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(['GET']),
        )
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=retry)
        session.mount('https://', adapter)
    except Exception:
        pass  # Plain session still beats a new connection per request
    return session


_SEC_SESSION = _build_sec_session()

# Use config values (aliased for backward compatibility)
CIK_CACHE_DAYS = SEC_CIK_CACHE_DAYS
EPS_CACHE_DAYS = SEC_EPS_CACHE_DAYS

# Module state
sec_update_running = False
sec_update_progress = {'current': 0, 'total': 0, 'ticker': '', 'status': 'idle'}
last_sec_request = 0
_rate_limit_lock = threading.Lock()


def rate_limit():
    """Ensure we don't exceed SEC's 10 req/sec limit.

    SEC fetches run concurrently from the background updater thread AND from
    Flask request handlers, so the read-sleep-write of last_sec_request must
    be atomic — otherwise two threads read the same timestamp, both decide
    enough time has passed, and fire simultaneously, bursting past the limit
    and risking a 10-minute SEC ban. Reserve the next slot under the lock,
    then sleep (outside the lock so other threads queue in order).
    """
    global last_sec_request
    with _rate_limit_lock:
        now = time.time()
        wait = SEC_RATE_LIMIT - (now - last_sec_request)
        # Reserve our slot: the next caller spaces off OUR scheduled time,
        # not the current clock, so N threads serialize to N*SEC_RATE_LIMIT.
        scheduled = now + wait if wait > 0 else now
        last_sec_request = scheduled
    if wait > 0:
        time.sleep(wait)


# --- Metadata ---

def load_metadata():
    """Load cache metadata from database"""
    version = db.get_metadata('sec_cache_version')
    last_update = db.get_metadata('sec_last_full_update')
    return {
        'version': int(version) if version else 2,
        'last_full_update': last_update
    }


def save_metadata(data):
    """Save cache metadata to database"""
    if 'version' in data:
        db.set_metadata('sec_cache_version', str(data['version']))
    if 'last_full_update' in data:
        db.set_metadata('sec_last_full_update', data['last_full_update'])


# --- CIK Mapping ---

def load_cik_mapping():
    """Load cached ticker->CIK mapping from database"""
    return db.get_cik_mapping()


def save_cik_mapping(data):
    """Save ticker->CIK mapping to database"""
    db.save_cik_mapping(data)


def update_cik_mapping():
    """Fetch fresh ticker->CIK mapping from SEC"""
    try:
        rate_limit()
        url = "https://www.sec.gov/files/company_tickers.json"
        response = _SEC_SESSION.get(url, timeout=SEC_REQUEST_TIMEOUT)

        if response.status_code == 200:
            raw_data = response.json()

            # Build ticker -> CIK lookup
            tickers = {}
            for key, company in raw_data.items():
                ticker = company['ticker']
                tickers[ticker] = {
                    'cik': str(company['cik_str']).zfill(10),
                    'name': company['title']
                }

            mapping = {
                'tickers': tickers,
                'updated': datetime.now().isoformat(),
                'count': len(tickers)
            }
            save_cik_mapping(mapping)
            print(f"[SEC] Updated CIK mapping: {len(tickers)} tickers")
            return mapping
    except Exception as e:
        print(f"[SEC] Error updating CIK mapping: {e}")

    return load_cik_mapping()


# Freshness of the CIK mapping is a property of the whole table, so it is
# checked once per process window rather than on every ticker lookup.
_CIK_FRESHNESS_CHECKED_AT = 0.0
_CIK_FRESHNESS_TTL = 300  # seconds


def _ensure_cik_mapping_fresh():
    """Refresh the ticker->CIK table if it has aged out.

    Checked at most once per _CIK_FRESHNESS_TTL: this used to run on every
    single-ticker lookup, and each check loaded all ~12,000 mapping rows plus
    a MAX() scan.
    """
    global _CIK_FRESHNESS_CHECKED_AT

    if time.time() - _CIK_FRESHNESS_CHECKED_AT < _CIK_FRESHNESS_TTL:
        return
    _CIK_FRESHNESS_CHECKED_AT = time.time()

    latest = db.get_cik_mapping_last_updated()
    if not latest:
        if db.get_cik_mapping_count() == 0:
            update_cik_mapping()
        return

    try:
        if datetime.now() - datetime.fromisoformat(latest) > timedelta(days=CIK_CACHE_DAYS):
            update_cik_mapping()
    except (ValueError, TypeError):
        update_cik_mapping()


def get_cik_for_ticker(ticker):
    """Get CIK for a ticker, updating mapping if needed"""
    _ensure_cik_mapping_fresh()
    return db.get_cik_for_ticker(ticker)


# --- Company EPS Data (now stored in database) ---

def load_company_cache(ticker):
    """Load cached data for a single company from database"""
    return db.get_sec_company(ticker)


def save_company_cache(ticker, data):
    """Save data for a single company to database"""
    db.save_sec_company(ticker, data)


def fetch_company_eps(ticker, cik):
    """Fetch EPS data from SEC EDGAR for a company. Returns the data or None."""
    data, _status = _fetch_company_eps_with_status(ticker, cik)
    return data


def _fetch_company_eps_with_status(ticker, cik):
    """Fetch EPS data from SEC EDGAR, reporting WHY it came back empty.

    Returns (data, status) where status is one of:
      'ok'      - EPS extracted
      'no_eps'  - SEC answered, but this filer publishes no usable annual EPS
      'error'   - transport/HTTP failure, nothing learned about the company

    The distinction matters for caching: 'no_eps' is a durable fact worth
    persisting so the ticker isn't re-downloaded every run, while 'error' must
    not be recorded as knowledge.
    """
    try:
        # Shared with the other companyfacts parsers via the per-CIK memo, so
        # the document is downloaded once per ticker rather than once per
        # parser (see _fetch_companyfacts).
        data = _fetch_companyfacts(cik)

        if data is not None:
            us_gaap = data.get('facts', {}).get('us-gaap', {})

            # EPS fields to extract, in order of preference (most specific to least)
            eps_fields = [
                ('EarningsPerShareDiluted', 'Diluted EPS'),
                ('EarningsPerShareBasic', 'Basic EPS'),
                ('IncomeLossFromContinuingOperationsPerDilutedShare', 'Continuing Ops (Diluted)'),
                ('IncomeLossFromContinuingOperationsPerBasicShare', 'Continuing Ops (Basic)'),
            ]

            def extract_annual_eps(field_name, label):
                """Extract annual EPS from 10-K filings for a given field"""
                if field_name not in us_gaap:
                    return {}
                eps_records = us_gaap[field_name].get('units', {}).get('USD/shares', [])
                annual = {}
                for r in eps_records:
                    if r.get('form') == '10-K':
                        frame = r.get('frame', '')
                        # Full year records (not quarterly) - frame like "CY2024" not "CY2024Q1"
                        if frame and 'Q' not in frame:
                            # Extract year from frame (e.g., "CY2024" -> 2024)
                            try:
                                year = int(frame.replace('CY', ''))
                            except ValueError:
                                continue
                            # Keep latest filing for each calendar year (most up-to-date data)
                            if year not in annual or r.get('filed', '') > annual[year].get('filed', ''):
                                annual[year] = {
                                    'year': year,
                                    'eps': r['val'],
                                    'filed': r.get('filed'),
                                    'start': r.get('start'),
                                    'end': r.get('end'),
                                    'eps_type': label
                                }
                return annual

            # Extract all EPS types from US-GAAP
            all_eps_data = {}
            for field_name, label in eps_fields:
                eps_data = extract_annual_eps(field_name, label)
                for year, data in eps_data.items():
                    if year not in all_eps_data:
                        all_eps_data[year] = []
                    all_eps_data[year].append(data)

            # Fallback 1: Check IFRS section for foreign companies
            if not all_eps_data:
                ifrs = data.get('facts', {}).get('ifrs-full', {})
                if ifrs:
                    ifrs_eps_fields = [
                        ('DilutedEarningsLossPerShare', 'Diluted EPS (IFRS)'),
                        ('BasicEarningsLossPerShare', 'Basic EPS (IFRS)'),
                        ('BasicAndDilutedEarningsLossPerShare', 'EPS (IFRS)'),
                    ]

                    def extract_ifrs_eps(field_name, label):
                        """Extract annual EPS from IFRS filings (20-F, 40-F, or 10-K)"""
                        if field_name not in ifrs:
                            return {}
                        # Check both USD/shares and other currencies
                        units = ifrs[field_name].get('units', {})
                        # Prefer USD, but accept EUR/GBP if USD not available
                        for unit_key in ['USD/shares', 'EUR/shares', 'GBP/shares']:
                            if unit_key in units:
                                eps_records = units[unit_key]
                                annual = {}
                                for r in eps_records:
                                    form = r.get('form', '')
                                    # Accept 20-F, 40-F (foreign filer annual) or 10-K
                                    if form in ['20-F', '40-F', '10-K']:
                                        frame = r.get('frame', '')
                                        if frame and 'Q' not in frame:
                                            try:
                                                year = int(frame.replace('CY', ''))
                                            except ValueError:
                                                continue
                                            if year not in annual or r.get('filed', '') > annual[year].get('filed', ''):
                                                currency = unit_key.split('/')[0]
                                                annual[year] = {
                                                    'year': year,
                                                    'eps': r['val'],
                                                    'filed': r.get('filed'),
                                                    'start': r.get('start'),
                                                    'end': r.get('end'),
                                                    'eps_type': f"{label} ({currency})"
                                                }
                                if annual:
                                    return annual
                        return {}

                    for field_name, label in ifrs_eps_fields:
                        eps_data = extract_ifrs_eps(field_name, label)
                        for year, eps_entry in eps_data.items():
                            if year not in all_eps_data:
                                all_eps_data[year] = []
                            all_eps_data[year].append(eps_entry)

            # Fallback 2: Calculate EPS from Net Income / Shares Outstanding
            if not all_eps_data:
                net_income = us_gaap.get('NetIncomeLoss', {}).get('units', {}).get('USD', [])
                # Try multiple share fields
                shares = None
                for share_field in ['CommonStockSharesOutstanding',
                                    'WeightedAverageNumberOfDilutedSharesOutstanding',
                                    'WeightedAverageNumberOfSharesOutstandingBasic']:
                    if share_field in us_gaap:
                        shares = us_gaap[share_field].get('units', {}).get('shares', [])
                        if shares:
                            break

                if net_income and shares:
                    # Build year-indexed dicts
                    ni_by_year = {}
                    for r in net_income:
                        if r.get('form') == '10-K' and r.get('fy'):
                            fy = r['fy']
                            if fy not in ni_by_year or r.get('filed', '') > ni_by_year[fy].get('filed', ''):
                                ni_by_year[fy] = r

                    sh_by_year = {}
                    for r in shares:
                        if r.get('form') == '10-K' and r.get('fy'):
                            fy = r['fy']
                            if fy not in sh_by_year or r.get('filed', '') > sh_by_year[fy].get('filed', ''):
                                sh_by_year[fy] = r

                    # Calculate EPS for years with both values
                    for year in set(ni_by_year.keys()) & set(sh_by_year.keys()):
                        ni_val = ni_by_year[year]['val']
                        sh_val = sh_by_year[year]['val']
                        if sh_val and sh_val > 0:
                            calculated_eps = ni_val / sh_val
                            if year not in all_eps_data:
                                all_eps_data[year] = []
                            all_eps_data[year].append({
                                'year': year,
                                'eps': round(calculated_eps, 2),
                                'filed': ni_by_year[year].get('filed'),
                                'start': ni_by_year[year].get('start'),
                                'end': ni_by_year[year].get('end'),
                                'eps_type': 'Calculated (NI/Shares)'
                            })

            if not all_eps_data:
                return None, 'no_eps'

            # Sanity check and correction for EPS values
            # Some companies (e.g., HAL) have XBRL filing errors where EPS is 1,000,000x too high.
            # The two thresholds are now contiguous: anything below the scale-error floor is
            # KEPT (Berkshire A shares legitimately post EPS in the tens of thousands — the old
            # 1000 cap silently dropped exactly those years and biased the 8-year average), and
            # only values in the 1M-scale-error band are corrected/rejected.
            LIKELY_SCALE_ERROR_MIN = 100000  # Values at/above this are likely 1M scale errors
            MAX_REASONABLE_EPS = LIKELY_SCALE_ERROR_MIN  # Keep everything below the scale-error floor

            def correct_eps_value(eps_val):
                """Attempt to correct obviously wrong EPS values"""
                if abs(eps_val) >= LIKELY_SCALE_ERROR_MIN:
                    # Check if dividing by 1M gives a reasonable value
                    corrected = eps_val / 1000000
                    if abs(corrected) <= MAX_REASONABLE_EPS:
                        return corrected, True
                return eps_val, False

            # For each year, take the lower (more conservative) EPS value
            annual_eps = {}
            for fy, eps_list in all_eps_data.items():
                # Try to correct scale errors first
                corrected_eps = []
                for e in eps_list:
                    new_val, was_corrected = correct_eps_value(e['eps'])
                    if was_corrected:
                        print(f"[SEC] Corrected {ticker} FY{fy} EPS: {e['eps']:,.0f} -> {new_val:.2f} (likely 1M scale error)")
                    corrected_eps.append({**e, 'eps': new_val})

                # Filter out still-unreasonable EPS values
                valid_eps = [e for e in corrected_eps if abs(e['eps']) <= MAX_REASONABLE_EPS]
                if not valid_eps:
                    print(f"[SEC] Warning: All EPS values for {ticker} FY{fy} exceed sanity check (values: {[e['eps'] for e in eps_list]})")
                    continue
                # Sort by EPS value (ascending) and take the lowest
                valid_eps.sort(key=lambda x: x['eps'])
                annual_eps[fy] = valid_eps[0]

            # Sort by year descending
            sorted_eps = sorted(annual_eps.values(), key=lambda x: x['year'], reverse=True)

            if not sorted_eps:
                return None, 'no_eps'

            return {
                'ticker': ticker,
                'cik': cik,
                'company_name': data.get('entityName', ticker),
                'eps_history': sorted_eps[:8],  # Keep up to 8 years max
                'updated': datetime.now().isoformat()
            }, 'ok'
    except Exception as e:
        print(f"[SEC] Error fetching EPS for {ticker}: {e}")

    return None, 'error'


def fetch_company_metrics(ticker, cik):
    """Fetch key financial metrics from SEC EDGAR for a company.

    Returns multi-year EPS data organized by type (for matrix display)
    and annual dividend data.
    """
    try:
        # Same companyfacts document as the EPS/balance-sheet/shares parsers;
        # routed through the shared memo instead of its own download.
        data = _fetch_companyfacts(cik)

        if data is not None:
            us_gaap = data.get('facts', {}).get('us-gaap', {})

            def get_annual_values(field_name, unit='USD'):
                """Get all annual values for a field, organized by year"""
                if field_name not in us_gaap:
                    return {}
                unit_key = 'USD/shares' if unit == 'USD/shares' else unit
                records = us_gaap[field_name].get('units', {}).get(unit_key, [])

                # Filter to 10-K annual records (full year, not quarterly)
                annual = {}
                for r in records:
                    if r.get('form') == '10-K':
                        frame = r.get('frame', '')
                        # Full year records - frame like "CY2024" not "CY2024Q1"
                        if frame and 'Q' not in frame:
                            try:
                                year = int(frame.replace('CY', ''))
                            except ValueError:
                                continue
                            # Keep latest filing for each year
                            if year not in annual or r.get('filed', '') > annual[year].get('filed', ''):
                                annual[year] = {
                                    'value': r.get('val'),
                                    'year': year,
                                    'period_start': r.get('start'),
                                    'period_end': r.get('end'),
                                    'filed': r.get('filed')
                                }
                return annual

            # EPS fields to extract (multi-year)
            eps_fields = [
                ('EarningsPerShareBasic', 'Basic EPS'),
                ('EarningsPerShareDiluted', 'Diluted EPS'),
                ('IncomeLossFromContinuingOperationsPerBasicShare', 'Continuing Ops (Basic)'),
                ('IncomeLossFromContinuingOperationsPerDilutedShare', 'Continuing Ops (Diluted)'),
                ('IncomeLossFromDiscontinuedOperationsNetOfTaxPerBasicShare', 'Discontinued Ops (Basic)'),
                ('IncomeLossFromDiscontinuedOperationsNetOfTaxPerDilutedShare', 'Discontinued Ops (Diluted)'),
            ]

            # Build EPS matrix: {eps_type: {year: value, ...}, ...}
            eps_matrix = {}
            all_years = set()

            for field, label in eps_fields:
                annual_data = get_annual_values(field, 'USD/shares')
                if annual_data:
                    eps_matrix[label] = {}
                    for year, data_point in annual_data.items():
                        eps_matrix[label][year] = data_point['value']
                        all_years.add(year)

            # Get years sorted ascending
            years = sorted(all_years)

            # Dividend data - CommonStockDividendsPerShareDeclared
            dividend_fields = [
                ('CommonStockDividendsPerShareDeclared', 'Common Stock Dividend'),
                ('CommonStockDividendsPerShareCashPaid', 'Common Stock Dividend (Paid)'),
            ]

            dividend_matrix = {}
            dividend_years = set()

            for field, label in dividend_fields:
                annual_data = get_annual_values(field, 'USD/shares')
                if annual_data:
                    dividend_matrix[label] = {}
                    for year, data_point in annual_data.items():
                        dividend_matrix[label][year] = data_point['value']
                        dividend_years.add(year)

            div_years = sorted(dividend_years)

            return {
                'ticker': ticker,
                'cik': cik,
                'company_name': data.get('entityName', ticker),
                'eps_matrix': eps_matrix,
                'eps_years': years[-8:] if len(years) > 8 else years,  # Last 8 years
                'dividend_matrix': dividend_matrix,
                'dividend_years': div_years[-8:] if len(div_years) > 8 else div_years,
                'fetched': datetime.now().isoformat()
            }
    except Exception as e:
        print(f"[SEC] Error fetching metrics for {ticker}: {e}")

    return None


# Short-lived memo of the raw companyfacts document, keyed by CIK.
#
# The same multi-megabyte document is needed by four different parsers (EPS,
# metrics, balance sheet, shares outstanding). The star phase in particular
# calls fetch_balance_sheet and fetch_shares_outstanding back-to-back for the
# same ticker, so the identical document was downloaded and JSON-parsed twice
# per ticker per run. Parsed documents are large, so the memo is deliberately
# tiny - it exists to collapse the burst of calls for ONE ticker, not to hold
# the universe. TTL bounds staleness within a long-running screener pass.
_COMPANYFACTS_MEMO = OrderedDict()
_COMPANYFACTS_MEMO_LOCK = threading.Lock()
_COMPANYFACTS_MEMO_MAX = 4
_COMPANYFACTS_MEMO_TTL = 300  # seconds


def _companyfacts_from_memo(cik):
    with _COMPANYFACTS_MEMO_LOCK:
        entry = _COMPANYFACTS_MEMO.get(cik)
        if not entry:
            return None
        cached_at, payload = entry
        if time.time() - cached_at > _COMPANYFACTS_MEMO_TTL:
            _COMPANYFACTS_MEMO.pop(cik, None)
            return None
        _COMPANYFACTS_MEMO.move_to_end(cik)
        return payload


def _companyfacts_to_memo(cik, payload):
    if payload is None:
        return
    with _COMPANYFACTS_MEMO_LOCK:
        _COMPANYFACTS_MEMO[cik] = (time.time(), payload)
        _COMPANYFACTS_MEMO.move_to_end(cik)
        while len(_COMPANYFACTS_MEMO) > _COMPANYFACTS_MEMO_MAX:
            _COMPANYFACTS_MEMO.popitem(last=False)


def clear_companyfacts_memo():
    """Drop the in-process companyfacts memo (used by force-refresh paths)."""
    with _COMPANYFACTS_MEMO_LOCK:
        _COMPANYFACTS_MEMO.clear()


def _fetch_companyfacts(cik, use_memo=True):
    """Internal: fetch raw companyfacts JSON for a CIK. Respects rate limit.

    Serves a recently fetched copy when one is in the memo, so the several
    parsers that each need this document for the same ticker share one
    download instead of issuing their own.
    """
    if use_memo:
        cached = _companyfacts_from_memo(cik)
        if cached is not None:
            return cached

    rate_limit()
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    response = _SEC_SESSION.get(url, timeout=SEC_REQUEST_TIMEOUT)
    if response.status_code != 200:
        return None

    payload = response.json()
    _companyfacts_to_memo(cik, payload)
    return payload


def _latest_annual_value(us_gaap, field_names, unit='USD'):
    """
    From us_gaap dict, find the most recent annual (10-K, non-quarterly) value
    among the candidate field_names, in priority order.

    Returns (value, as_of_date, field_used) or (None, None, None) if not found.
    """
    for field in field_names:
        if field not in us_gaap:
            continue
        unit_key = unit
        records = us_gaap[field].get('units', {}).get(unit_key, [])
        latest = None
        for r in records:
            if r.get('form') != '10-K':
                continue
            frame = r.get('frame', '')
            # Period instants for balance sheet items use frames like "CY2024Q4I"
            # Period durations for income/cashflow use "CY2024" (no Q).
            # Both are valid annual checkpoints.
            end_date = r.get('end')
            if not end_date:
                continue
            if latest is None or end_date > latest['end']:
                latest = {'val': r.get('val'), 'end': end_date}
        if latest is not None:
            return latest['val'], latest['end'], field
    return None, None, None


def fetch_balance_sheet(ticker):
    """
    Fetch the most recent balance-sheet components from SEC EDGAR.

    Returns dict with long_term_debt, short_term_debt, stockholders_equity,
    as_of_date, and computed debt_to_capital — or None on failure.

    Used by the Star Scoring system (debt-to-capital criterion).
    """
    ticker = ticker.upper()
    cik = get_cik_for_ticker(ticker)
    if not cik:
        return None

    try:
        data = _fetch_companyfacts(cik)
        if not data:
            return None
        us_gaap = data.get('facts', {}).get('us-gaap', {})

        ltd, ltd_date, _ = _latest_annual_value(
            us_gaap,
            ['LongTermDebtNoncurrent', 'LongTermDebt'],
            unit='USD',
        )
        std, std_date, _ = _latest_annual_value(
            us_gaap,
            ['DebtCurrent', 'LongTermDebtCurrent', 'ShortTermBorrowings'],
            unit='USD',
        )
        equity, equity_date, _ = _latest_annual_value(
            us_gaap,
            [
                'StockholdersEquity',
                'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
            ],
            unit='USD',
        )

        # Need at least equity + some debt to compute the ratio.
        if equity is None:
            return None

        ltd_v = float(ltd) if ltd is not None else 0.0
        std_v = float(std) if std is not None else 0.0
        equity_v = float(equity)
        total_debt = ltd_v + std_v
        denom = total_debt + equity_v
        debt_to_capital = (total_debt / denom) if denom > 0 else None

        # Use the most recent end date among the fields we found
        as_of = max(d for d in [ltd_date, std_date, equity_date] if d)

        return {
            'ticker': ticker,
            'long_term_debt': ltd_v if ltd is not None else None,
            'short_term_debt': std_v if std is not None else None,
            'stockholders_equity': equity_v,
            'debt_to_capital': debt_to_capital,
            'as_of_date': as_of,
            'source': 'sec_edgar',
        }
    except Exception as e:
        print(f"[SEC] Error fetching balance sheet for {ticker}: {e}")
        return None


def fetch_shares_outstanding(ticker):
    """
    Fetch shares outstanding history from SEC EDGAR.

    Returns dict with current (most-recent) shares and a time series of
    {date, shares} entries — or None on failure.

    Used by the Star Scoring system (share-buyback criterion).
    """
    ticker = ticker.upper()
    cik = get_cik_for_ticker(ticker)
    if not cik:
        return None

    try:
        data = _fetch_companyfacts(cik)
        if not data:
            return None
        facts = data.get('facts', {})
        us_gaap = facts.get('us-gaap', {})
        dei = facts.get('dei', {})

        # Concepts in priority order — different filers use different ones.
        # Each is paired with its XBRL namespace: EntityCommonStockShares-
        # Outstanding is a cover-page `dei` concept, NOT us-gaap, so the old
        # us-gaap-only lookup could never find it (dead fallback). For filers
        # missing us-gaap CommonStockSharesOutstanding the code then wrongly
        # fell through to the diluted weighted-average count.
        candidate_fields = [
            ('us-gaap', 'CommonStockSharesOutstanding'),
            ('dei', 'EntityCommonStockSharesOutstanding'),
            ('us-gaap', 'WeightedAverageNumberOfDilutedSharesOutstanding'),
        ]
        namespaces = {'us-gaap': us_gaap, 'dei': dei}

        # Collect ALL records (not just 10-K) so we get the freshest snapshot.
        records = []
        field_used = None
        for ns, field in candidate_fields:
            ns_facts = namespaces.get(ns, {})
            if field in ns_facts:
                shares_records = ns_facts[field].get('units', {}).get('shares', [])
                if shares_records:
                    records = shares_records
                    field_used = field
                    break
        if not records:
            return None

        # Deduplicate by end date keeping latest filing
        by_date = {}
        for r in records:
            end_date = r.get('end')
            val = r.get('val')
            if not end_date or val is None:
                continue
            existing = by_date.get(end_date)
            if existing is None or r.get('filed', '') > existing.get('filed', ''):
                by_date[end_date] = {'end': end_date, 'val': float(val), 'filed': r.get('filed', '')}

        if not by_date:
            return None

        history = sorted(
            (
                {'date': v['end'], 'shares': v['val'], 'source': 'sec_edgar'}
                for v in by_date.values()
            ),
            key=lambda r: r['date'],
        )

        latest_entry = history[-1]
        return {
            'ticker': ticker,
            'current': latest_entry['shares'],
            'current_date': latest_entry['date'],
            'field': field_used,
            'history': history,
        }
    except Exception as e:
        print(f"[SEC] Error fetching shares outstanding for {ticker}: {e}")
        return None


# --- Bulk EPS via the XBRL frames API ---

# Concepts tried per year, most specific first. The first concept that reports
# a value for a company wins, matching the per-company extraction order.
_FRAME_CONCEPTS = (
    ('EarningsPerShareDiluted', 'Diluted EPS'),
    ('EarningsPerShareBasic', 'Basic EPS'),
)


def fetch_eps_frame(concept, year):
    """Fetch one XBRL frame: annual EPS for EVERY filer in one request.

    https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD-per-shares/CY{year}.json
    returns ~5,500 companies in a single ~800 KB response, versus one
    multi-megabyte companyfacts download per company.

    Returns {cik_int: {'eps', 'period_start', 'period_end'}} or {} on failure.
    """
    url = (f"https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}"
           f"/USD-per-shares/CY{year}.json")
    try:
        rate_limit()
        response = _SEC_SESSION.get(url, timeout=SEC_REQUEST_TIMEOUT)
        if response.status_code != 200:
            return {}
        payload = response.json()
    except Exception as e:
        print(f"[SEC] frames fetch failed for {concept} CY{year}: {e}")
        return {}

    out = {}
    for entry in payload.get('data', []):
        cik = entry.get('cik')
        val = entry.get('val')
        if cik is None or val is None:
            continue
        out[int(cik)] = {
            'eps': val,
            'period_start': entry.get('start'),
            'period_end': entry.get('end'),
        }
    return out


def refresh_eps_from_frames(tickers=None, years=None, progress_callback=None):
    """Fill missing annual EPS for many tickers using the frames API.

    Replaces a per-company companyfacts crawl (one multi-MB download each,
    serialized behind the SEC rate limit) with roughly 2 requests per year -
    about 16 requests for an 8-year window, regardless of universe size.

    Gap-filling only: years already stored from per-company data are left
    alone, since those carry the filing date and preferred concept. Frames
    responses have no `filed` field, so rows written here record None for it.

    Args:
        tickers: restrict to these tickers (default: everything with a CIK)
        years: number of years back to cover (default RECOMMENDED_EPS_YEARS)
        progress_callback: optional callable(done, total) for UI progress

    Returns a stats dict.
    """
    from config import RECOMMENDED_EPS_YEARS

    years = years or RECOMMENDED_EPS_YEARS
    _ensure_cik_mapping_fresh()

    # cik(int) -> ticker, restricted to tickers we actually track.
    mapping = db.get_cik_mapping().get('tickers', {})
    wanted = {t.upper() for t in tickers} if tickers is not None else None
    cik_to_ticker = {}
    for ticker, info in mapping.items():
        if wanted is not None and ticker.upper() not in wanted:
            continue
        try:
            cik_to_ticker[int(info['cik'])] = ticker.upper()
        except (TypeError, ValueError, KeyError):
            continue

    if not cik_to_ticker:
        return {'requests': 0, 'rows_written': 0, 'tickers_touched': 0, 'years': 0}

    existing = db.get_existing_eps_years(list(cik_to_ticker.values()))

    current_year = datetime.now().year
    # The most recent full fiscal year is normally last year; go back `years`.
    target_years = list(range(current_year - 1, current_year - 1 - years, -1))

    rows = []
    requests_made = 0
    total_steps = len(target_years) * len(_FRAME_CONCEPTS)
    done_steps = 0

    for year in target_years:
        seen_this_year = set()
        for concept, label in _FRAME_CONCEPTS:
            frame = fetch_eps_frame(concept, year)
            requests_made += 1
            done_steps += 1
            if progress_callback:
                try:
                    progress_callback(done_steps, total_steps)
                except Exception:
                    pass

            for cik, record in frame.items():
                ticker = cik_to_ticker.get(cik)
                if not ticker or ticker in seen_this_year:
                    continue
                if year in existing.get(ticker, set()):
                    continue  # per-company data already covers this year
                seen_this_year.add(ticker)
                rows.append({
                    'ticker': ticker,
                    'year': year,
                    'eps': record['eps'],
                    'filed': None,
                    'period_start': record.get('period_start'),
                    'period_end': record.get('period_end'),
                    'eps_type': f'{label} (frames)',
                })

    written = db.fill_eps_history_gaps(rows) if rows else 0
    return {
        'requests': requests_made,
        'rows_written': written,
        'tickers_touched': len({r['ticker'] for r in rows}),
        'years': len(target_years),
    }


def get_sec_metrics(ticker):
    """Get SEC metrics for a ticker (fetches fresh each time for now)"""
    ticker = ticker.upper()
    cik = get_cik_for_ticker(ticker)
    if not cik:
        return None
    return fetch_company_metrics(ticker, cik)


def _next_expected_filing(cached):
    """Date a newer 10-K could first plausibly exist for this cached company.

    Derived from the latest cached fiscal period end: the next fiscal year ends
    a year later, and the 10-K follows within SEC_EPS_FILING_LAG_DAYS. Returns
    None when the cache has no usable period_end.
    """
    history = (cached or {}).get('eps_history') or []
    period_end = None
    for row in history:
        # eps_history is ordered year DESC; take the newest row that has a date.
        end = row.get('period_end') or row.get('end')
        if end:
            period_end = end
            break

    if not period_end:
        return None

    try:
        fy_end = datetime.strptime(period_end, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None

    try:
        next_fy_end = fy_end.replace(year=fy_end.year + 1)
    except ValueError:
        # Feb 29 fiscal year end — the following year isn't a leap year
        next_fy_end = fy_end.replace(year=fy_end.year + 1, day=28)

    return next_fy_end + timedelta(days=SEC_EPS_FILING_LAG_DAYS)


def eps_cache_is_fresh(cached):
    """Is this cached SEC EPS record still current?

    Annual EPS only changes when a new 10-K is filed, so a flat one-day TTL
    made every screener run re-download the whole universe's multi-megabyte
    companyfacts documents for data that changes once a year. Freshness is
    keyed to the filing calendar instead:

    - before the next 10-K can plausibly exist -> fresh, whatever its age
    - after that date -> fresh only if we have already re-checked since it
      passed (so a company that files late is retried, not hammered)
    - no fiscal dates at all -> fall back to a periodic re-probe
    - tickers SEC has no EPS for -> long negative TTL instead of every run
    - anything older than the hard ceiling -> always refresh, as a self-heal
    """
    if not cached or not cached.get('updated'):
        return False

    try:
        updated = datetime.fromisoformat(cached['updated'])
    except (ValueError, TypeError):
        return False

    now = datetime.now()
    age = now - updated

    # Self-heal ceiling: never trust a record indefinitely.
    if age >= timedelta(days=SEC_EPS_MAX_AGE_DAYS):
        return False

    # Negative result: SEC has no EPS for this ticker. Re-probe occasionally
    # instead of re-downloading companyfacts on every single run.
    if cached.get('sec_no_eps'):
        return age < timedelta(days=SEC_EPS_NO_DATA_RECHECK_DAYS)

    expected = _next_expected_filing(cached)
    if expected is None:
        return age < timedelta(days=SEC_EPS_RECHECK_DAYS)

    if now < expected:
        return True

    # The filing window has opened. Fresh only if this record was written
    # after it opened (i.e. we already looked and there was nothing new).
    return updated >= expected


def get_sec_eps(ticker, log_source=True, force=False):
    """Get SEC EPS data for a ticker, using cache when available"""
    ticker = ticker.upper()
    cached = load_company_cache(ticker)

    # Check if cache is fresh enough (filing-calendar aware)
    if cached and not force and eps_cache_is_fresh(cached):
        # Using cached data - mark it so caller knows
        cached['_from_cache'] = True
        return cached

    # Fetch fresh data from SEC API
    cik = get_cik_for_ticker(ticker)
    if not cik:
        return None

    data, status = _fetch_company_eps_with_status(ticker, cik)
    if data:
        data['_from_cache'] = False
        save_company_cache(ticker, data)
        return data

    # SEC answered but has no usable EPS for this filer: persist that as a
    # dated negative result so the next run doesn't re-download the whole
    # companyfacts document to rediscover it. Previously nothing was written on
    # this path, so `updated` never advanced and no-EPS tickers were re-fetched
    # on every run forever. A transport error records nothing.
    if status == 'no_eps':
        record = dict(cached) if cached else {'ticker': ticker}
        had_history = bool(record.get('eps_history'))
        record.update({
            'cik': cik,
            'updated': datetime.now().isoformat(),
        })
        # Only flag sec_no_eps for a filer we have never had EPS for. If we hold
        # history and SEC suddenly returns none, keep the history and just stamp
        # the re-check date rather than blanking a good company.
        if not had_history:
            record['sec_no_eps'] = True
            record['reason'] = record.get('reason') or 'No EPS data in SEC companyfacts'
        record.pop('_from_cache', None)
        try:
            save_company_cache(ticker, record)
        except Exception as e:
            print(f"[SEC] Could not persist no-EPS marker for {ticker}: {e}")

    # Return stale cache if fetch failed
    if cached:
        cached['_from_cache'] = True
    return cached


def is_cache_stale(ticker):
    """Check if a ticker's cache needs updating"""
    return not eps_cache_is_fresh(load_company_cache(ticker))


def has_cached_eps(ticker):
    """Check if we have any cached SEC data for a ticker (regardless of staleness)"""
    return db.has_sec_eps(ticker.upper())


def fetch_sec_eps_if_missing(ticker):
    """
    Fetch SEC EPS data only if we don't have it cached.
    Returns tuple: (data, was_fetched)
    """
    ticker = ticker.upper()
    cached = load_company_cache(ticker)

    # If we have cached data with EPS, use it
    if cached and cached.get('eps_history'):
        return cached, False

    # If we've already tried and SEC has no EPS for this ticker, don't retry
    if cached and cached.get('sec_no_eps'):
        return None, False

    # No cache - fetch from SEC
    cik = get_cik_for_ticker(ticker)
    if not cik:
        # Save marker that this ticker has no CIK mapping
        save_company_cache(ticker, {
            'ticker': ticker,
            'cik': None,
            'eps_history': [],
            'sec_no_eps': True,
            'reason': 'No CIK mapping found',
            'updated': datetime.now().isoformat()
        })
        return None, True  # Return True so it counts as "attempted"

    data = fetch_company_eps(ticker, cik)
    if data and data.get('eps_history'):
        save_company_cache(ticker, data)
        return data, True

    # SEC has no EPS data for this ticker - save marker so we don't keep retrying
    save_company_cache(ticker, {
        'ticker': ticker,
        'cik': cik,
        'eps_history': [],
        'sec_no_eps': True,
        'reason': 'SEC XBRL has no EPS data for this company',
        'updated': datetime.now().isoformat()
    })
    return None, True  # Return True so it counts as "attempted"


def force_refresh_sec_eps(ticker):
    """
    Check SEC for newer EPS data and add any new years to the database.
    Returns tuple: (data, new_years_added)
    """
    ticker = ticker.upper()

    # Check if we have existing data
    existing = load_company_cache(ticker)
    existing_years = set()
    if existing and existing.get('eps_history'):
        existing_years = {eps['year'] for eps in existing['eps_history']}

    # Get CIK for ticker
    cik = get_cik_for_ticker(ticker)
    if not cik:
        if not existing:
            save_company_cache(ticker, {
                'ticker': ticker,
                'cik': None,
                'eps_history': [],
                'sec_no_eps': True,
                'reason': 'No CIK mapping found',
                'updated': datetime.now().isoformat()
            })
        return existing, 0

    # Fetch from SEC
    fresh_data = fetch_company_eps(ticker, cik)

    if not fresh_data or not fresh_data.get('eps_history'):
        if not existing:
            save_company_cache(ticker, {
                'ticker': ticker,
                'cik': cik,
                'eps_history': [],
                'sec_no_eps': True,
                'reason': 'SEC XBRL has no EPS data for this company',
                'updated': datetime.now().isoformat()
            })
        return existing, 0

    # Find new years we don't have
    fresh_years = {eps['year'] for eps in fresh_data['eps_history']}
    new_years = fresh_years - existing_years

    if new_years:
        # Add only the new years
        new_eps = [eps for eps in fresh_data['eps_history'] if eps['year'] in new_years]
        added = db.add_new_eps_years(ticker, new_eps)

        # Return updated data
        updated_data = load_company_cache(ticker)
        return updated_data, added
    else:
        # No new years, just update timestamp
        db.add_new_eps_years(ticker, [])  # Updates timestamp only
        return existing, 0


# --- Background Updates ---

def update_sec_data_for_tickers(tickers):
    """Background update of SEC data for multiple tickers"""
    global sec_update_running, sec_update_progress

    sec_update_running = True
    sec_update_progress = {
        'current': 0,
        'total': len(tickers),
        'ticker': '',
        'status': 'running'
    }

    updated_count = 0

    for i, ticker in enumerate(tickers):
        if not sec_update_running:
            sec_update_progress['status'] = 'cancelled'
            break

        sec_update_progress['current'] = i + 1
        sec_update_progress['ticker'] = ticker

        # Check if we need to update this ticker
        if is_cache_stale(ticker):
            cik = get_cik_for_ticker(ticker)
            if cik:
                data = fetch_company_eps(ticker, cik)
                if data:
                    save_company_cache(ticker, data)
                    updated_count += 1

    # Update metadata
    metadata = load_metadata()
    metadata['last_full_update'] = datetime.now().isoformat()
    save_metadata(metadata)

    sec_update_progress['status'] = 'complete'
    sec_update_running = False

    print(f"[SEC] Updated {updated_count} companies")
    return updated_count


def start_background_update(tickers):
    """Start SEC data update in background thread"""
    global sec_update_running

    if sec_update_running:
        return False

    thread = threading.Thread(target=update_sec_data_for_tickers, args=(tickers,))
    thread.daemon = True
    thread.start()
    return True


def stop_update():
    """Stop the running update"""
    global sec_update_running
    sec_update_running = False


def get_update_progress():
    """Get current update progress"""
    return sec_update_progress


def check_and_update_on_startup(tickers):
    """Check if SEC data needs updating on startup, run in background if so"""
    # Initialize database
    db.init_database()

    # Check CIK mapping first
    mapping = load_cik_mapping()
    if not mapping.get('tickers'):
        print("[SEC] No CIK mapping found, fetching...")
        update_cik_mapping()
    elif mapping.get('updated'):
        try:
            updated = datetime.fromisoformat(mapping['updated'])
            if datetime.now() - updated > timedelta(days=CIK_CACHE_DAYS):
                print("[SEC] CIK mapping stale, refreshing...")
                update_cik_mapping()
        except (ValueError, TypeError):
            update_cik_mapping()

    # Check which tickers need updating
    needs_update = [t for t in tickers if is_cache_stale(t)]

    if needs_update:
        print(f"[SEC] {len(needs_update)} tickers need updating, starting background update...")
        start_background_update(needs_update)
    else:
        print("[SEC] All SEC data is up to date")


# --- Cache Status ---

def get_cache_status():
    """Get status of SEC cache for UI display"""
    mapping = load_cik_mapping()

    return {
        'cik_mapping': {
            'count': mapping.get('count', 0),
            'updated': mapping.get('updated')
        },
        'companies': {
            'count': db.get_sec_company_count(),
            'last_full_update': db.get_metadata('sec_last_full_update')
        }
    }


# --- EPS Update Recommendations ---

def get_eps_update_recommendations():
    """
    Analyze cached SEC data to recommend which tickers need EPS updates.

    Companies typically file 10-K reports within 60-90 days after their fiscal year ends.
    This function identifies tickers where:
    1. Their fiscal year has ended and enough time has passed for new filings
    2. We haven't fetched data recently enough to catch new filings

    Returns dict with:
    - needs_update: list of tickers likely to have new 10-K filings available
    - recently_updated: list of tickers with fresh data
    - details: per-ticker analysis info
    """
    # Get all SEC companies from database
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT sc.ticker, sc.company_name, sc.updated,
                   eh.year, eh.eps, eh.filed, eh.period_start, eh.period_end
            FROM sec_companies sc
            LEFT JOIN eps_history eh ON sc.ticker = eh.ticker
            WHERE sc.sec_no_eps = 0
            ORDER BY sc.ticker, eh.year DESC
        ''')
        rows = cursor.fetchall()

    if not rows:
        return {'needs_update': [], 'recently_updated': [], 'details': {}}

    # Group by ticker
    ticker_data = {}
    for row in rows:
        ticker = row['ticker']
        if ticker not in ticker_data:
            ticker_data[ticker] = {
                'company_name': row['company_name'],
                'updated': row['updated'],
                'eps_history': []
            }
        if row['year']:
            ticker_data[ticker]['eps_history'].append({
                'year': row['year'],
                'eps': row['eps'],
                'filed': row['filed'],
                'start': row['period_start'],
                'end': row['period_end']
            })

    needs_update = []
    recently_updated = []
    details = {}
    today = datetime.now()

    for ticker, data in ticker_data.items():
        if not data['eps_history']:
            continue

        latest_eps = data['eps_history'][0]

        ticker_info = {
            'ticker': ticker,
            'company_name': data.get('company_name', ticker),
            'latest_fy': latest_eps.get('year'),
            'fiscal_year_end': latest_eps.get('end'),
            'last_filing_date': latest_eps.get('filed'),
            'cache_updated': data.get('updated'),
            'status': 'current',
            'reason': None
        }

        # Parse dates
        fiscal_year_end = None
        if latest_eps.get('end'):
            try:
                fiscal_year_end = datetime.strptime(latest_eps['end'], '%Y-%m-%d')
                ticker_info['fiscal_year_end_parsed'] = fiscal_year_end.strftime('%b %d, %Y')
            except ValueError:
                pass

        cache_updated = None
        if data.get('updated'):
            try:
                cache_updated = datetime.fromisoformat(data['updated'])
            except ValueError:
                pass

        # Determine if new filing might be available
        if fiscal_year_end:
            try:
                next_fy_end = fiscal_year_end.replace(year=fiscal_year_end.year + 1)
            except ValueError:
                # Feb 29 fiscal year end — the following year isn't a leap year
                next_fy_end = fiscal_year_end.replace(year=fiscal_year_end.year + 1, day=28)
            expected_filing_date = next_fy_end + timedelta(days=75)
            ticker_info['next_fy_end'] = next_fy_end.strftime('%b %d, %Y')
            ticker_info['expected_filing'] = expected_filing_date.strftime('%b %d, %Y')

            days_since_fy_end = (today - next_fy_end).days

            if days_since_fy_end > 75:
                if cache_updated and cache_updated < expected_filing_date:
                    ticker_info['status'] = 'update_recommended'
                    ticker_info['reason'] = f'FY{latest_eps.get("year")+1} 10-K likely available'
                    ticker_info['days_since_fy_end'] = days_since_fy_end
                    ticker_info['priority'] = 'high' if days_since_fy_end > 120 else 'medium'
                    needs_update.append(ticker)
                else:
                    recently_updated.append(ticker)
            elif days_since_fy_end > 0:
                ticker_info['status'] = 'pending'
                ticker_info['reason'] = f'FY ended {days_since_fy_end} days ago'
            else:
                ticker_info['status'] = 'current'
                recently_updated.append(ticker)
        else:
            if is_cache_stale(ticker):
                ticker_info['status'] = 'stale'
                ticker_info['reason'] = 'Cache is stale'
                needs_update.append(ticker)
            else:
                recently_updated.append(ticker)

        details[ticker] = ticker_info

    # Sort needs_update by priority
    needs_update_with_priority = []
    for ticker in needs_update:
        info = details[ticker]
        priority_score = 0
        if info.get('priority') == 'high':
            priority_score = 1000
        elif info.get('priority') == 'medium':
            priority_score = 500
        priority_score += info.get('days_since_fy_end', 0)
        needs_update_with_priority.append((ticker, priority_score))

    needs_update_with_priority.sort(key=lambda x: x[1], reverse=True)
    needs_update = [t[0] for t in needs_update_with_priority]

    return {
        'needs_update': needs_update,
        'needs_update_count': len(needs_update),
        'recently_updated': recently_updated,
        'recently_updated_count': len(recently_updated),
        'total_cached': len(details),
        'details': details,
        'generated': datetime.now().isoformat()
    }


# --- 10-K Filing URLs ---

FILINGS_CACHE_DAYS = 7  # Check for new filings weekly


def fetch_10k_filings(ticker, cik):
    """Fetch 10-K filing URLs from SEC submissions API"""
    try:
        rate_limit()
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        response = _SEC_SESSION.get(url, timeout=SEC_REQUEST_TIMEOUT)

        if response.status_code != 200:
            print(f"[SEC] Failed to fetch submissions for {ticker}: {response.status_code}")
            return []

        data = response.json()
        filings = data.get('filings', {}).get('recent', {})

        if not filings:
            return []

        # Extract 10-K filings
        tenk_filings = []
        forms = filings.get('form', [])
        accession_numbers = filings.get('accessionNumber', [])
        filing_dates = filings.get('filingDate', [])
        primary_documents = filings.get('primaryDocument', [])
        report_dates = filings.get('reportDate', [])

        # CIK without leading zeros for URL construction
        cik_no_pad = str(int(cik))

        for i, form in enumerate(forms):
            if form in ('10-K', '10-K/A'):
                # Extract fiscal year from report date
                report_date = report_dates[i] if i < len(report_dates) else ''
                try:
                    fiscal_year = int(report_date[:4]) if report_date else None
                except (ValueError, TypeError):
                    fiscal_year = None

                if not fiscal_year:
                    continue

                # Construct document URL
                accession = accession_numbers[i].replace('-', '')
                primary_doc = primary_documents[i] if i < len(primary_documents) else ''
                doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_no_pad}/{accession}/{primary_doc}"

                tenk_filings.append({
                    'fiscal_year': fiscal_year,
                    'form_type': form,
                    'accession_number': accession_numbers[i],
                    'filing_date': filing_dates[i] if i < len(filing_dates) else '',
                    'document_url': doc_url
                })

        # Sort by fiscal year descending
        tenk_filings.sort(key=lambda x: x['fiscal_year'], reverse=True)

        print(f"[SEC] Found {len(tenk_filings)} 10-K filings for {ticker}")
        return tenk_filings

    except Exception as e:
        print(f"[SEC] Error fetching 10-K filings for {ticker}: {e}")
        return []


def is_filings_stale(ticker):
    """Check if a ticker's filing URLs need refreshing"""
    last_updated = db.get_sec_filings_last_updated(ticker)
    if not last_updated:
        return True

    try:
        updated = datetime.fromisoformat(last_updated)
        return datetime.now() - updated >= timedelta(days=FILINGS_CACHE_DAYS)
    except (ValueError, TypeError):
        return True


def get_10k_filings(ticker):
    """Get 10-K filing URLs for a ticker, using cache when available"""
    ticker = ticker.upper()

    # Check if we have cached filings that aren't stale
    if not is_filings_stale(ticker):
        cached = db.get_sec_filings(ticker)
        if cached:
            return cached

    # Fetch fresh data
    cik = get_cik_for_ticker(ticker)
    if not cik:
        return []

    filings = fetch_10k_filings(ticker, cik)
    if filings:
        db.save_sec_filings(ticker, filings)
        return filings

    # Return stale cache if fetch failed
    return db.get_sec_filings(ticker)

"""
SEC EDGAR Data Module
Handles fetching and caching of SEC financial data (EPS, etc.)

Cache Structure:
    data/sec/
    ├── metadata.json         # Cache version, last update timestamps
    ├── cik_mapping.json      # Ticker → CIK (updates weekly)
    └── companies/
        ├── AAPL.json
        ├── MSFT.json
        └── ...
"""
import os
import json
import time
import requests
from datetime import datetime, timedelta
import threading

# Directory structure
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
SEC_DIR = os.path.join(DATA_DIR, 'sec')
COMPANIES_DIR = os.path.join(SEC_DIR, 'companies')
METADATA_FILE = os.path.join(SEC_DIR, 'metadata.json')
CIK_MAPPING_FILE = os.path.join(SEC_DIR, 'cik_mapping.json')

# Legacy cache file (for migration)
LEGACY_CACHE_FILE = os.path.join(DATA_DIR, 'sec_cache.json')

# Cache version - increment when structure changes
CACHE_VERSION = 2

# SEC requires User-Agent with contact info
SEC_HEADERS = {'User-Agent': 'FinanceApp contact@example.com'}

# Rate limiting: SEC allows 10 requests/second
SEC_RATE_LIMIT = 0.12  # seconds between requests

# Cache durations
CIK_CACHE_DAYS = 7  # Refresh ticker->CIK mapping weekly
EPS_CACHE_DAYS = 1  # Check for new filings daily

# Module state
sec_update_running = False
sec_update_progress = {'current': 0, 'total': 0, 'ticker': '', 'status': 'idle'}
last_sec_request = 0


def ensure_directories():
    """Ensure cache directories exist"""
    os.makedirs(COMPANIES_DIR, exist_ok=True)


def rate_limit():
    """Ensure we don't exceed SEC rate limits"""
    global last_sec_request
    elapsed = time.time() - last_sec_request
    if elapsed < SEC_RATE_LIMIT:
        time.sleep(SEC_RATE_LIMIT - elapsed)
    last_sec_request = time.time()


# --- Metadata ---

def load_metadata():
    """Load cache metadata"""
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {'version': CACHE_VERSION, 'last_full_update': None}


def save_metadata(data):
    """Save cache metadata"""
    ensure_directories()
    data['version'] = CACHE_VERSION
    with open(METADATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# --- CIK Mapping ---

def load_cik_mapping():
    """Load cached ticker->CIK mapping"""
    if os.path.exists(CIK_MAPPING_FILE):
        try:
            with open(CIK_MAPPING_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {'tickers': {}, 'updated': None}


def save_cik_mapping(data):
    """Save ticker->CIK mapping to cache"""
    ensure_directories()
    with open(CIK_MAPPING_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def update_cik_mapping():
    """Fetch fresh ticker->CIK mapping from SEC"""
    try:
        rate_limit()
        url = "https://www.sec.gov/files/company_tickers.json"
        response = requests.get(url, headers=SEC_HEADERS, timeout=30)

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


def get_cik_for_ticker(ticker):
    """Get CIK for a ticker, updating mapping if needed"""
    mapping = load_cik_mapping()

    # Check if mapping needs refresh
    if mapping.get('updated'):
        updated = datetime.fromisoformat(mapping['updated'])
        if datetime.now() - updated > timedelta(days=CIK_CACHE_DAYS):
            mapping = update_cik_mapping()
    elif not mapping.get('tickers'):
        mapping = update_cik_mapping()

    ticker_info = mapping.get('tickers', {}).get(ticker.upper())
    if ticker_info:
        return ticker_info['cik']
    return None


# --- Company EPS Data (per-ticker files) ---

def get_company_cache_path(ticker):
    """Get path to company cache file"""
    return os.path.join(COMPANIES_DIR, f"{ticker.upper()}.json")


def load_company_cache(ticker):
    """Load cached data for a single company"""
    filepath = get_company_cache_path(ticker)
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def save_company_cache(ticker, data):
    """Save data for a single company"""
    ensure_directories()
    filepath = get_company_cache_path(ticker)
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)


def fetch_company_eps(ticker, cik):
    """Fetch EPS data from SEC EDGAR for a company"""
    try:
        rate_limit()
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        response = requests.get(url, headers=SEC_HEADERS, timeout=30)

        if response.status_code == 200:
            data = response.json()
            us_gaap = data.get('facts', {}).get('us-gaap', {})

            # Extract both diluted and basic EPS to compare
            def extract_annual_eps(field_name):
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
                                    'end': r.get('end')
                                }
                return annual

            diluted_eps = extract_annual_eps('EarningsPerShareDiluted')
            basic_eps = extract_annual_eps('EarningsPerShareBasic')

            if not diluted_eps and not basic_eps:
                return None

            # Combine: for each year, take the lower (more conservative) value
            all_years = set(diluted_eps.keys()) | set(basic_eps.keys())
            annual_eps = {}

            for fy in all_years:
                diluted = diluted_eps.get(fy)
                basic = basic_eps.get(fy)

                if diluted and basic:
                    # Both available - take the lower (more conservative) value
                    if diluted['eps'] <= basic['eps']:
                        annual_eps[fy] = {**diluted, 'eps_type': 'diluted'}
                    else:
                        annual_eps[fy] = {**basic, 'eps_type': 'basic'}
                elif diluted:
                    annual_eps[fy] = {**diluted, 'eps_type': 'diluted'}
                else:
                    annual_eps[fy] = {**basic, 'eps_type': 'basic'}

            # Sort by year descending
            sorted_eps = sorted(annual_eps.values(), key=lambda x: x['year'], reverse=True)

            return {
                'ticker': ticker,
                'cik': cik,
                'company_name': data.get('entityName', ticker),
                'eps_history': sorted_eps[:8],  # Keep up to 8 years max
                'updated': datetime.now().isoformat()
            }
    except Exception as e:
        print(f"[SEC] Error fetching EPS for {ticker}: {e}")

    return None


def fetch_company_metrics(ticker, cik):
    """Fetch key financial metrics from SEC EDGAR for a company"""
    try:
        rate_limit()
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        response = requests.get(url, headers=SEC_HEADERS, timeout=30)

        if response.status_code == 200:
            data = response.json()
            us_gaap = data.get('facts', {}).get('us-gaap', {})

            def get_latest_annual(field_name, unit='USD'):
                """Get most recent annual value for a field"""
                if field_name not in us_gaap:
                    return None
                unit_key = 'USD/shares' if unit == 'USD/shares' else unit
                records = us_gaap[field_name].get('units', {}).get(unit_key, [])
                # Filter to 10-K annual records
                annual = [r for r in records if r.get('form') == '10-K'
                         and r.get('frame') and 'Q' not in r.get('frame', '')]
                if not annual:
                    return None
                # Get most recent by frame
                latest = max(annual, key=lambda x: x.get('frame', ''))
                try:
                    year = int(latest.get('frame', '').replace('CY', ''))
                except ValueError:
                    year = None
                return {
                    'value': latest.get('val'),
                    'year': year,
                    'period_start': latest.get('start'),
                    'period_end': latest.get('end'),
                    'filed': latest.get('filed')
                }

            # EPS Metrics
            eps_metrics = {}
            eps_fields = [
                ('EarningsPerShareBasic', 'Basic EPS'),
                ('EarningsPerShareDiluted', 'Diluted EPS'),
                ('IncomeLossFromContinuingOperationsPerBasicShare', 'Continuing Ops EPS (Basic)'),
                ('IncomeLossFromContinuingOperationsPerDilutedShare', 'Continuing Ops EPS (Diluted)'),
                ('IncomeLossFromDiscontinuedOperationsNetOfTaxPerBasicShare', 'Discontinued Ops EPS (Basic)'),
                ('IncomeLossFromDiscontinuedOperationsNetOfTaxPerDilutedShare', 'Discontinued Ops EPS (Diluted)'),
            ]

            for field, label in eps_fields:
                result = get_latest_annual(field, 'USD/shares')
                if result:
                    eps_metrics[field] = {
                        'label': label,
                        **result
                    }

            return {
                'ticker': ticker,
                'cik': cik,
                'company_name': data.get('entityName', ticker),
                'eps_metrics': eps_metrics,
                'fetched': datetime.now().isoformat()
            }
    except Exception as e:
        print(f"[SEC] Error fetching metrics for {ticker}: {e}")

    return None


def get_sec_metrics(ticker):
    """Get SEC metrics for a ticker (fetches fresh each time for now)"""
    ticker = ticker.upper()
    cik = get_cik_for_ticker(ticker)
    if not cik:
        return None
    return fetch_company_metrics(ticker, cik)


def get_sec_eps(ticker):
    """Get SEC EPS data for a ticker, using cache when available"""
    ticker = ticker.upper()
    cached = load_company_cache(ticker)

    # Check if cache is fresh enough
    if cached and cached.get('updated'):
        try:
            updated = datetime.fromisoformat(cached['updated'])
            if datetime.now() - updated < timedelta(days=EPS_CACHE_DAYS):
                return cached
        except (ValueError, TypeError):
            pass

    # Fetch fresh data
    cik = get_cik_for_ticker(ticker)
    if not cik:
        return None

    data = fetch_company_eps(ticker, cik)
    if data:
        save_company_cache(ticker, data)
        return data

    # Return stale cache if fetch failed
    return cached


def is_cache_stale(ticker):
    """Check if a ticker's cache needs updating"""
    cached = load_company_cache(ticker)
    if not cached:
        return True
    if not cached.get('updated'):
        return True
    try:
        updated = datetime.fromisoformat(cached['updated'])
        return datetime.now() - updated >= timedelta(days=EPS_CACHE_DAYS)
    except (ValueError, TypeError):
        return True


def has_cached_eps(ticker):
    """Check if we have any cached SEC data for a ticker (regardless of staleness)"""
    ticker = ticker.upper()
    cached = load_company_cache(ticker)
    return cached is not None and cached.get('eps_history')


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
    ensure_directories()

    # Migrate legacy cache if exists
    migrate_legacy_cache()

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
    metadata = load_metadata()

    # Count cached companies
    company_count = 0
    if os.path.exists(COMPANIES_DIR):
        company_count = len([f for f in os.listdir(COMPANIES_DIR) if f.endswith('.json')])

    return {
        'cik_mapping': {
            'count': mapping.get('count', 0),
            'updated': mapping.get('updated')
        },
        'companies': {
            'count': company_count,
            'last_full_update': metadata.get('last_full_update')
        }
    }


# --- Migration ---

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
    if not os.path.exists(COMPANIES_DIR):
        return {'needs_update': [], 'recently_updated': [], 'details': {}}

    needs_update = []
    recently_updated = []
    details = {}
    today = datetime.now()

    for filename in os.listdir(COMPANIES_DIR):
        if not filename.endswith('.json'):
            continue

        ticker = filename.replace('.json', '')
        cached = load_company_cache(ticker)

        if not cached or not cached.get('eps_history'):
            continue

        # Get most recent EPS entry
        latest_eps = cached['eps_history'][0] if cached['eps_history'] else None
        if not latest_eps:
            continue

        ticker_info = {
            'ticker': ticker,
            'company_name': cached.get('company_name', ticker),
            'latest_fy': latest_eps.get('year'),
            'fiscal_year_end': latest_eps.get('end'),
            'last_filing_date': latest_eps.get('filed'),
            'cache_updated': cached.get('updated'),
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

        last_filing = None
        if latest_eps.get('filed'):
            try:
                last_filing = datetime.strptime(latest_eps['filed'], '%Y-%m-%d')
                ticker_info['last_filing_parsed'] = last_filing.strftime('%b %d, %Y')
            except ValueError:
                pass

        cache_updated = None
        if cached.get('updated'):
            try:
                cache_updated = datetime.fromisoformat(cached['updated'])
            except ValueError:
                pass

        # Determine if new filing might be available
        # Logic: If fiscal year ended 90+ days ago AND cache hasn't been updated since then,
        # a new 10-K might be available

        if fiscal_year_end:
            # Calculate next fiscal year end (approximately 1 year from last)
            next_fy_end = fiscal_year_end.replace(year=fiscal_year_end.year + 1)

            # Companies file 10-K within 60-90 days of fiscal year end
            # Use 75 days as middle ground
            expected_filing_date = next_fy_end + timedelta(days=75)
            ticker_info['next_fy_end'] = next_fy_end.strftime('%b %d, %Y')
            ticker_info['expected_filing'] = expected_filing_date.strftime('%b %d, %Y')

            days_since_fy_end = (today - next_fy_end).days

            if days_since_fy_end > 75:
                # Fiscal year ended 75+ days ago - new filing likely available
                if cache_updated:
                    days_since_update = (today - cache_updated).days
                    if cache_updated < expected_filing_date:
                        # Cache was updated before expected filing date
                        ticker_info['status'] = 'update_recommended'
                        ticker_info['reason'] = f'FY{latest_eps.get("year")+1} 10-K likely available (FY ended {days_since_fy_end} days ago)'
                        ticker_info['days_since_fy_end'] = days_since_fy_end
                        ticker_info['priority'] = 'high' if days_since_fy_end > 120 else 'medium'
                        needs_update.append(ticker)
                    else:
                        recently_updated.append(ticker)
                else:
                    ticker_info['status'] = 'update_recommended'
                    ticker_info['reason'] = f'FY{latest_eps.get("year")+1} 10-K likely available'
                    needs_update.append(ticker)
            elif days_since_fy_end > 0:
                # Fiscal year ended but too soon for 10-K
                ticker_info['status'] = 'pending'
                ticker_info['reason'] = f'FY ended {days_since_fy_end} days ago, 10-K expected in ~{75 - days_since_fy_end} days'
            else:
                # Fiscal year hasn't ended yet
                ticker_info['status'] = 'current'
                ticker_info['reason'] = f'Current FY ends {next_fy_end.strftime("%b %d, %Y")}'
                recently_updated.append(ticker)
        else:
            # No fiscal year end info - check if cache is stale
            if is_cache_stale(ticker):
                ticker_info['status'] = 'stale'
                ticker_info['reason'] = 'Cache is stale (no fiscal year info available)'
                needs_update.append(ticker)
            else:
                recently_updated.append(ticker)

        details[ticker] = ticker_info

    # Sort needs_update by priority (high first) then by days since FY end
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


def migrate_legacy_cache():
    """Migrate old single-file cache to per-ticker files"""
    if not os.path.exists(LEGACY_CACHE_FILE):
        return

    try:
        with open(LEGACY_CACHE_FILE, 'r') as f:
            legacy = json.load(f)

        companies = legacy.get('companies', {})
        if companies:
            print(f"[SEC] Migrating {len(companies)} companies from legacy cache...")
            ensure_directories()

            for ticker, data in companies.items():
                save_company_cache(ticker, data)

            # Update metadata with legacy update time
            metadata = load_metadata()
            if legacy.get('last_full_update'):
                metadata['last_full_update'] = legacy['last_full_update']
            metadata['migrated_from_legacy'] = datetime.now().isoformat()
            save_metadata(metadata)

            # Remove legacy file after successful migration
            os.remove(LEGACY_CACHE_FILE)
            print(f"[SEC] Migration complete, removed legacy cache file")

    except Exception as e:
        print(f"[SEC] Error migrating legacy cache: {e}")

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
from datetime import datetime, timedelta
import threading

# Import database module for all operations
import database as db

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


def rate_limit():
    """Ensure we don't exceed SEC rate limits"""
    global last_sec_request
    elapsed = time.time() - last_sec_request
    if elapsed < SEC_RATE_LIMIT:
        time.sleep(SEC_RATE_LIMIT - elapsed)
    last_sec_request = time.time()


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
        try:
            updated = datetime.fromisoformat(mapping['updated'])
            if datetime.now() - updated > timedelta(days=CIK_CACHE_DAYS):
                mapping = update_cik_mapping()
        except (ValueError, TypeError):
            mapping = update_cik_mapping()
    elif not mapping.get('tickers'):
        mapping = update_cik_mapping()

    ticker_info = mapping.get('tickers', {}).get(ticker.upper())
    if ticker_info:
        return ticker_info['cik']
    return None


# --- Company EPS Data (now stored in database) ---

def load_company_cache(ticker):
    """Load cached data for a single company from database"""
    return db.get_sec_company(ticker)


def save_company_cache(ticker, data):
    """Save data for a single company to database"""
    db.save_sec_company(ticker, data)


def fetch_company_eps(ticker, cik):
    """Fetch EPS data from SEC EDGAR for a company"""
    try:
        rate_limit()
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        response = requests.get(url, headers=SEC_HEADERS, timeout=30)

        if response.status_code == 200:
            data = response.json()
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

            # Extract all EPS types
            all_eps_data = {}
            for field_name, label in eps_fields:
                eps_data = extract_annual_eps(field_name, label)
                for year, data in eps_data.items():
                    if year not in all_eps_data:
                        all_eps_data[year] = []
                    all_eps_data[year].append(data)

            if not all_eps_data:
                return None

            # For each year, take the lower (more conservative) EPS value
            annual_eps = {}
            for fy, eps_list in all_eps_data.items():
                # Sort by EPS value (ascending) and take the lowest
                eps_list.sort(key=lambda x: x['eps'])
                annual_eps[fy] = eps_list[0]

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
    """Fetch key financial metrics from SEC EDGAR for a company.

    Returns multi-year EPS data organized by type (for matrix display)
    and annual dividend data.
    """
    try:
        rate_limit()
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        response = requests.get(url, headers=SEC_HEADERS, timeout=30)

        if response.status_code == 200:
            data = response.json()
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
            next_fy_end = fiscal_year_end.replace(year=fiscal_year_end.year + 1)
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
        response = requests.get(url, headers=SEC_HEADERS, timeout=30)

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

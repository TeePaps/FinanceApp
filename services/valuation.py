"""
Stock valuation calculations service.

Provides:
- EPS data validation (SEC vs yfinance)
- Fair value calculation using EPS averaging
- Valuation summary generation
- Split warning computation
"""

import math
from datetime import datetime, timedelta
from typing import List, Dict
from config import (
    PE_RATIO_MULTIPLIER, RECOMMENDED_EPS_YEARS,
    ESTIMATED_VALUE_RATIO_LOW, ESTIMATED_VALUE_RATIO_HIGH,
    SPLIT_WARNING_LOOKBACK_YEARS, SPLIT_WARNING_RECENT_YEARS, SPLIT_WARNING_MIN_RATIO,
)


def get_split_adjusted_eps_history(ticker: str, eps_history_map: Dict = None,
                                   splits_map: Dict = None) -> List[Dict]:
    """
    Return eps_history with EPS values adjusted for any splits that occurred
    AFTER each fiscal year. Adjusted values are on the SAME per-share basis as
    the current ticker price, so averages and fair-value math work correctly.

    SEC reports EPS on a pre-split basis. Without adjustment, a company that
    split 25:1 (like BKNG on 2026-04-06) will have a fair value 25x too high
    relative to its post-split price.

    The eps_history table keeps raw SEC values (audit-friendly). Only the
    returned list is adjusted.

    Args:
        ticker: Stock ticker symbol

    Returns:
        List of eps_history rows with `eps` possibly adjusted, plus
        `split_adjusted=True` and `split_adjustment_factor=<ratio>` keys
        on rows that were touched.
    """
    import database as db
    # Callers that process many tickers can pass pre-loaded maps to avoid two
    # connections + two queries per ticker (this runs up to three times per
    # ticker per screener run).
    if eps_history_map is not None:
        history = eps_history_map.get(ticker.upper(), [])
    else:
        history = db.get_eps_history(ticker)
    if not history:
        return []

    if splits_map is not None:
        splits = splits_map.get(ticker.upper(), [])
    else:
        splits = db.get_splits(ticker)
    if not splits:
        return [dict(row) for row in history]

    adjusted = []
    for row in history:
        eps = row.get('eps')
        if eps is None:
            adjusted.append(dict(row))
            continue

        # Use period_end as the fiscal-year cutoff; fall back to filed date,
        # then to Jan 1 of (year + 1). Conservative — any split in year Y
        # is attributed to AFTER fiscal year Y.
        cutoff = row.get('period_end') or row.get('filed')
        if not cutoff:
            year = row.get('year')
            cutoff = f"{int(year) + 1}-01-01" if year else None

        cumulative = 1.0
        if cutoff:
            for s in splits:
                d = s.get('date')
                ratio = s.get('ratio')
                if not d or not ratio or ratio <= 0:
                    continue
                if d > cutoff:
                    cumulative *= float(ratio)

        new_row = dict(row)
        if cumulative != 1.0:
            new_row['eps'] = eps / cumulative
            new_row['split_adjusted'] = True
            new_row['split_adjustment_factor'] = cumulative
        adjusted.append(new_row)
    return adjusted


def average_split_adjusted_eps(history: List[Dict], years: int = RECOMMENDED_EPS_YEARS):
    """
    Average the newest `years` usable EPS values from a newest-first history list
    (as returned by get_split_adjusted_eps_history / db.get_eps_history).

    Rows with eps=None don't consume window slots and don't dilute the average —
    summing only non-None values while dividing by the full window length would
    bias fair value low whenever a year is missing.

    Returns (eps_avg, years_used); (None, 0) when no usable rows exist.
    """
    valid = [row['eps'] for row in history if row.get('eps') is not None][:years]
    if not valid:
        return None, 0
    return sum(valid) / len(valid), len(valid)


def compute_estimated_value(eps_avg, annual_dividend, current_price=None):
    """
    Canonical fair-value computation: (eps_avg + annual_dividend) * multiplier,
    with sanity guards that return (None, None) when the result is meaningless.

    Returns (estimated_value, price_vs_value). Either may be None.

    Sanity rules — return (None, None) when:
      - eps_avg is None or eps_avg <= 0  (e.g., company averaged losses)
      - computed value <= 0              (defense in depth)
      - current_price > 0 AND the value is outside [LOW * price, HIGH * price]
        (catches tickers whose SEC EPS doesn't fit the per-share formula,
         e.g. BRK-B's class B share structure giving avg EPS of $0.01)

    Called from calculate_valuation() and all four screener variants so the
    same rules apply everywhere fair value is built.
    """
    if eps_avg is None or eps_avg <= 0:
        return None, None
    annual_dividend = annual_dividend or 0
    ev = (eps_avg + annual_dividend) * PE_RATIO_MULTIPLIER
    if ev <= 0:
        return None, None
    if current_price is not None and current_price > 0:
        low_bound = ESTIMATED_VALUE_RATIO_LOW * current_price
        high_bound = ESTIMATED_VALUE_RATIO_HIGH * current_price
        if ev < low_bound or ev > high_bound:
            return None, None
        pvv = ((current_price - ev) / ev) * 100
        return round(ev, 2), round(pvv, 1)
    return round(ev, 2), None


def get_validated_eps(ticker):
    """
    Get EPS data using the orchestrator (SEC EDGAR first, then yfinance fallback).

    SEC EDGAR data uses company fiscal year and is the official source.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Tuple of (eps_data, source, validation_info)
        - eps_data: List of dicts with year and eps values
        - source: 'sec_edgar', 'yfinance', or 'none'
        - validation_info: Dict with validation details
    """
    ticker = ticker.upper()
    validation_info = {'validated': False, 'years_available': 0}

    # Use orchestrator to fetch EPS (handles SEC-first-then-yfinance fallback)
    from services.providers import get_orchestrator
    orchestrator = get_orchestrator()

    result = orchestrator.fetch_eps(ticker)

    if result.success and result.data:
        eps_data = result.data

        # Convert orchestrator format to expected format
        eps_list = []
        for entry in eps_data.eps_history:
            if 'eps' in entry and entry['eps'] is not None:
                eps_entry = {
                    'year': int(entry['year']),
                    'eps': float(entry['eps'])
                }
                # Add optional fields if present
                if 'eps_type' in entry:
                    eps_entry['eps_type'] = entry['eps_type']
                if 'period_start' in entry:
                    eps_entry['period_start'] = entry['period_start']
                if 'period_end' in entry:
                    eps_entry['period_end'] = entry['period_end']
                eps_list.append(eps_entry)

        # Determine validation info based on source
        source = eps_data.source
        # Include company name from EPS data if available
        sec_company_name = eps_data.company_name

        if source == 'sec_edgar':
            validation_info = {
                'validated': True,
                'source': 'SEC EDGAR 10-K filings',
                'years_available': len(eps_list),
                'fiscal_year': True,
                'company_name': sec_company_name
            }
            return eps_list[:8], 'sec', validation_info
        elif source == 'yfinance':
            validation_info = {
                'validated': False,
                'source': 'yfinance (SEC data not available)',
                'years_available': len(eps_list),
                'company_name': sec_company_name
            }
            return eps_list[:8], 'yfinance', validation_info
        else:
            # Other sources (defeatbeta, etc.)
            validation_info = {
                'validated': True,
                'source': source,
                'years_available': len(eps_list),
                'company_name': sec_company_name
            }
            return eps_list[:8], source, validation_info

    return [], 'none', validation_info


def needs_split_refresh(ticker, last_checked=None):
    """Is this ticker's split history older than the configured cache window?

    Splits are rare corporate actions; split_cache_days governs how often we
    look. Shared by the screener's splits phase and refresh_splits() so both
    apply the same policy - previously only the screener had a gate, and every
    single /api/valuation request fired a live split fetch.
    """
    import database as db
    from services.providers import get_config as get_provider_config

    if last_checked is None:
        last_checked = db.get_split_history_last_updated(ticker.upper())
    if not last_checked:
        return True

    cutoff = (datetime.now() - timedelta(
        days=get_provider_config().split_cache_days)).isoformat()
    return last_checked < cutoff


def refresh_splits(ticker, orchestrator=None, force=False):
    """
    Fetch split history for a ticker from the provider chain and persist it.

    Safe to call from any code path (screener, single-ticker refresh, analyze).
    Silently swallows fetch errors — this feature is informational and must
    not break the main valuation flow.

    Skips the network entirely when the stored split data is still within
    split_cache_days (pass force=True for an explicit user refresh).

    Args:
        ticker: Stock ticker symbol
        orchestrator: Optional DataOrchestrator (fetched lazily if None)
        force: Ignore the freshness gate and always re-fetch
    """
    import database as db
    ticker = ticker.upper()

    try:
        if not force and not needs_split_refresh(ticker):
            return

        if orchestrator is None:
            from services.providers import get_orchestrator
            orchestrator = get_orchestrator()

        result = orchestrator.fetch_splits(ticker)
        if result.success and result.data:
            if result.data.splits:
                db.upsert_splits(ticker, result.data.splits, source=result.source or 'unknown')
            # Record the check either way, so a ticker that has simply never
            # split is not re-fetched on every subsequent run.
            db.record_split_checks([ticker])
    except Exception:
        pass


def compute_split_warning(ticker):
    """
    Build the `split_warning` payload for a ticker from persisted split history.

    Only flags splits with ratio >= SPLIT_WARNING_MIN_RATIO within the last
    SPLIT_WARNING_LOOKBACK_YEARS. Severity is 'recent' if any flagged split
    is within SPLIT_WARNING_RECENT_YEARS, otherwise 'historical'.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dict with warning fields when active; None otherwise.
    """
    import database as db
    ticker = ticker.upper()

    now = datetime.now()
    cutoff_date = (now - timedelta(days=365 * SPLIT_WARNING_LOOKBACK_YEARS)).strftime('%Y-%m-%d')
    recent_cutoff_date = (now - timedelta(days=365 * SPLIT_WARNING_RECENT_YEARS)).strftime('%Y-%m-%d')

    all_splits = db.get_splits(ticker, since_date=cutoff_date)
    qualifying = [
        s for s in all_splits
        if s.get('ratio') is not None and s['ratio'] >= SPLIT_WARNING_MIN_RATIO
    ]

    if not qualifying:
        return None

    severity = 'recent' if any(s['date'] >= recent_cutoff_date for s in qualifying) else 'historical'
    most_recent = qualifying[0]  # get_splits returns newest-first

    note = (
        f"{len(qualifying)} stock split(s) in the last {SPLIT_WARNING_LOOKBACK_YEARS}-year EPS window — "
        "fair value may be skewed because historical EPS is on a pre-split basis."
    )

    return {
        'active': True,
        'severity': severity,
        'count': len(qualifying),
        'most_recent_date': most_recent['date'],
        'most_recent_ratio': most_recent['ratio'],
        'splits': [{'date': s['date'], 'ratio': s['ratio']} for s in qualifying],
        'lookback_years': SPLIT_WARNING_LOOKBACK_YEARS,
        'min_ratio': SPLIT_WARNING_MIN_RATIO,
        'note': note,
    }


def calculate_valuation(ticker):
    """
    Calculate stock valuation using EPS and dividend formula.

    Formula: (Average EPS + Annual Dividend) x PE_RATIO_MULTIPLIER

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dict with valuation data
    """
    ticker = ticker.upper()

    try:
        # Fetch data using orchestrator
        from services.providers import get_orchestrator
        orchestrator = get_orchestrator()

        # Get company info
        info_result = orchestrator.fetch_stock_info(ticker)
        if info_result.success and info_result.data:
            info_data = info_result.data
            company_name = info_data.company_name
            fifty_two_week_high = info_data.fifty_two_week_high
            fifty_two_week_low = info_data.fifty_two_week_low
        else:
            company_name = ticker
            fifty_two_week_high = None
            fifty_two_week_low = None

        # Fetch current price from provider system
        price_result = orchestrator.fetch_price(ticker)
        current_price = price_result.data if price_result.success else 0
        price_source = price_result.source if price_result.success else 'none'

        # Get validated EPS data using orchestrator
        eps_data, eps_source, validation_info = get_validated_eps(ticker)

        # Use company name from EPS data if available (SEC or other authoritative source)
        # But only if it's a real name, not just the ticker repeated
        sec_name = validation_info.get('company_name')
        if sec_name and sec_name.upper() != ticker:
            company_name = sec_name

        # Get dividend info using orchestrator
        dividend_result = orchestrator.fetch_dividends(ticker)
        annual_dividend = 0
        dividend_info = []

        if dividend_result.success and dividend_result.data:
            dividend_data = dividend_result.data
            annual_dividend = dividend_data.annual_dividend
            dividend_info = dividend_data.payments

        # yfinance flakiness guard: if fresh fetch returned 0 but the cache
        # has a non-zero dividend, the fresh fetch is almost certainly wrong.
        # Preserve the cached value instead of producing a bogus fair value.
        # Note: the explicit /api/valuation/<ticker>/refresh endpoint bypasses
        # calculate_valuation and writes the fresh value directly, so a user
        # who really wants to clear a dividend can still do so.
        if not annual_dividend or annual_dividend <= 0:
            import database as db
            cached = db.get_valuation(ticker) or {}
            cached_div = cached.get('annual_dividend') or 0
            if cached_div > 0:
                from logger import log
                log.warning(
                    f"[{ticker}] fresh dividend fetch returned 0; "
                    f"preserving cached ${cached_div:.2f}"
                )
                annual_dividend = cached_div

        # Get selloff metrics via orchestrator
        selloff_metrics = None
        selloff_result = orchestrator.fetch_selloff(ticker)
        if selloff_result.success and selloff_result.data:
            selloff_data = selloff_result.data
            selloff_metrics = {
                'day': selloff_data.day,
                'week': selloff_data.week,
                'month': selloff_data.month,
                'avg_volume': selloff_data.avg_volume,
                'severity': selloff_data.severity
            }

        # Refresh split history (persist to DB) and compute the Split Warning.
        # Informational only — never affects fair value in this iteration.
        refresh_splits(ticker, orchestrator=orchestrator)
        split_warning = compute_split_warning(ticker)

        # Calculate valuation via the shared helper (sanity checks + None on bad input).
        # Prefer split-adjusted EPS from the eps_history table when available — that
        # keeps fair value consistent across splits (e.g. BKNG 25:1 on 2026-04-06).
        # Fall back to the freshly-validated EPS list when eps_history isn't populated.
        eps_avg = None
        adjusted = get_split_adjusted_eps_history(ticker)
        if adjusted:
            eps_avg, _ = average_split_adjusted_eps(adjusted)
            # Display the same per-share basis the average uses. Returning the
            # raw fetched list alongside the adjusted average made the EPS
            # table read as nonsense after a split (BKNG: rows of ~$165
            # "averaging" to $3.78).
            display_rows = [r for r in adjusted if r.get('eps') is not None][:RECOMMENDED_EPS_YEARS]
            if display_rows:
                keep = ('year', 'eps', 'eps_type', 'period_start', 'period_end',
                        'split_adjusted', 'split_adjustment_factor')
                eps_data = [
                    {k: r[k] for k in keep if r.get(k) is not None}
                    for r in display_rows
                ]
        if eps_avg is None and len(eps_data) > 0:
            eps_avg = sum(e['eps'] for e in eps_data) / len(eps_data)
        estimated_value, price_vs_value = compute_estimated_value(
            eps_avg, annual_dividend, current_price
        )

        return {
            'ticker': ticker,
            'company_name': company_name,
            'current_price': round(current_price, 2) if current_price else None,
            'eps_data': eps_data,
            'eps_years': len(eps_data),
            'eps_source': eps_source,
            'eps_validation': validation_info,
            'eps_avg': round(eps_avg, 2) if eps_avg else None,
            'min_years_recommended': RECOMMENDED_EPS_YEARS,
            'has_enough_years': len(eps_data) >= RECOMMENDED_EPS_YEARS,
            'annual_dividend': round(annual_dividend, 2),
            'dividend_payments': dividend_info,
            'estimated_value': round(estimated_value, 2) if estimated_value else None,
            'price_vs_value': round(price_vs_value, 1) if price_vs_value is not None else None,
            'formula': f'(({round(eps_avg, 2) if eps_avg else "N/A"} avg EPS) + {round(annual_dividend, 2)} dividend) x {PE_RATIO_MULTIPLIER} = ${round(estimated_value, 2) if estimated_value else "N/A"}',
            'selloff': selloff_metrics,
            'split_warning': split_warning,
        }
    except Exception as e:
        print(f"Error calculating valuation for {ticker}: {e}")
        return {
            'ticker': ticker,
            'error': str(e)
        }

"""
Star Scoring System.

Computes a 6-criterion star rating for each tracked ticker after the
screener's valuation phase completes:

  1. Earnings Beat       — most recent reported EPS > prior consensus estimate
  2. Fair Value Up       — current 8-year-formula fair value > value 1 year ago
  3. Dividend Up         — trailing-12mo dividend > TTM dividend ending 1y ago
  4. Debt-to-Capital ≤25% — (LTD + STD) / (LTD + STD + Equity) ≤ 0.25
  5. Share Buybacks      — current shares < shares at earliest buy date (holdings only)
  6. Undervalued         — current_price < estimated_value (holdings only)

Missing data → criterion unearned (0 stars), never an error. Stars are
persisted to the `star_ratings` table for fast tab loads.
"""

from datetime import datetime
from typing import Dict, Optional, Set

import database as db
from config import DEBT_TO_CAPITAL_THRESHOLD
from services.activity_log import activity_log
from services.providers import get_orchestrator
from services.providers.yfinance_provider import fetch_yearly_dividends


# -----------------------------------------------------------------------------
# Helper: who's a holding?
# -----------------------------------------------------------------------------

def _holdings_ticker_set() -> Set[str]:
    """Return uppercase tickers with current shares > 0 (FIFO-aware)."""
    from services.holdings import calculate_holdings
    holdings = calculate_holdings(confirmed_only=False)
    return {t.upper() for t, h in holdings.items() if h.get('shares', 0) > 0}


# -----------------------------------------------------------------------------
# Criterion 1: Earnings Beat
# -----------------------------------------------------------------------------

def _check_earnings_beat(ticker: str) -> bool:
    """Star earned when most recent reported EPS > prior consensus estimate."""
    try:
        orch = get_orchestrator()
        result = orch.fetch_analyst_estimates(ticker)
        if not result.success or not result.data:
            return False
        actual = result.data.eps_actual
        estimate = result.data.eps_estimate
        if actual is None or estimate is None:
            return False
        return actual > estimate
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Criterion 2: Fair Value Up Year-over-Year
# -----------------------------------------------------------------------------

def _prior_year_fair_value_from_eps(ticker: str, annual_dividend_year_ago: float) -> Optional[float]:
    """
    Backfill: recompute fair value as it would have been 1 year ago using the
    EPS history table — 8-year EPS average ending 1 year ago × 10, with
    prior-year dividend included in the formula (same shape as today's).
    """
    eps_history = db.get_eps_history(ticker)
    if not eps_history:
        return None
    current_year = datetime.now().year
    # Window: years T-9 through T-2 inclusive (8 years ending 1 year ago)
    window_years = set(range(current_year - 9, current_year - 1))
    window_eps = [row['eps'] for row in eps_history
                  if row.get('eps') is not None and row.get('year') in window_years]
    if not window_eps:
        return None
    avg = sum(window_eps) / len(window_eps)
    if avg <= 0:
        return None
    return (avg + (annual_dividend_year_ago or 0)) * 10


def _check_fair_value_up(ticker: str, current_estimated_value: Optional[float],
                        yearly_dividends: Optional[Dict[int, float]] = None) -> bool:
    """
    Star earned when current estimated_value > estimated_value snapshot from
    ~1 year ago. Snapshot is taken from valuation_history if present, otherwise
    computed by shifting the EPS window back 1 year (backfill).
    """
    if current_estimated_value is None or current_estimated_value <= 0:
        return False

    # Prefer a stored snapshot from ~1 year ago (most recent that's old enough).
    history = db.get_valuation_history(ticker)
    if history:
        target = datetime.now().replace(microsecond=0)
        target_str = target.replace(year=target.year - 1).strftime('%Y-%m-%d')
        prior = next((row for row in history if row['snapshot_date'] <= target_str), None)
        if prior and prior.get('estimated_value'):
            return current_estimated_value > prior['estimated_value']

    # Backfill from eps_history + yearly dividends.
    prior_year = datetime.now().year - 1
    div_year_ago = 0.0
    if yearly_dividends:
        div_year_ago = float(yearly_dividends.get(prior_year, 0.0))
    backfilled = _prior_year_fair_value_from_eps(ticker, div_year_ago)
    if backfilled is None or backfilled <= 0:
        return False
    return current_estimated_value > backfilled


# -----------------------------------------------------------------------------
# Criterion 3: Dividend Up Year-over-Year
# -----------------------------------------------------------------------------

def _check_dividend_up(ticker: str, current_annual_dividend: Optional[float],
                      yearly_dividends: Optional[Dict[int, float]] = None) -> bool:
    """
    Star earned when current trailing-12mo dividend > dividend total for the
    prior calendar year. yearly_dividends is the yfinance year->total dict.
    """
    if not current_annual_dividend or current_annual_dividend <= 0:
        return False
    if not yearly_dividends:
        # Fall back to the snapshot table
        history = db.get_dividend_history(ticker)
        prior_year = datetime.now().year - 1
        prior_div = history.get(prior_year)
        if prior_div is None or prior_div <= 0:
            return False
        return current_annual_dividend > prior_div

    prior_year = datetime.now().year - 1
    prior_div = yearly_dividends.get(prior_year)
    if prior_div is None or prior_div <= 0:
        return False
    return current_annual_dividend > prior_div


# -----------------------------------------------------------------------------
# Criterion 4: Debt-to-Capital ≤ 25%
# -----------------------------------------------------------------------------

def _check_debt_to_capital_low(ticker: str) -> bool:
    """Star earned when debt-to-capital ratio is ≤ DEBT_TO_CAPITAL_THRESHOLD."""
    # Use cached balance sheet first; refresh only via screener phase.
    bs = db.get_balance_sheet(ticker)
    if not bs:
        return False
    dtc = bs.get('debt_to_capital')
    if dtc is None:
        return False
    return dtc <= DEBT_TO_CAPITAL_THRESHOLD


# -----------------------------------------------------------------------------
# Criterion 5: Share Buybacks Since Buy Date (holdings only)
# -----------------------------------------------------------------------------

def _check_shares_buyback_since_buy(ticker: str, first_buy_date: Optional[str]) -> bool:
    """
    Star earned when current shares outstanding < shares outstanding at
    the earliest buy date for this ticker.
    """
    if not first_buy_date:
        return False
    history = db.get_shares_outstanding_history(ticker)
    if not history:
        return False
    # history is sorted by date asc. Find the entry closest-and-before
    # (or equal to) first_buy_date; if no entries before, use the first.
    at_buy = None
    for row in history:
        if row['as_of_date'] <= first_buy_date:
            at_buy = row
        else:
            break
    # If we have nothing on or before the buy date, the earliest record
    # we have is already AFTER the buy, so we can't make the comparison.
    if at_buy is None:
        return False
    current = history[-1]
    if not at_buy.get('shares') or not current.get('shares'):
        return False
    return current['shares'] < at_buy['shares']


# -----------------------------------------------------------------------------
# Criterion 6: Undervalued (holdings only)
# -----------------------------------------------------------------------------

def _check_undervalued(current_price: Optional[float], estimated_value: Optional[float]) -> bool:
    if current_price is None or estimated_value is None or estimated_value <= 0:
        return False
    return current_price < estimated_value


# -----------------------------------------------------------------------------
# Snapshot helpers used during the screener phase
# -----------------------------------------------------------------------------

def _snapshot_today(ticker: str, valuation: Dict, yearly_dividends: Dict[int, float]):
    """Persist today's valuation + this-year dividend total + any fresh shares."""
    today = datetime.now().strftime('%Y-%m-%d')
    db.snapshot_valuation(
        ticker, today,
        estimated_value=valuation.get('estimated_value'),
        eps_avg=valuation.get('eps_avg'),
        annual_dividend=valuation.get('annual_dividend'),
    )
    current_year = datetime.now().year
    this_year_div = yearly_dividends.get(current_year)
    if this_year_div is not None:
        db.snapshot_dividend_year(ticker, current_year, this_year_div)
    prior_year_div = yearly_dividends.get(current_year - 1)
    if prior_year_div is not None:
        db.snapshot_dividend_year(ticker, current_year - 1, prior_year_div)


def _refresh_balance_sheet(ticker: str):
    """Fetch + persist the latest balance sheet. Silent on failure."""
    try:
        result = get_orchestrator().fetch_balance_sheet(ticker)
        if not result.success or not result.data:
            return
        d = result.data
        ltd = d.long_term_debt or 0
        std = d.short_term_debt or 0
        equity = d.stockholders_equity
        if equity is None:
            return
        denom = ltd + std + equity
        dtc = ((ltd + std) / denom) if denom > 0 else None
        db.update_balance_sheet(ticker, {
            'long_term_debt': d.long_term_debt,
            'short_term_debt': d.short_term_debt,
            'stockholders_equity': equity,
            'debt_to_capital': dtc,
            'as_of_date': d.as_of_date,
            'source': d.source,
        })
    except Exception:
        pass


def _refresh_shares_outstanding(ticker: str):
    """Fetch + persist shares-outstanding history. Silent on failure."""
    try:
        result = get_orchestrator().fetch_shares_outstanding(ticker)
        if not result.success or not result.data:
            return
        for entry in result.data.history or []:
            date = entry.get('date')
            shares = entry.get('shares')
            if not date or shares is None:
                continue
            db.snapshot_shares_outstanding(ticker, date, shares, source=entry.get('source'))
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def calculate_stars(ticker: str, valuation: Dict, is_holding: bool,
                    first_buy_date: Optional[str] = None,
                    yearly_dividends: Optional[Dict[int, float]] = None) -> Dict:
    """
    Compute the 6 criterion booleans + total for a single ticker.

    Caller is responsible for refreshing balance sheet / shares outstanding
    beforehand if fresh values are wanted (see `_refresh_*` helpers).
    """
    current_price = valuation.get('current_price')
    estimated_value = valuation.get('estimated_value')
    annual_dividend = valuation.get('annual_dividend')

    earnings_beat = _check_earnings_beat(ticker)
    fair_value_up = _check_fair_value_up(ticker, estimated_value, yearly_dividends)
    dividend_up = _check_dividend_up(ticker, annual_dividend, yearly_dividends)
    debt_low = _check_debt_to_capital_low(ticker)
    # Undervalued applies to all tickers (criterion 5)
    undervalued = _check_undervalued(current_price, estimated_value)
    # Share buybacks: holdings-only (criterion 6)
    buyback = _check_shares_buyback_since_buy(ticker, first_buy_date) if is_holding else False

    return {
        'earnings_beat': earnings_beat,
        'fair_value_up': fair_value_up,
        'dividend_up': dividend_up,
        'debt_to_capital_low': debt_low,
        'shares_buyback': buyback,
        'undervalued': undervalued,
        'is_holding': is_holding,
    }


def calculate_all_star_ratings(tickers=None, progress_callback=None) -> int:
    """
    Phase entry point. Walk tickers, refresh per-ticker data (balance sheet,
    shares outstanding, dividend history), compute stars, and bulk-write to
    the star_ratings table.

    Args:
        tickers: optional iterable of tickers to process. Defaults to all
                 tickers in the valuations table.
        progress_callback: optional callable(current_count, ticker_symbol)
                 invoked after each ticker. Used by the screener phase to
                 update its progress bar.

    Returns the number of tickers rated.
    """
    valuations = db.get_all_valuations()
    if tickers is None:
        ticker_list = sorted(valuations.keys())
    else:
        ticker_list = sorted({t.upper() for t in tickers})

    if not ticker_list:
        return 0

    holdings = _holdings_ticker_set()

    activity_log.log("info", "stars", f"Calculating star ratings for {len(ticker_list)} tickers...")

    # Per-ticker rate limit-friendly refreshes happen inside the loop.
    # Each ticker takes 3-4 SEC + yfinance calls, so this phase will be
    # slow on first run. Subsequent runs benefit from cached snapshots.

    ratings_batch: Dict[str, Dict] = {}
    processed = 0

    for ticker in ticker_list:
        valuation = valuations.get(ticker, {})
        is_holding = ticker in holdings

        # Refresh external data sources (writes to balance_sheet + shares_outstanding_history)
        _refresh_balance_sheet(ticker)
        _refresh_shares_outstanding(ticker)

        # Pull yfinance dividend history once (needed for criteria 2 + 3)
        yearly_divs = fetch_yearly_dividends(ticker)

        first_buy = db.get_first_buy_date(ticker) if is_holding else None

        # Snapshot today's valuation + dividend years (for next year's comparisons)
        _snapshot_today(ticker, valuation, yearly_divs)

        rating = calculate_stars(
            ticker, valuation, is_holding,
            first_buy_date=first_buy,
            yearly_dividends=yearly_divs,
        )
        ratings_batch[ticker] = rating
        processed += 1

        if progress_callback is not None:
            try:
                progress_callback(processed, ticker)
            except Exception:
                pass

        # Flush in batches to avoid losing progress
        if len(ratings_batch) >= 50:
            db.bulk_update_star_ratings(ratings_batch)
            ratings_batch.clear()

    if ratings_batch:
        db.bulk_update_star_ratings(ratings_batch)

    activity_log.log("success", "stars", f"Star ratings computed for {processed} tickers")
    return processed


# -----------------------------------------------------------------------------
# Per-criterion explanation (Company Profile page table)
# -----------------------------------------------------------------------------

def _fmt_money(v):
    if v is None:
        return None
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return None


def _fmt_pct(v):
    if v is None:
        return None
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return None


def _fmt_shares(v):
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{n:.0f}"


def _explain_earnings_beat(ticker):
    try:
        result = get_orchestrator().fetch_analyst_estimates(ticker)
    except Exception:
        result = None
    if not result or not result.success or not result.data:
        return {
            'earned': False,
            'summary': None,
            'note': 'No analyst estimate data available',
            'values': {},
        }
    actual = result.data.eps_actual
    estimate = result.data.eps_estimate
    period = result.data.period_end
    if actual is None or estimate is None:
        return {
            'earned': False,
            'summary': None,
            'note': 'Most recent quarter missing actual or estimate',
            'values': {'eps_actual': actual, 'eps_estimate': estimate, 'period_end': period},
        }
    earned = actual > estimate
    # yfinance's surprisePercent is unreliable (sometimes decimal, sometimes pct).
    # Compute from actual/estimate ourselves for a consistent display.
    delta = ((actual - estimate) / abs(estimate) * 100) if estimate else None
    delta_str = _fmt_pct(delta) if delta is not None else ''
    period_str = f" (Q ending {period})" if period else ''
    summary = f"EPS actual ${actual:.2f} vs estimate ${estimate:.2f} → {delta_str}{period_str}"
    return {
        'earned': earned,
        'summary': summary,
        'note': None,
        'values': {
            'eps_actual': actual,
            'eps_estimate': estimate,
            'beat_percent': delta,
            'period_end': period,
        },
    }


def _explain_fair_value_up(ticker, valuation, yearly_dividends):
    current = valuation.get('estimated_value')
    if current is None or current <= 0:
        return {
            'earned': False, 'summary': None,
            'note': 'No fair value computed (missing EPS)',
            'values': {},
        }
    history = db.get_valuation_history(ticker)
    prior = None
    prior_source = None
    if history:
        target = datetime.now().replace(microsecond=0)
        target_str = target.replace(year=target.year - 1).strftime('%Y-%m-%d')
        snap = next((row for row in history if row['snapshot_date'] <= target_str), None)
        if snap and snap.get('estimated_value'):
            prior = snap['estimated_value']
            prior_source = f"snapshot {snap['snapshot_date']}"

    if prior is None:
        prior_year = datetime.now().year - 1
        div_year_ago = float((yearly_dividends or {}).get(prior_year, 0.0))
        prior = _prior_year_fair_value_from_eps(ticker, div_year_ago)
        if prior is not None:
            prior_source = "backfill from eps_history"

    if prior is None or prior <= 0:
        return {
            'earned': False, 'summary': None,
            'note': 'No prior-year fair value available',
            'values': {'fair_value_now': current},
        }
    delta_pct = ((current - prior) / prior) * 100
    earned = current > prior
    summary = f"Now {_fmt_money(current)} / 1y ago {_fmt_money(prior)} (Δ {_fmt_pct(delta_pct)})"
    return {
        'earned': earned, 'summary': summary, 'note': None,
        'values': {
            'fair_value_now': current,
            'fair_value_prior_year': prior,
            'delta_pct': delta_pct,
            'source': prior_source,
        },
    }


def _explain_dividend_up(ticker, valuation, yearly_dividends):
    current_div = valuation.get('annual_dividend')
    if not current_div or current_div <= 0:
        return {
            'earned': False, 'summary': None,
            'note': 'No dividend paid',
            'values': {'dividend_now': current_div},
        }
    prior_year = datetime.now().year - 1
    prior_div = None
    if yearly_dividends:
        prior_div = yearly_dividends.get(prior_year)
    if prior_div is None:
        prior_div = db.get_dividend_history(ticker).get(prior_year)
    if prior_div is None or prior_div <= 0:
        return {
            'earned': False, 'summary': None,
            'note': 'No prior-year dividend total available',
            'values': {'dividend_now': current_div},
        }
    delta_pct = ((current_div - prior_div) / prior_div) * 100
    earned = current_div > prior_div
    summary = f"TTM {_fmt_money(current_div)} / {prior_year} {_fmt_money(prior_div)} (Δ {_fmt_pct(delta_pct)})"
    return {
        'earned': earned, 'summary': summary, 'note': None,
        'values': {
            'dividend_now': current_div,
            'dividend_prior_year': prior_div,
            'prior_year': prior_year,
            'delta_pct': delta_pct,
        },
    }


def _explain_debt_to_capital_low(ticker):
    bs = db.get_balance_sheet(ticker)
    if not bs:
        return {
            'earned': False, 'summary': None,
            'note': 'No balance sheet data available',
            'values': {},
        }
    ltd = bs.get('long_term_debt') or 0
    std = bs.get('short_term_debt') or 0
    equity = bs.get('stockholders_equity')
    ratio = bs.get('debt_to_capital')
    if ratio is None or equity is None:
        return {
            'earned': False, 'summary': None,
            'note': 'Balance sheet data incomplete',
            'values': bs,
        }
    earned = ratio <= DEBT_TO_CAPITAL_THRESHOLD
    as_of = bs.get('as_of_date')
    as_of_str = f" — as of {as_of}" if as_of else ''
    summary = (f"(LTD {_fmt_money(ltd)} + STD {_fmt_money(std)}) / "
               f"(debt + equity {_fmt_money(ltd + std + equity)}) = {ratio * 100:.1f}%{as_of_str}")
    return {
        'earned': earned, 'summary': summary, 'note': None,
        'values': {
            'long_term_debt': ltd, 'short_term_debt': std,
            'stockholders_equity': equity, 'debt_to_capital': ratio,
            'threshold': DEBT_TO_CAPITAL_THRESHOLD, 'as_of_date': as_of,
        },
    }


def _explain_undervalued(valuation):
    cp = valuation.get('current_price')
    fv = valuation.get('estimated_value')
    if cp is None or fv is None or fv <= 0:
        return {
            'earned': False, 'summary': None,
            'note': 'Missing current price or fair value',
            'values': {'current_price': cp, 'fair_value': fv},
        }
    delta_pct = ((cp - fv) / fv) * 100
    earned = cp < fv
    summary = f"Price {_fmt_money(cp)} vs FV {_fmt_money(fv)} (Δ {_fmt_pct(delta_pct)})"
    return {
        'earned': earned, 'summary': summary, 'note': None,
        'values': {'current_price': cp, 'fair_value': fv, 'delta_pct': delta_pct},
    }


def _explain_shares_buyback(ticker, is_holding, first_buy_date):
    if not is_holding:
        return {
            'earned': False, 'summary': None,
            'note': 'Only applies to holdings',
            'values': {},
        }
    if not first_buy_date:
        return {
            'earned': False, 'summary': None,
            'note': 'No buy transactions found',
            'values': {},
        }
    history = db.get_shares_outstanding_history(ticker)
    if not history:
        return {
            'earned': False, 'summary': None,
            'note': 'No shares-outstanding history available',
            'values': {'first_buy_date': first_buy_date},
        }
    at_buy = None
    for row in history:
        if row['as_of_date'] <= first_buy_date:
            at_buy = row
        else:
            break
    if at_buy is None:
        return {
            'earned': False, 'summary': None,
            'note': 'No shares snapshot at/before first buy date',
            'values': {'first_buy_date': first_buy_date, 'history_starts': history[0]['as_of_date']},
        }
    current = history[-1]
    s_then = at_buy.get('shares')
    s_now = current.get('shares')
    if not s_then or not s_now:
        return {
            'earned': False, 'summary': None,
            'note': 'Shares data incomplete',
            'values': {},
        }
    delta_pct = ((s_now - s_then) / s_then) * 100
    earned = s_now < s_then
    summary = (f"First buy {first_buy_date}: {_fmt_shares(s_then)} shares (as of {at_buy['as_of_date']}). "
               f"Now {_fmt_shares(s_now)} ({_fmt_pct(delta_pct)})")
    return {
        'earned': earned, 'summary': summary, 'note': None,
        'values': {
            'first_buy_date': first_buy_date,
            'shares_at_buy': s_then, 'shares_at_buy_date': at_buy['as_of_date'],
            'shares_now': s_now, 'shares_now_date': current['as_of_date'],
            'delta_pct': delta_pct,
        },
    }


def explain_stars(ticker: str, valuation_override: Optional[Dict] = None) -> Dict:
    """
    Return a per-criterion breakdown for the Company Profile page table.

    When `valuation_override` is provided (e.g., from the fresh
    /api/valuation/<ticker> response), its non-null fields are merged over
    the cached `valuations` row. This guarantees the Company Profile's
    formula card and explain table show the same numbers.

    Shape:
      {
        'ticker': 'PGR',
        'is_holding': False,
        'max_stars': 5,
        'total_stars': 4,
        'criteria': [{num, key, name, holdings_only, earned, summary, note, values}, ...]
      }
    """
    ticker = ticker.upper()
    cached = db.get_valuation(ticker) or {}
    if valuation_override:
        # Only use the override's non-null values; fall back to cache otherwise.
        valuation = dict(cached)
        for k, v in valuation_override.items():
            if v is not None:
                valuation[k] = v
    else:
        valuation = cached
    is_holding = ticker in _holdings_ticker_set()
    first_buy = db.get_first_buy_date(ticker) if is_holding else None
    yearly_divs = fetch_yearly_dividends(ticker)

    criteria_rows = [
        {'num': 1, 'key': 'earnings_beat',       'name': 'Earnings Beat',
         'holdings_only': False, **_explain_earnings_beat(ticker)},
        {'num': 2, 'key': 'fair_value_up',       'name': 'Fair Value Up YoY',
         'holdings_only': False, **_explain_fair_value_up(ticker, valuation, yearly_divs)},
        {'num': 3, 'key': 'dividend_up',         'name': 'Dividend Up YoY',
         'holdings_only': False, **_explain_dividend_up(ticker, valuation, yearly_divs)},
        {'num': 4, 'key': 'debt_to_capital_low', 'name': 'Debt-to-Capital ≤ 25%',
         'holdings_only': False, **_explain_debt_to_capital_low(ticker)},
        {'num': 5, 'key': 'undervalued',         'name': 'Undervalued',
         'holdings_only': False, **_explain_undervalued(valuation)},
        {'num': 6, 'key': 'shares_buyback',      'name': 'Share Buybacks Since Buy',
         'holdings_only': True,  **_explain_shares_buyback(ticker, is_holding, first_buy)},
    ]

    # Total only sums criteria that apply (holdings get all 6; non-holdings get 5)
    total = sum(1 for c in criteria_rows if c['earned'] and (is_holding or not c['holdings_only']))
    max_stars = 6 if is_holding else 5

    return {
        'ticker': ticker,
        'is_holding': is_holding,
        'max_stars': max_stars,
        'total_stars': total,
        'criteria': criteria_rows,
    }

"""
One-shot backfill: recompute valuations.estimated_value for every ticker
where the authoritative SEC eps_history table holds data the cache doesn't
reflect.

Fixes the drift documented in
requirements/2026-05-22-2218-stars-fair-value-mismatch — the screener used
to fall back to stale yfinance values (or old cached rows) when a SEC fetch
failed, even though correct SEC data was already in eps_history.

Usage:
    ./venv/bin/python scripts/backfill_valuations.py

Idempotent: re-running on already-clean data prints "unchanged" for every
ticker and writes nothing.
"""
import os
import sys
import time

# Make the project root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from services.providers import init_providers, get_orchestrator
from services.valuation import (
    get_split_adjusted_eps_history, compute_estimated_value,
    average_split_adjusted_eps,
)


def main():
    init_providers()
    orch = get_orchestrator()
    all_v = db.get_all_valuations()
    total = len(all_v)
    print(f"Scanning {total} tickers...")

    examined = updated = unchanged = no_eps_history = 0
    start = time.time()

    for ticker, row in sorted(all_v.items()):
        examined += 1

        # Split-adjusted history so post-split tickers (e.g. BKNG 25:1) average correctly.
        hist = get_split_adjusted_eps_history(ticker)
        if not hist:
            no_eps_history += 1
            continue

        raw_avg, years_used = average_split_adjusted_eps(hist)
        if raw_avg is None:
            no_eps_history += 1
            continue
        eps_avg = round(raw_avg, 2)

        # Always refresh dividends — that's half the bug.
        div_result = orch.fetch_dividends(ticker)
        if div_result.success and div_result.data:
            fetched_div = div_result.data.annual_dividend
            cached_div_val = row.get('annual_dividend') or 0
            # Same flakiness guard as calculate_valuation: distrust a fresh 0
            # when the cache has a non-zero dividend (yfinance intermittently
            # returns empty payment lists) rather than zeroing fair value.
            if (not fetched_div or fetched_div <= 0) and cached_div_val > 0:
                annual_div = round(cached_div_val, 2)
            else:
                annual_div = round(fetched_div, 2)
        else:
            annual_div = round(row.get('annual_dividend') or 0, 2)

        # compute_estimated_value applies sanity rules (eps_avg <= 0 -> None,
        # value-to-price ratio bounds) consistently with the rest of the codebase.
        estimated_value, pvv = compute_estimated_value(
            eps_avg, annual_div, row.get('current_price')
        )

        cached_eps_avg = row.get('eps_avg')
        cached_div = row.get('annual_dividend')
        cached_ev = row.get('estimated_value')
        if (eps_avg == cached_eps_avg
                and annual_div == cached_div
                and estimated_value == cached_ev):
            unchanged += 1
            if examined % 100 == 0:
                elapsed = time.time() - start
                print(f"  scanned {examined}/{total}  updated={updated}  "
                      f"unchanged={unchanged}  no_hist={no_eps_history}  "
                      f"elapsed={elapsed:.0f}s")
            continue

        db.bulk_update_valuations({ticker: {
            **row,
            'eps_avg': eps_avg,
            'eps_years': years_used,
            'eps_source': 'sec_cache',
            'annual_dividend': annual_div,
            'estimated_value': estimated_value,
            'price_vs_value': pvv,
        }})
        updated += 1

        if examined % 25 == 0:
            elapsed = time.time() - start
            print(f"  scanned {examined}/{total}  updated={updated}  "
                  f"unchanged={unchanged}  no_hist={no_eps_history}  "
                  f"elapsed={elapsed:.0f}s")

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.0f}s")
    print(f"  examined        : {examined}")
    print(f"  updated         : {updated}")
    print(f"  unchanged       : {unchanged}")
    print(f"  no eps_history  : {no_eps_history}")


if __name__ == '__main__':
    main()

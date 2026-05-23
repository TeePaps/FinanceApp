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
from config import PE_RATIO_MULTIPLIER
from services.providers import init_providers, get_orchestrator


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

        hist = db.get_eps_history(ticker)
        if not hist:
            no_eps_history += 1
            continue

        use = hist[:8]
        eps_avg = round(sum(h['eps'] for h in use) / len(use), 2)

        # Always refresh dividends — that's half the bug.
        div_result = orch.fetch_dividends(ticker)
        if div_result.success and div_result.data:
            annual_div = round(div_result.data.annual_dividend, 2)
        else:
            annual_div = round(row.get('annual_dividend') or 0, 2)

        estimated_value = round((eps_avg + annual_div) * PE_RATIO_MULTIPLIER, 2)
        cp = row.get('current_price')
        if cp and estimated_value:
            pvv = round(((cp - estimated_value) / estimated_value) * 100, 1)
        else:
            pvv = None

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
            'eps_years': len(use),
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

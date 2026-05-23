"""
One-shot cleanup: re-apply the canonical compute_estimated_value() sanity
rules to every row in `valuations`. Tickers that fail the rules
(eps_avg <= 0, value out of [0.1x, 10x] price band) get
estimated_value + price_vs_value cleared to NULL.

Fixes the 37 currently-bad rows produced by the original inline formula
that didn't have sanity checks. After this runs, the Recommendations Top 10
no longer surfaces tickers like PANW ($-0.50 fair value) or BRK-B ($0.10).

Idempotent — re-running prints "unchanged" for every row.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from services.valuation import compute_estimated_value


def main():
    all_v = db.get_all_valuations()
    total = len(all_v)
    print(f"Scanning {total} tickers...")

    examined = cleaned = unchanged = 0
    cleared_examples = []

    for ticker, row in sorted(all_v.items()):
        examined += 1
        ev, pvv = compute_estimated_value(
            row.get('eps_avg'),
            row.get('annual_dividend'),
            row.get('current_price'),
        )
        cached_ev = row.get('estimated_value')
        cached_pvv = row.get('price_vs_value')
        if ev == cached_ev and pvv == cached_pvv:
            unchanged += 1
            continue

        db.bulk_update_valuations({ticker: {
            **row,
            'estimated_value': ev,
            'price_vs_value': pvv,
        }})
        cleaned += 1
        if ev is None and cached_ev is not None and len(cleared_examples) < 10:
            cleared_examples.append((ticker, cached_ev, row.get('eps_avg'), row.get('current_price')))

    print(f"\nDone.  examined={examined}  cleaned={cleaned}  unchanged={unchanged}")
    if cleared_examples:
        print("\nExample tickers whose fair value was nulled out:")
        for t, old_ev, eps, price in cleared_examples:
            print(f"  {t:6s} was ev=${old_ev}  (eps_avg={eps}, price=${price})")


if __name__ == '__main__':
    main()

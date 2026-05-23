# Requirements Spec: Suppress Nonsense Fair Values

**Date:** 2026-05-23
**Requirement ID:** 2026-05-23-1453-recommendations-negative-fair-value
**Status:** Ready for implementation

---

## 1. Problem Statement

The fair-value formula `(avg_EPS + dividend) × 10` produces nonsense when
`avg_EPS ≤ 0` or when EPS is tiny relative to share price. 33 tickers have
negative cached fair values (e.g., PANW -$0.50, BA -$48.90) and 4 have
near-zero positive values (BRK-B $0.10 vs $487 price). These dominate the
Recommendations Top 10 with absurd "undervalued by -52,216%" claims because
the engine sorts by raw `price_vs_value`.

## 2. Solution Overview

Three coordinated changes — all backend:

1. **Single source of truth — `_compute_estimated_value()` helper** in
   `services/valuation.py` applied wherever fair value is calculated.
   Returns `None` when EPS is non-positive OR the ratio sanity check fails.
2. **Apply it everywhere fair value is built**: `calculate_valuation()`,
   `services/screener.py:run_screener()` Phase 4, `run_quick_price_update()`,
   `run_smart_update()`, `run_global_refresh()`.
3. **Recommendation engine** filters tickers without a valid `estimated_value`.
4. **One-shot cleanup script** to fix the 37 currently-bad rows in the DB.

No schema changes. No UI changes (existing N/A rendering handles `None`).

## 3. Functional Requirements

### 3.1 Sanity rules for `estimated_value`
Compute `estimated_value = (eps_avg + annual_dividend) × PE_RATIO_MULTIPLIER`,
then set BOTH `estimated_value` and `price_vs_value` to `None` if ANY:

- `eps_avg is None or eps_avg <= 0`
- `estimated_value <= 0` (defense-in-depth — a dividend could pull this negative too in pathological cases)
- `current_price > 0` AND `(estimated_value < 0.1 * current_price OR estimated_value > 10 * current_price)`

The third rule catches:
- BRK-B-style cases where SEC's per-share EPS structure doesn't fit (BRK-B avg EPS $0.01 → fv $0.10 vs price $487)
- Generally: any case where the formula produces values that are clearly off by an order of magnitude

### 3.2 Recommendation engine filter
`services/recommendations.py:score_stock()` (and/or `get_top_recommendations`)
should skip — not score — any ticker where `estimated_value is None` or
`price_vs_value is None`. Returning a score of `None` (or omitting the row
entirely) keeps them out of the Top 10 sort.

### 3.3 One-shot cleanup of existing cached rows
A script `scripts/clean_invalid_valuations.py` walks every row in `valuations`
and, if the sanity rules say `None`, updates the row to NULL the
`estimated_value` and `price_vs_value` columns. Reports examined / cleared /
unchanged. Idempotent — re-running on clean data writes nothing.

## 4. Technical Requirements

### 4.1 `services/valuation.py` — new helper
```python
from config import PE_RATIO_MULTIPLIER, ESTIMATED_VALUE_RATIO_LOW, ESTIMATED_VALUE_RATIO_HIGH

def compute_estimated_value(eps_avg, annual_dividend, current_price=None):
    """
    Returns (estimated_value, price_vs_value) following the canonical
    formula plus sanity checks. Either or both may be None.

    Rules:
      - eps_avg None or <= 0           -> (None, None)
      - estimated_value <= 0           -> (None, None)
      - current_price > 0 AND
        (ev < 0.1 * price OR ev > 10 * price)  -> (None, None)
    """
    if eps_avg is None or eps_avg <= 0:
        return None, None
    annual_dividend = annual_dividend or 0
    ev = (eps_avg + annual_dividend) * PE_RATIO_MULTIPLIER
    if ev <= 0:
        return None, None
    if current_price and current_price > 0:
        low_bound = ESTIMATED_VALUE_RATIO_LOW * current_price   # 0.1
        high_bound = ESTIMATED_VALUE_RATIO_HIGH * current_price  # 10
        if ev < low_bound or ev > high_bound:
            return None, None
        pvv = ((current_price - ev) / ev) * 100
        return round(ev, 2), round(pvv, 1)
    return round(ev, 2), None
```

Update `calculate_valuation()` to use the helper instead of inline math.

### 4.2 `config.py` — new constants
```python
# Sanity bounds: estimated_value must be between (LOW * price) and (HIGH * price)
# else it's treated as untrusted (set to None). Catches cases where SEC's
# per-share EPS structure doesn't fit the formula (e.g., BRK-B class B).
ESTIMATED_VALUE_RATIO_LOW  = _get('valuation.sanity_ratio_low',  0.1)
ESTIMATED_VALUE_RATIO_HIGH = _get('valuation.sanity_ratio_high', 10.0)
```

### 4.3 `services/screener.py` — apply helper
Replace inline `estimated_value = (eps_avg + annual_dividend) * 10` and the
`price_vs_value = ...` math in all four screener variants with a call to
`compute_estimated_value()`.

### 4.4 `services/recommendations.py` — filter
In `score_stock()` (and `get_top_recommendations()`), return `None` (or
filter out) any ticker where the valuation has `estimated_value is None`
or `price_vs_value is None`.

### 4.5 `scripts/clean_invalid_valuations.py` (NEW)
```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db
from services.valuation import compute_estimated_value

def main():
    all_v = db.get_all_valuations()
    examined = cleaned = unchanged = 0
    for ticker, row in all_v.items():
        examined += 1
        ev, pvv = compute_estimated_value(
            row.get('eps_avg'),
            row.get('annual_dividend'),
            row.get('current_price'),
        )
        if ev == row.get('estimated_value') and pvv == row.get('price_vs_value'):
            unchanged += 1
            continue
        db.bulk_update_valuations({ticker: {**row, 'estimated_value': ev, 'price_vs_value': pvv}})
        cleaned += 1
    print(f'examined={examined} cleaned={cleaned} unchanged={unchanged}')

if __name__ == '__main__':
    main()
```

## 5. Acceptance Criteria

1. **PANW / SHOP**: After cleanup, `db.get_valuation('PANW')['estimated_value']` is `None`. PANW does NOT appear in `/api/recommendations`.
2. **BRK-B**: After cleanup, `db.get_valuation('BRK-B')['estimated_value']` is `None` (caught by the 0.1× sanity bound). Does not appear in recommendations.
3. **Top 10**: The Recommendations Top 10 no longer shows any ticker with a negative or absurd `price_vs_value`. Every row shows a sane "Significantly/Moderately/Slightly undervalued by X%" reason where X is between roughly -90% and +900%.
4. **Audit**: `select count(*) from valuations where estimated_value < 0` returns 0 after cleanup. Same for `estimated_value < 0.1 * current_price` and `> 10 * current_price` (where current_price is not null).
5. **Future-proof**: Running the screener does NOT reintroduce nonsense — Phase 4 uses the new helper. Same for `calculate_valuation()` calls from the Company Lookup endpoint.

## 6. Assumptions

- A: The 0.1× / 10× bounds are reasonable starting values. Configurable via
  `config.yaml` if we need to tune.
- B: Tickers excluded from Recommendations can still appear elsewhere (Stars tab,
  Holdings, screener results). Only the "is this stock undervalued?" pages
  filter them.
- C: The sanity check is a heuristic — a truly extreme undervaluation (price
  10× below fair value) would also be filtered. This is acceptable; if it
  happens we can revisit.
- D: We don't try to "fix" the underlying data for these tickers (e.g.,
  averaging only positive years). That's a larger redesign best deferred.

# Requirements Spec: Split-Adjust Historical EPS Before Averaging

**Date:** 2026-05-23
**Requirement ID:** 2026-05-23-1516-split-adjust-eps-for-fair-value
**Status:** Ready for implementation

---

## 1. Problem Statement

SEC reports EPS on a pre-split basis. When a stock splits, current price drops
by the split ratio but historical EPS stays at its pre-split nominal value.
The formula `(eps_avg + dividend) × 10` then produces a fair value that's
off by the cumulative split ratio.

BKNG (recent 25:1 split on 2026-04-06):
- pre-split eps_avg = $94.59 → fair value $961.60 → "undervalued by -83%" ❌
- post-split adjusted eps_avg = $3.78 → fair value $37.80 → overvalued by +326% ✓

This affects any ticker with a split inside the 8-year EPS averaging window.

## 2. Solution Overview

One central helper that returns split-adjusted EPS values:

```python
# services/valuation.py
def get_split_adjusted_eps_history(ticker) -> list[dict]:
    """
    Returns eps_history with each year's `eps` adjusted for any splits that
    occurred AFTER that year. So all values are on the SAME per-share basis
    as the current ticker price.
    """
```

Used everywhere fair value is computed: `calculate_valuation`, all 4 screener
variants, and the Stars criterion 2 backfill path.

No new tables. No new API endpoints. No UI changes (the Split Warning badge
keeps doing its job).

## 3. Functional Requirements

### 3.1 Adjustment math
For each EPS record with year Y and EPS X:
1. Find all splits in `split_history` whose `date` is later than the EPS
   record's `period_end` (or `filed` date if period_end is missing).
2. Compute cumulative_ratio = product of those splits' ratios.
3. adjusted_eps = X / cumulative_ratio.

This handles both forward (ratio > 1 → divides) and reverse (ratio < 1 →
effectively multiplies, since dividing by 0.5 = ×2).

Edge cases:
- Split history empty → return raw EPS unchanged.
- EPS record missing period_end AND filed → assume Jan 1 of (year + 1) (i.e.,
  ANY split in year `Y` is treated as AFTER that fiscal year — conservative).
- Split ratio of 0 or None → skip that split (defensive).

### 3.2 Where it's wired
1. **`calculate_valuation()`** (services/valuation.py) — replace the raw
   `eps_data` sum with the adjusted version.
2. **Screener Phase 1** (services/screener.py) — when building `eps_results`
   from `sec_result.data.eps_history`, apply the adjustment before averaging.
   The eps_history fallback path (`db.get_eps_history`) also uses adjusted
   history.
3. **`compute_estimated_value()`** — no change. Receives a single eps_avg
   number. The split-adjustment happens upstream when computing eps_avg.
4. **`services/stars.py:_prior_year_fair_value_from_eps()`** — use adjusted
   history. So the YoY fair-value comparison is apples-to-apples.

### 3.3 Backfill existing cached rows
After deploying, re-run the existing `scripts/backfill_valuations.py`
(or a small new script) so the 1487 cached rows get correct
split-adjusted fair values immediately, without waiting for the next
screener run.

## 4. Technical Requirements

### 4.1 `services/valuation.py` — new helper
```python
import database as db
from typing import List, Dict


def get_split_adjusted_eps_history(ticker: str) -> List[Dict]:
    """
    Return eps_history with EPS values adjusted for any splits AFTER each
    fiscal year. Adjusted values are on the SAME per-share basis as the
    current ticker price, so averages and fair-value math work correctly.
    """
    history = db.get_eps_history(ticker)
    if not history:
        return []
    splits = db.get_splits(ticker)
    if not splits:
        return list(history)

    def split_date_of(s):
        return s.get('date')

    adjusted = []
    for row in history:
        eps = row.get('eps')
        if eps is None:
            adjusted.append(dict(row))
            continue
        # Use period_end as the "fiscal year cutoff"; fall back to filed,
        # then to Jan 1 of year+1 if neither present.
        year = row.get('year')
        cutoff = row.get('period_end') or row.get('filed')
        if not cutoff and year:
            cutoff = f"{int(year) + 1}-01-01"

        cumulative = 1.0
        for s in splits:
            d = split_date_of(s)
            ratio = s.get('ratio')
            if not d or not ratio or ratio <= 0:
                continue
            if cutoff and d > cutoff:
                cumulative *= float(ratio)

        if cumulative != 1.0:
            new_row = dict(row)
            new_row['eps'] = eps / cumulative
            new_row['split_adjusted'] = True
            new_row['split_adjustment_factor'] = cumulative
            adjusted.append(new_row)
        else:
            adjusted.append(dict(row))
    return adjusted
```

### 4.2 `services/valuation.py` — use it in `calculate_valuation()`
Right now `get_validated_eps()` returns `eps_list[:8]` from the provider's
fresh fetch. For consistency, switch `calculate_valuation()` to derive
`eps_data` from `get_split_adjusted_eps_history(ticker)` whenever
eps_history rows exist (after the screener has run for the ticker at least
once). This gives split-adjustment for every fair value computation.

A simpler tactic that doesn't disturb `get_validated_eps()`:
- After `get_validated_eps()`, call `get_split_adjusted_eps_history(ticker)`.
  If it returns N >= len(eps_data), prefer it. Otherwise use the validated
  list (fresh-from-provider, no adjustment but at least has data).

### 4.3 `services/screener.py` — Phase 1
Inside the per-ticker loop, after the SEC fetch returns
`sec_result.data.eps_history`, apply the helper:
```python
from services.valuation import get_split_adjusted_eps_history

# Persist as-is to eps_history table (raw), but compute averages on adjusted:
adjusted = get_split_adjusted_eps_history(t)
if adjusted:
    eps_history = adjusted[:8]
else:
    eps_history = sec_result.data.eps_history[:8]
```
Same change in the eps_history fallback path that was added previously.

Important: `eps_history` table CONTINUES to store raw (pre-split) values —
matching what SEC actually reports, so the data stays auditable. Only the
in-memory averaging uses the adjusted view.

### 4.4 `services/stars.py` — criterion 2 backfill
In `_prior_year_fair_value_from_eps()`, replace
`db.get_eps_history(ticker)` with `get_split_adjusted_eps_history(ticker)`
(import from `services.valuation`).

### 4.5 Cleanup pass
Re-run `scripts/backfill_valuations.py` after the code change. It already
recomputes from `eps_history` (now via the adjusted helper), so it will
rewrite all 1487 cached rows with correct values.

## 5. Acceptance Criteria

1. **BKNG**: After backfill, `db.get_valuation('BKNG')['estimated_value']` is
   close to $37 (or `None` if it falls outside the 0.1× sanity bound vs
   $161 price — let's see). Either way, BKNG is NOT in the Top 10
   "undervalued" recommendations.
2. **PANW**: PANW had a 2:1 split on 2024-12-16. With adjustment its old
   pre-split EPS years should halve. (PANW also has avg EPS ≤ 0 issue, so
   it remains suppressed by the existing rule.)
3. **A non-splitting ticker** (e.g., GIS, USB): unchanged behavior — fair
   value identical to current value within a cent.
4. **YoY comparison**: For a ticker with a split inside the past year (BKNG),
   the Stars page criterion 2 "Fair Value Up YoY" comparison uses adjusted
   values so it's apples-to-apples (no spurious +1000% jump that's purely a
   split artifact).
5. **eps_history table unchanged**: raw SEC values still stored as filed —
   only the in-memory averaging is adjusted.

## 6. Assumptions

- A: SEC EPS records always have either `period_end` or `filed` populated.
  Tickers missing both fall back to "Jan 1 of next year" (conservative —
  attributes ANY year split to that fiscal year, so the math still works).
- B: The Split Warning UI badge remains valuable for users to know an
  adjustment was applied — keep it as-is.
- C: yfinance/FMP report split ratios consistently (forward > 1, reverse < 1).
  Already verified in the split provider chain.
- D: The 8-year-cap on EPS years (already applied) means we don't need to
  worry about ancient splits like BKNG's 2003 1-for-6 reverse — it's
  outside the window.

# Initial Request: Split-Adjust Historical EPS Before Averaging

**Date:** 2026-05-23
**Requested by:** tp@promenet.com
**Related work:** Recommendations bug — companion to
`2026-05-23-1453-recommendations-negative-fair-value`.

## Original Report

> I still don't get this one: BKNG
> Recent Split 2026-04-06 (25:1), Price $161.06, Est. Value $961.60
> vs Value -83%, Annual Div $1.57/yr
> Split but value doesn't look right and I can't tell

## Investigation Findings

BKNG had a **25-for-1 forward split on 2026-04-06**. SEC's EPS history is on a
PRE-split basis (one big share earned $172 in 2024), but the current price
($161.06) is on a POST-split basis (one small share). The fair value formula
averages pre-split EPS × 10 and produces a meaningless "$961.60" because it's
comparing per-old-share earnings to per-new-share price.

### Raw numbers
```
Current price:      $161.06   (post-split)
Annual dividend:    $1.57

eps_history (pre-split, from SEC):
  2025: $165.57    2024: $172.69    2023: $117.40    2022: $76.35
  2021: $28.17     2020: $1.44      2019: $111.82    2018: $83.26
  avg = $94.59

cached estimated_value = (94.59 + 1.57) × 10 = $961.60
cached price_vs_value  = -83%  (looks "undervalued")
```

### Split-adjusted view (what the formula should produce)
Each pre-split EPS year should be divided by 25 (the cumulative split ratio
for splits occurring AFTER that year's fiscal period):
```
2025: $6.62    2024: $6.91    2023: $4.70    2022: $3.05
2021: $1.13    2020: $0.06    2019: $4.47    2018: $3.33
avg ≈ $3.78
fair value = ($3.78 + $1.57) × 10 = $53.50
vs price $161 → +201% OVERVALUED (not -83% undervalued)
```

This flips BKNG from being a "top buy" to a "consider selling" recommendation.

### Data we already have
- `eps_history` table with annual EPS records (pre-split as SEC reports them)
- `split_history` table populated by the screener via the Split provider chain
  (yfinance, FMP, Alpaca, SEC)
- `compute_split_warning(ticker)` flags splits within the EPS-averaging window
  (informational only — does NOT affect fair value today)

### Scope
The Split Warning UI badge that already exists handles the *user-visible*
warning. But the underlying fair-value math is still wrong. Any ticker with a
split inside the EPS averaging window (≤ 8 years) will have its fair value off
by the cumulative split ratio. The user wants the actual NUMBERS fixed, not
just a warning.

# Initial Request: Company Profile Star Table Shows Inconsistent Data

**Date:** 2026-05-23
**Requested by:** tp@promenet.com
**Related work:** Bug in the recently-shipped
`2026-05-22-2246-company-profile-stars-table` feature.

## Original Report

> Seems like stars on star tab differ from stars on company profile.
> Example: USB
>
> The whole table on company profile seems off really, like they are almost
> not getting data from same API:
>
> Star Rating Breakdown:
> 1. Earnings Beat — EPS actual $1.18 vs estimate $1.14 → +3.4% (Q ending 2026-03-31)
> 2. Fair Value Up YoY — No fair value computed (missing EPS)
> 3. Dividend Up YoY — No dividend paid
> 4. Debt-to-Capital ≤ 25% — 46.6% (correctly shown)
> 5. Undervalued — Missing current price or fair value
> 6. Share Buybacks Since Buy (holdings only)

## Investigation Findings

USB's data in three places right now:

| Source                                | Endpoint / Table                        | est_value | eps_avg | dividend | price |
|---------------------------------------|-----------------------------------------|-----------|---------|----------|-------|
| Company Profile fair-value formula    | `GET /api/valuation/USB` (fresh)        | **$39.79** | 3.98    | **$0.00** | 54.83 |
| Star Rating Breakdown table           | `GET /api/stars/USB/explanation` (cached)| $60.39  | 3.98    | $2.06    | 54.83 |
| Stars tab                             | `GET /api/stars` (cached)               | $60.39   | 3.98    | $2.06    | 54.83 |
| Underlying `valuations` row (cache)   | `db.get_valuation('USB')`               | $60.39   | 3.98    | $2.06    | 54.83 |

Two independent bugs compound:

### Bug A — Empty-cache state shows "missing" everywhere
When a new ticker is added or its `valuations` row hasn't been populated yet,
`explain_stars()` reads from `db.get_valuation()` and sees nulls for
`current_price`, `eps_avg`, `estimated_value`, `annual_dividend`. So criteria
2, 3, 5 all report "missing". That's what the user saw for USB when they
first loaded the page.

### Bug B — Fresh-calc dividend can be 0 even when cached is correct
`calculate_valuation()` (used by `/api/valuation/<ticker>`) calls
`fetch_dividends(ticker)` fresh every time. yfinance is flaky — sometimes
returns 0 dividends for tickers that actually pay. When that happens, the
fresh fair-value formula on the same page differs from the cached value used
in the explain table. Result: **two different fair values on one page**.

### Why both surface as "Stars tab differs from Company Profile"
Stars tab: reads cache → shows correct ($60.39, 4 stars earned).
Company Profile: shows both a fresh-calculated formula card AND a cached
explain table → they disagree with each other when fresh ≠ cached.

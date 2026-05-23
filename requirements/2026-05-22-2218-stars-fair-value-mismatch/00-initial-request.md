# Initial Request: Stars Page Shows Stale/Wrong Fair Value

**Date:** 2026-05-22
**Requested by:** tp@promenet.com
**Related work:** Discovered during Stars tab testing (see `2026-05-22-2048-star-scoring-system`).

## Original Report

> Something is wrong with the star page.
> Take a look at PRG [meant PGR] for example, It says value is around $152,
> but clicking into the company profile, it shows about $223. I assume this
> is issue across most.

## Investigation Findings

PGR cached on Stars/screener: $152.50.
PGR fresh from Company Lookup: $223.81.

The discrepancy decomposes into:

|                  | Stars (cached)  | Company Lookup (fresh) |
|------------------|-----------------|------------------------|
| EPS source       | **yfinance**    | sec                    |
| EPS years used   | **4**           | 8                      |
| EPS average      | $10.35          | $8.48                  |
| Annual dividend  | **$4.90**       | **$13.90**             |
| Estimated value  | **$152.50**     | **$223.81**            |

### Root cause
1. The screener (`services/screener.py:189-216`) tries SEC EDGAR first for EPS.
   On SEC failure it falls back to *the previous valuation row's cached data*
   (line 207-208: `existing = existing_valuations.get(t, {})`). That fallback
   ignores the `eps_history` table — which may already contain better SEC data
   from a prior successful fetch.
2. The screener doesn't refresh dividends for tickers where any annual_dividend
   value already exists in the cache (Phase 2's `needs_dividend_update()` skips
   tickers with a non-null cached dividend that's not stale). PGR's $4.90 vs
   the actual $13.90 is from an old payment that was never refreshed.

### Scope
- **362 tickers** have `eps_source='yfinance'` (likely stale 4-year data)
- **916 tickers** have `eps_source='unknown'` (origin lost)
- **294 tickers** have a cached `eps_avg` that diverges by >$0.10 from the
  authoritative `eps_history` table — about half of the tickers that have
  EPS at all (559 total).
- Affects every page that reads from `valuations.estimated_value`: Stars,
  Recommendations, Holdings sell-recommendations, screener results — not just
  the Stars page.

### Examples
- CSCO: cached 2.75 (yfinance) vs hist-avg 2.34 (8 yrs SEC)
- DOW: cached 1.24 (yfinance) vs hist-avg 2.05 (8 yrs SEC)
- LYB: cached 5.02 (yfinance) vs hist-avg 8.72 (8 yrs SEC)
- PNC: cached 14.24 (yfinance) vs hist-avg 11.83 (8 yrs SEC)

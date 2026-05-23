# Initial Request: Recommendations Showing Negative Fair Values

**Date:** 2026-05-23
**Requested by:** tp@promenet.com
**Related work:** Independent from recent star scoring work — long-standing
formula edge case exposed by sorting recommendations by undervaluation.

## Original Report

> I think all the valuations and recommendations got screwed up somewhere.
> #1 PANW: Est. Value $-0.50, vs Value -52216%
> #2 SHOP: Est. Value $-0.30, vs Value -34433%

## Investigation Findings

The fair value formula `(avg_EPS_over_8_years + annual_dividend) × 10`
produces nonsense when `avg_EPS ≤ 0`. PANW had four loss years (2019-2022)
before becoming profitable; averaged across 7 years its EPS is **-$0.05**,
giving an "estimated value" of **-$0.50**. Price/value math then produces
−52,216%, which the recommendation engine ranks as the most attractive stock.

### Scope across the DB

- **33 tickers** have `estimated_value < 0` (e.g., BA, TTWO, ALNY, PCG, DASH,
  NCLH — all had losing years averaged in)
- **4 tickers** have `0 < estimated_value < 1` (e.g., **BRK-B at $0.10**,
  pvv = +486,280%)
- Every one of these dominates the Recommendations sort and produces
  nonsensical "Why recommended" text like "Significantly undervalued at
  -52216% below estimated value"

### Why this isn't a regression

The valuations rows were last updated on 2026-05-22 (before today's lazy-write
fix). The same formula has always produced these values; sorting by
`price_vs_value` just makes them dominate.

### What's actually wrong

The formula assumes:
- A positive long-run earnings average exists for a meaningful "fair value"
- Multiplier × negative number is meaningless
- Multiplier × very small positive number creates extreme false-undervaluation
  signals for companies whose reported EPS is tiny (e.g., BRK-B's class B
  shares structure)

### Possible fixes

1. **Don't compute a fair value** when `eps_avg ≤ some threshold` (e.g.,
   ≤ 0 or ≤ 10% of recent price). Set `estimated_value = None`. The UI
   already handles None (shows "N/A").
2. **Recommendation engine** filters out tickers without a valid positive
   fair value — they simply don't appear in the Top 10.
3. **Sanity cap** on `price_vs_value` to prevent extreme outliers from
   dominating (e.g., clamp to ±100%).

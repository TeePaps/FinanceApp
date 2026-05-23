# Phase 2: Discovery Questions

The bug is the formula producing nonsense when avg EPS is non-positive. Three
fix dimensions to decide.

---

## Q1: When `eps_avg ≤ 0` (company had average losses over the window), should we set `estimated_value = None` (UI shows "N/A") rather than computing a negative number?
**Default if unknown:** Yes. A negative fair value is mathematically valid but semantically meaningless. None signals "can't reliably value this company with this formula" — which is honest.

## Q2: Should the Recommendation engine FILTER OUT tickers without a valid positive fair value (so they simply don't appear in the Top 10), rather than ranking them?
**Default if unknown:** Yes. The current behavior ranks "no fair value" tickers as #1 most undervalued, which is the opposite of useful. Excluding them surfaces real undervaluation candidates.

## Q3: Should we also handle the "very small positive eps_avg" pathology (BRK-B with eps_avg=$0.01 → est_value=$0.10 → +486,280% overvalued) — e.g., require `estimated_value` to be at least some fraction of current price before treating it as meaningful?
**Default if unknown:** Yes — apply a sanity check. If `estimated_value < 10% of current_price` OR > 10x current price, treat as untrusted and don't surface in undervaluation/overvaluation rankings. This protects against tickers whose EPS structure doesn't fit the formula (BRK-B class B, ADRs, etc.).

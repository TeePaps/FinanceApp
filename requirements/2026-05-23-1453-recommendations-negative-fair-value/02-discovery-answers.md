# Phase 2: Discovery Answers

## Q1: Negative eps_avg → None?
**Answer:** Yes — set estimated_value to None / N/A.

**Implication:** In `calculate_valuation()` and the screener's Phase 4, when
`eps_avg <= 0`, store `estimated_value = None` and `price_vs_value = None`.
The UI already handles None ("N/A" or "—").

---

## Q2: Filter recommendations?
**Answer:** Yes — filter them out.

**Implication:** `services/recommendations.py:get_top_recommendations()` must
skip tickers where `estimated_value is None` or `price_vs_value is None`.
These tickers can still appear in the Stars tab and elsewhere, just not as
"top investment recommendations".

---

## Q3: Sanity ratio check?
**Answer:** Yes — require sane ratio (0.1× to 10× current price).

**Implication:** After computing `estimated_value`, if `estimated_value < 0.1 *
current_price` OR `estimated_value > 10 * current_price`, treat as untrusted:
set `estimated_value = None` and `price_vs_value = None`. This catches the
BRK-B-style cases where SEC EPS values don't fit the per-share formula and
the DDOG-style cases where eps_avg is technically positive but tiny.

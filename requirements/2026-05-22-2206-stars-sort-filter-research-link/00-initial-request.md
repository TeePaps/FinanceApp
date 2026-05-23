# Initial Request: Stars Page — Sort, Filter, and Click-to-Research

**Date:** 2026-05-22
**Requested by:** tp@promenet.com
**Related work:** Extends `2026-05-22-2048-star-scoring-system` and `2026-05-22-2127-star-undervalued-and-tooltips`.

## Original Request

> On star page, we should be able to sort and filter on highest star companies
> and highest percentage fair value. Also, clicking a company tile should get
> you to the Company lookup page, similar to how it works on other pages

## Three Changes

### Change A — Sortable list
Allow user to sort the Stars tab tickers by:
- Highest star count (default — current behavior already sorts by total_stars desc)
- Highest % undervaluation (most undervalued first — i.e., most negative `price_vs_value`)

### Change B — Filterable list
Allow filtering to e.g.:
- Tickers with ≥ N stars (where N is user-selected)
- Tickers that are currently undervalued (price < fair value)

### Change C — Click-to-research
Clicking a company tile (the ticker card in the Stars list) should navigate
to the existing Company Lookup tab and auto-load that ticker — matching the
pattern used elsewhere in the app.

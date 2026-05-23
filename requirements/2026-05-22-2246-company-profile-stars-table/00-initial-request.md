# Initial Request: Stars Table on Company Profile Page

**Date:** 2026-05-22
**Requested by:** tp@promenet.com
**Related work:** Extends the Star Scoring System (`2026-05-22-2048-star-scoring-system`).

## Original Request

> On company profile page, I want to show the star values as well and how and
> what the calculations were for each in sort of a 6 row table

## Goal

On the Company Lookup page (research tab, `/api/valuation/<ticker>`), add a
6-row table that shows:

1. Each of the 6 star criteria
2. Whether the star is earned (★ vs ☆)
3. The underlying numbers used to determine it (e.g., "EPS actual $1.50 vs estimate $1.40 — beat by 7.1%")

This gives the user transparency into why a ticker has the rating it does on
the Stars tab.

## What Data Is Needed Per Criterion

| # | Criterion | Underlying numbers to show |
|---|---|---|
| 1 | Earnings Beat | actual EPS, prior consensus estimate, surprise %, quarter end date |
| 2 | Fair Value Up | current fair value, fair value 1 year ago |
| 3 | Dividend Up | trailing-12mo dividend, dividend 1 year ago |
| 4 | Debt-to-Capital ≤ 25% | LTD, STD, stockholders equity, computed ratio, as-of date |
| 5 | Undervalued | current price, fair value, % vs fair value |
| 6 | Shares Buyback (holdings only) | first buy date, shares at then, shares now, change |

Most of this is already cached in the DB after the screener's Phase 5;
serving it requires either a new endpoint or extending `/api/valuation/<ticker>`.

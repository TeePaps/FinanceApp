# Initial Request: Star Scoring System

**Date:** 2026-05-22
**Requested by:** tp@promenet.com

## Original Request

I want to add a new scoring system. Stars are awarded based on the following criteria:

1. **Earnings Beat (1 star)** — If projected earnings beat the current quarter's analyst expectations.

2. **Fair Value Growth (1 star)** — If the value from the calculated formula for 8 years of earnings went up from the past year.

3. **Dividend Growth (1 star)** — If the dividend went up in value.

4. **Debt to Capital (1 star)** — Anytime the debt-to-capital ratio is 25% and below.

5. **Share Buybacks (1 star)** — If from the time it was bought, the shares outstanding went down. (Only applies to companies the user holds.)

6. **Undervalued (1 star)** — If the formula marks them as undervalued. (Only applies to companies the user holds.)

## Maximum Score
Up to **6 stars** total for holdings; up to **4 stars** for non-holdings (criteria 5 and 6 only apply to holdings).

## Notes / Open Questions to Clarify
- Where should the rating display (recommendations, valuations, holdings, screener)?
- How should ties/edge cases be handled (missing data → no star vs. exclude)?
- Should the formula already exist for projected earnings vs. analyst expectations? (Currently, the codebase tracks EPS history but may not have analyst projections.)
- Does the "fair value formula went up from the past year" use the 8-year EPS average snapshot from a year ago, or recalculated?
- For share buybacks: is "from the time it was bought" the earliest buy lot date for the user?

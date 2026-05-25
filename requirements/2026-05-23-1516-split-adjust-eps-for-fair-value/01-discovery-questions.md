# Phase 2: Discovery Questions

The data exists; the question is the fix strategy.

---

## Q1: Should we split-adjust historical EPS values (divide each pre-split-date EPS by the cumulative split ratio of any splits AFTER that year), rather than just suppressing the fair value or showing a warning?
**Default if unknown:** Yes — adjust. We have full split_history data. Computing the correct fair value is strictly more useful than just hiding it. The Split Warning badge remains as a user-visible advisory.

## Q2: Should the adjustment also apply to forward stock splits' inverse case — reverse splits (e.g., 1-for-10 reverse, ratio < 1) — so a reverse split correctly multiplies the pre-split EPS instead of dividing?
**Default if unknown:** Yes — handle both. Math is symmetric: adjusted_eps = pre_split_eps / split_ratio where ratio>1 (forward) divides and ratio<1 (reverse, e.g., 0.5) multiplies by 2. Same formula in both directions.

## Q3: Should this adjustment also feed the Stars page's "Fair Value Up YoY" (criterion 2) which compares current fair value to 1-year-ago fair value, since the prior-year backfill also uses raw eps_history?
**Default if unknown:** Yes. The same split-adjusted EPS history should be used everywhere fair value is computed (calculate_valuation, screener Phase 4, stars criterion 2 backfill). Otherwise the year-over-year comparison would be apples-to-oranges across a split.

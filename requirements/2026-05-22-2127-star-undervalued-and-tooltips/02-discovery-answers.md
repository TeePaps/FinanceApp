# Phase 2: Discovery Answers

## Q1: Tooltip style?
**Answer:** Custom CSS tooltip — instant appearance, app-themed styling.

**Implication:** Add a CSS-based tooltip pattern (e.g., a `.star-tooltip` element absolutely-positioned next to each star, shown on `:hover` via CSS only). Keep the native `title` attribute as well for accessibility/screen readers.

---

## Q2: Recompute star_ratings after deploying the change?
**Answer:** Yes — recompute now.

**Implication:** After code changes are deployed, run `calculate_all_star_ratings()` against the existing ~12 rated tickers (or whichever subset exists) so the UI reflects the new criterion ordering and the Undervalued-for-all rule immediately. No full screener required.

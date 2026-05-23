# Phase 2: Discovery Questions

The reordering + scope change of the Undervalued criterion is fully specified
by the user. Only the tooltip implementation and migration approach are open.

---

## Q1: For the hover tooltip, do you want the native browser `title` tooltip (already wired up — appears after a short delay, browser-styled), or a custom CSS tooltip that appears instantly and matches the app's theme?
**Default if unknown:** Custom CSS tooltip. Native tooltips have a ~1.5s delay and look generic. A small CSS `:hover` tooltip is ~30 lines and is consistent with the rest of the app's UX.

## Q2: Should we recompute existing star_ratings now (so the watchlist gets Undervalued stars immediately), or wait for the next screener run to refresh them?
**Default if unknown:** Recompute now using `calculate_all_star_ratings()` — runs against existing valuations, takes a couple minutes for ~12 sample tickers, no full screener needed.

# Detail Answers

## Q1: Should the Quick Update (All Prices) automatically calculate EPS valuations from cached SEC data when eps_avg is missing?
**Answer:** Yes (confirmed)

## Q2: When fixing existing tickers with missing EPS data, should this happen automatically on the next Quick Update, or require a separate "Repair" action?
**Answer:** Automatically on next Quick Update (confirmed)

## Q3: Should tickers that have no SEC data show a different indicator than just "-"?
**Answer:** No - keep showing "-" for simplicity (confirmed)

## Q4: Should the Smart Update remain as a separate option, or should its functionality be merged into Quick Update?
**Answer:** Unknown - using default: Keep separate

## Q5: After the fix is implemented, should we run a one-time repair to fix the 144 existing tickers, or let the next Quick Update fix them naturally?
**Answer:** Let the next Quick Update fix them naturally (no one-time repair)

---

## Summary
The fix should modify the Quick Update to use cached SEC data from the `sec_companies` table when `eps_avg` is missing from existing valuations. This will transparently fix both:
1. Future tickers that get added without EPS data
2. Existing 144 tickers on the next Quick Update run

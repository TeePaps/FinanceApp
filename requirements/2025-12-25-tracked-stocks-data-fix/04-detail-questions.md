# Expert Detail Questions

## Q1: Should the Quick Update (All Prices) automatically calculate EPS valuations from cached SEC data when eps_avg is missing?
**Default if unknown:** Yes (uses already-cached data, no API calls needed, provides complete valuations)

## Q2: When fixing existing tickers with missing EPS data, should this happen automatically on the next Quick Update, or require a separate "Repair" action?
**Default if unknown:** Automatically on next Quick Update (transparent fix, no user action required)

## Q3: Should tickers that have no SEC data (marked as sec_no_eps=True or sec_status='unavailable') show a different indicator than just "-"?
**Default if unknown:** No (keep showing "-" for simplicity, the Source column already indicates if SEC data is available)

## Q4: Should the Smart Update remain as a separate option, or should its functionality be merged into Quick Update?
**Default if unknown:** Keep separate (Smart Update fetches from API, Quick Update should stay fast using only cached data)

## Q5: After the fix is implemented, should we run a one-time repair to fix the 144 existing tickers, or let the next Quick Update fix them naturally?
**Default if unknown:** Run one-time repair (fixes the immediate problem without waiting for user to trigger an update)

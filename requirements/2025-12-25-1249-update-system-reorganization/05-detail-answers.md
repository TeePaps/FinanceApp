# Detail Answers

## Q1: Should automatic background price refresh only run during US market hours (9:30 AM - 4:00 PM ET)?
**Answer:** Yes

## Q2: Should the simplified UI have just two main options: "Refresh Prices" (fast) and "Full Sync" (comprehensive)?
**Answer:** No - should have THREE options:
1. **Refresh Prices** - Fast, prices only
2. **Full Sync** - Comprehensive (EPS + Dividends + Prices)
3. **Fix Missing** - Fill gaps for tickers with incomplete data

## Q3: Should the staleness dashboard be a compact status bar showing "Prices: 5m ago | EPS: 98% complete | Dividends: 3d ago"?
**Answer:** Yes

## Q4: For per-ticker refresh, should clicking a ticker's row show a "Refresh" button in the detail panel?
**Answer:** Yes

## Q5: Should the background scheduler be configurable via config.yaml (interval, enabled/disabled, market hours)?
**Answer:** Yes

---

## Summary

The update system should be reorganized with:

### UI Simplification
- **3 clear update options** (down from 4 confusing ones):
  - Refresh Prices (fast)
  - Full Sync (comprehensive)
  - Fix Missing (gap-filler)

### Background Automation
- Automatic price refresh during market hours only (9:30 AM - 4:00 PM ET)
- Configurable via config.yaml (interval, enabled/disabled, market hours)
- Manual override always available

### Visibility Improvements
- Compact staleness dashboard: "Prices: 5m ago | EPS: 98% complete | Dividends: 3d ago"
- Per-ticker refresh button in detail panel

### Per-Ticker Control
- Refresh button in ticker detail panel (not right-click menu)

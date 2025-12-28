# Expert Detail Questions

## Q1: Should automatic background price refresh only run during US market hours (9:30 AM - 4:00 PM ET)?
**Default if unknown:** Yes (no point refreshing prices when market is closed; saves API calls)

## Q2: Should the simplified UI have just two main options: "Refresh Prices" (fast) and "Full Sync" (comprehensive)?
**Default if unknown:** Yes (merges Quick/All Prices into one, keeps Full Update, drops Smart as it's confusing)

## Q3: Should the staleness dashboard be a compact status bar showing "Prices: 5m ago | EPS: 98% complete | Dividends: 3d ago"?
**Default if unknown:** Yes (provides quick visibility without taking up screen space)

## Q4: For per-ticker refresh, should clicking a ticker's row show a "Refresh" button in the detail panel?
**Default if unknown:** Yes (more discoverable than right-click context menu; fits existing detail panel pattern)

## Q5: Should the background scheduler be configurable via config.yaml (interval, enabled/disabled, market hours)?
**Default if unknown:** Yes (allows tuning without code changes; follows existing config.yaml pattern)

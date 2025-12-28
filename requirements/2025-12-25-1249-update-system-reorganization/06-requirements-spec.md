# Requirements Specification: Update System Reorganization

## Problem Statement

The current update system has 4 overlapping options (Quick Update, All Prices, Smart Update, Full Update) with unclear differences and confusing scoping rules. Users can't tell what data is stale, can't refresh individual tickers, and must manually trigger all updates.

## Solution Overview

Reorganize the update system with:
1. **Simplified UI** - 3 clear update options with consistent scoping
2. **Background automation** - Automatic price refresh during market hours
3. **Staleness visibility** - Dashboard showing data freshness per data type
4. **Per-ticker control** - Refresh button in ticker detail panel

---

## Functional Requirements

### FR1: Simplified Update Options

Replace the current 4 options with 3 clear actions:

| Action | What It Does | Speed | Scope |
|--------|--------------|-------|-------|
| **Refresh Prices** | Prices only for all enabled indexes | 1-2 min | All indexes |
| **Full Sync** | EPS + Dividends + Prices (4-phase) | 10-20 min | All indexes |
| **Fix Missing** | Full fetch only for tickers with incomplete data | Varies | All indexes |

**Mapping from current system:**
- Quick Update + All Prices → **Refresh Prices** (merged, always all indexes)
- Full Update → **Full Sync** (renamed)
- Smart Update → **Fix Missing** (renamed, clarified purpose)

### FR2: Background Price Refresh

- Automatic price refresh at configurable interval (default: 15 minutes)
- Only runs during US market hours: 9:30 AM - 4:00 PM Eastern Time
- Respects existing `screener_running` flag (won't start if manual update in progress)
- Can be enabled/disabled via config
- Manual "Refresh Prices" always available regardless of auto-refresh state

### FR3: Staleness Dashboard

Compact status bar in the header area showing:
```
Prices: 5m ago | EPS: 98% complete (12 missing) | Dividends: 3d ago
```

Components:
- **Prices timestamp**: Time since last price update
- **EPS completeness**: Percentage of tickers with EPS data + count missing
- **Dividends timestamp**: Time since last dividend update

Visual indicators:
- Green: Fresh (prices < 1hr, dividends < 7d)
- Yellow: Aging (prices 1-24hr, dividends 7-30d)
- Red: Stale (prices > 24hr, dividends > 30d)

### FR4: Per-Ticker Refresh

- Clicking a ticker row shows detail panel (existing behavior)
- Detail panel includes "Refresh" button
- Refresh fetches: current price + EPS (if stale) + dividend (if stale)
- Shows inline loading state during refresh
- Updates the single ticker without affecting others

### FR5: Manual Override

- All three update buttons always visible and functional
- Can trigger manual refresh even if auto-refresh is enabled
- Clear indication when auto-refresh is active: "Auto-refresh: ON (next: 12m)"

---

## Technical Requirements

### TR1: New Files to Create

```
services/scheduler.py          # Background scheduler implementation
```

### TR2: Files to Modify

```
config.yaml                    # Add scheduler settings
config.py                      # Load scheduler settings
services/screener.py           # Add per-ticker refresh function
routes/screener.py             # Add per-ticker refresh endpoint
static/app.js                  # Update UI (buttons, staleness bar, detail panel)
app.py                         # Initialize scheduler on startup
```

### TR3: Config Settings (config.yaml)

```yaml
scheduler:
  enabled: true
  price_refresh_interval_minutes: 15
  market_hours:
    start: "09:30"           # Eastern Time
    end: "16:00"             # Eastern Time
    timezone: "America/New_York"

staleness:
  price_fresh_minutes: 60    # Green threshold
  price_stale_hours: 24      # Red threshold
  dividend_fresh_days: 7
  dividend_stale_days: 30
```

### TR4: New API Endpoints

```
POST /api/ticker/<symbol>/refresh    # Per-ticker refresh
GET  /api/data-freshness             # Staleness dashboard data
GET  /api/scheduler/status           # Auto-refresh status
POST /api/scheduler/toggle           # Enable/disable auto-refresh
```

### TR5: Database Changes

Add to `metadata` table (or create `data_freshness` table):
```sql
-- Track last update timestamps per data type
INSERT INTO metadata (key, value) VALUES
  ('last_price_update', '2025-12-25T10:30:00Z'),
  ('last_eps_update', '2025-12-25T08:00:00Z'),
  ('last_dividend_update', '2025-12-22T12:00:00Z');
```

### TR6: Scheduler Implementation

Use APScheduler (lightweight, Flask-compatible):
```python
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.add_job(
    auto_refresh_prices,
    'interval',
    minutes=config.PRICE_REFRESH_INTERVAL,
    id='auto_price_refresh'
)
```

Market hours check:
```python
def is_market_open():
    et = pytz.timezone('America/New_York')
    now = datetime.now(et)
    if now.weekday() >= 5:  # Weekend
        return False
    market_open = now.replace(hour=9, minute=30, second=0)
    market_close = now.replace(hour=16, minute=0, second=0)
    return market_open <= now <= market_close
```

---

## Implementation Hints

### Pattern: Background Thread Check
```python
# In auto_refresh_prices():
if screener_running:
    log.info("Skipping auto-refresh: manual update in progress")
    return
```

### Pattern: Staleness Calculation
```python
def get_data_freshness():
    return {
        'prices': {
            'last_update': get_metadata('last_price_update'),
            'age_minutes': calculate_age_minutes(...),
            'status': 'fresh' | 'aging' | 'stale'
        },
        'eps': {
            'total': count_total_tickers(),
            'complete': count_tickers_with_eps(),
            'missing': count_tickers_without_eps()
        },
        'dividends': {
            'last_update': get_metadata('last_dividend_update'),
            'age_days': calculate_age_days(...),
            'status': 'fresh' | 'aging' | 'stale'
        }
    }
```

### Pattern: Per-Ticker Refresh
```python
def refresh_single_ticker(symbol):
    """Refresh all data for a single ticker."""
    orchestrator = get_orchestrator()

    # Always fetch current price
    price_result = orchestrator.fetch_price(symbol, skip_cache=True)

    # Fetch EPS if missing or stale (> 30 days)
    if needs_eps_refresh(symbol):
        eps_result = fetch_eps_from_sec(symbol)

    # Fetch dividend if missing or stale (> 90 days)
    if needs_dividend_refresh(symbol):
        div_result = orchestrator.fetch_dividends(symbol)

    # Recalculate valuation
    recalculate_single_valuation(symbol)
```

---

## Acceptance Criteria

### AC1: UI Simplification
- [ ] Dropdown shows exactly 3 options: "Refresh Prices", "Full Sync", "Fix Missing"
- [ ] All options operate on all enabled indexes (no scope confusion)
- [ ] Old option names removed from UI

### AC2: Background Refresh
- [ ] Prices auto-refresh every 15 minutes during market hours
- [ ] No refresh on weekends or outside 9:30 AM - 4:00 PM ET
- [ ] Auto-refresh skipped if manual update is running
- [ ] Status shows "Auto-refresh: ON (next: Xm)" or "OFF"

### AC3: Staleness Dashboard
- [ ] Status bar visible in header area
- [ ] Shows prices age, EPS completeness, dividends age
- [ ] Color-coded freshness indicators (green/yellow/red)
- [ ] Updates after each refresh operation

### AC4: Per-Ticker Refresh
- [ ] "Refresh" button visible in ticker detail panel
- [ ] Clicking refreshes price + stale EPS + stale dividends
- [ ] Loading indicator during refresh
- [ ] Detail panel updates with fresh data on completion

### AC5: Configuration
- [ ] All scheduler settings in config.yaml
- [ ] Can disable auto-refresh via config
- [ ] Can adjust refresh interval via config
- [ ] Can adjust market hours via config

---

## Assumptions

1. **APScheduler is acceptable** - Lightweight scheduler library; alternative is simple threading with sleep loops
2. **pytz for timezone handling** - Standard library for market hours detection
3. **Staleness thresholds are configurable** - Can be tuned via config.yaml
4. **No database schema changes required** - Using existing `metadata` table for timestamps
5. **SSE activity stream continues to work** - Background refresh logs to same activity stream

---

## Out of Scope

- Mobile-specific UI optimizations
- Push notifications for stale data
- Per-index refresh scheduling (all indexes refresh together)
- Historical data backfill automation
- Multi-timezone market hours (US markets only)

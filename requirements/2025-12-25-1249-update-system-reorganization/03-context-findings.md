# Context Findings: Update System Analysis

## Current System Architecture

### Update Options Available to Users

| Option | Endpoint | Scope | Data Updated | Speed |
|--------|----------|-------|--------------|-------|
| Quick Update | `/api/screener/quick-update` | Current index | Prices only | 1-2min |
| All Prices | `/api/screener/quick-update` | All indexes | Prices only | 2-3min |
| Smart Update | `/api/screener/smart-update` | All indexes | EPS (new tickers) + Prices (all) | 5-10min |
| Full Update | `/api/screener/start` | All indexes | EPS + Dividends + Prices | 10-20min |

### Key Files

- **UI:** `static/app.js` (lines 1700-1870)
- **Routes:** `routes/screener.py`
- **Core Logic:** `services/screener.py`
- **Config:** `config.py` (STALE_DATA_HOURS=24, PRICE_CACHE_DURATION=300)
- **Config YAML:** `config.yaml` (all configurable values)

### Current Issues Identified

1. **Overlapping/Confusing Options**
   - "Quick" vs "All Prices" only differ in scope
   - User must understand which to use when

2. **Inconsistent Scoping**
   - Quick Update: Index-specific
   - Smart/Full Update: Always 'all' (no single-index option)

3. **Staleness Tracking is Client-Side Only**
   - No server-side staleness enforcement
   - 24-hour threshold hardcoded in JS
   - No "auto-refresh if stale" capability

4. **Dividend Backoff is Hardcoded**
   - 4-month threshold in screener.py line 224
   - Not configurable

5. **No Per-Ticker Refresh**
   - Can't refresh a single ticker on demand
   - Must run update for entire index

6. **last_updated is Misleading**
   - Shows MAX(updated) across all tickers
   - Doesn't reflect bulk operation timestamps

### Data Flow

```
UI Button → POST /api/screener/{type} → Background Thread
    → Provider System (fetch prices/EPS/dividends)
    → Database Updates (valuations, eps_history)
    → SSE Progress Updates → UI Completion
```

### Staleness Thresholds (Current)

| Data Type | Current Behavior | Ideal Frequency |
|-----------|------------------|-----------------|
| Prices | No auto-refresh, 24h UI warning | Real-time during market hours |
| Dividends | Skip if < 4 months old | Quarterly check |
| EPS | Always fetched in Full Update | Annual (after earnings) |

## Technical Deep Dive

### Threading Model (Current)

- **No background scheduler** - all updates are manual
- Daemon threads spawned via `threading.Thread(target=..., daemon=True)`
- `screener_running` flag prevents concurrent updates
- ThreadPoolExecutor used in provider registry for timeouts

### Config Settings (from config.yaml)

```yaml
cache:
  price_cache_duration: 300    # 5 minutes
  stale_data_hours: 24         # UI warning threshold

rate_limits:
  screener:
    dividend_backoff: 0.3
    ticker_pause: 0.5
    price_delay: 0.2

providers:
  price_cache_seconds: 3600    # 1 hour provider cache
  circuit_breaker:
    failure_threshold: 3
    cooldown_seconds: 120
```

### Database Tables Affected

- `valuations` - Main valuation data (current_price, eps_avg, etc.)
- `eps_history` - Annual EPS by year
- `tickers` - Status info (valuation_updated, updated)
- `metadata` - Stores refresh summary stats

### Missing Infrastructure

- No scheduler library (APScheduler, schedule, etc.)
- No market hours detection
- No per-data-type staleness tracking
- No per-ticker refresh endpoint

## Patterns to Follow

- Background thread spawning with `daemon=True`
- SSE for progress updates via `/api/activity-stream`
- Provider orchestrator for all data fetching
- Bulk database updates via `bulk_update_valuations()`
- Config values loaded from `config.yaml`

## Implementation Considerations

### For Background Scheduler
- Could use APScheduler (lightweight, Flask-friendly)
- Need market hours detection (9:30 AM - 4:00 PM ET)
- Should respect existing `screener_running` flag

### For Simplified UI
- Reduce 4 options to 2-3 clear actions
- Add staleness dashboard showing data freshness
- Add per-ticker refresh via right-click or inline button

### For Staleness Tracking
- Add `prices_updated`, `eps_updated`, `dividends_updated` columns
- Or create separate `data_freshness` table
- Server-side staleness checks before returning data

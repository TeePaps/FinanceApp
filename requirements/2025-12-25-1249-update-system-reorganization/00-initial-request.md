# Initial Request: Update System Reorganization

## User Request
Can you think of a better way to organize the update system? Different options or algorithms?

## Initial Analysis Provided

### Current Issues Identified:
1. **Overlapping options** - 4 update types with unclear differences
2. **All-or-nothing** - Full Update tries to do everything at once, fails silently
3. **No staleness tracking** - Can't tell what's fresh vs stale
4. **Confusing scoping** - "Quick" = current index, "All Prices" = all indexes

### Options Proposed:

#### Option A: Simplify to 3 Clear Actions
| Action            | What                                             | When                |
|-------------------|--------------------------------------------------|---------------------|
| Refresh Prices    | Prices only, all enabled indexes                 | Daily / on-demand   |
| Full Sync         | EPS + Dividends + Prices, all enabled indexes    | Weekly / monthly    |
| Fix Missing       | Full fetch only for tickers with incomplete data | After adding stocks |

**Pros:** Simple, clear purpose for each
**Cons:** Less granular control

#### Option B: Data-Type Based
Separate buttons for each data type:

| Data Type | Frequency | Action                                        |
|-----------|-----------|-----------------------------------------------|
| Prices    | Real-time | "Refresh Prices" - always fast                |
| Dividends | Quarterly | "Update Dividends" - fetch if >90 days old    |
| EPS       | Annual    | "Update EPS" - fetch from SEC if >30 days old |

Then a single "Recalculate Valuations" that combines whatever data exists.

**Pros:** Clear what you're updating, can run expensive SEC fetch separately
**Cons:** More clicks for full refresh

#### Option C: Smart Single Button
One "Sync" button that:
1. Checks what's stale per ticker (price >1hr, dividend >90 days, EPS >30 days)
2. Fetches only what's needed
3. Shows progress: "Updating 45 prices, 12 dividends, 3 EPS..."

Add a "Force Full Refresh" in settings for when things are broken.

**Pros:** User doesn't need to think, system does the right thing
**Cons:** Less predictable, harder to debug

#### Option D: Background + On-Demand (Recommended)
Automatic background jobs:
- Prices: Refresh every 15-30 min during market hours
- Dividends: Check weekly for updates
- EPS: Check monthly (or when earnings season)

Manual actions:
- "Refresh Now" - Immediate price update
- "Sync Ticker" - Right-click a ticker to force full refresh
- "Rebuild All" - Nuclear option in settings

Dashboard shows:
- Prices: Updated 2 min ago
- EPS: 98% complete (12 missing) [Fix Missing]
- Dividends: Updated 3 days ago

**Pros:** Mostly automatic, clear visibility into data state
**Cons:** More complex to implement

### Suggested Path Forward:

**Simplest improvement (Option A):**
- Merge "Quick" and "All Prices" → single "Refresh Prices"
- Keep "Full Update" but rename to "Full Sync"
- Keep "Smart" but rename to "Fix Missing"
- Remove confusion about index scope (always do enabled indexes)

**Better long-term (Option D):**
- Add staleness tracking to database
- Background refresh for prices
- Single smart "Sync" that updates what's needed
- Per-ticker "Force Refresh" option

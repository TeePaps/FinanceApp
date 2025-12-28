# Requirements Specification: Market Analysis Click Issue

## Problem Statement

Clicking on tickers and cards on the Market Analysis page sometimes doesn't work. Elements show hover effects but clicking produces no action. The issue persists even after page refresh.

## Root Cause

**Infinite loop in `static/app.js`:**

```
loadScreener() [line 2704]
    → calls checkScreenerProgress()
        → [line 3194-3195] if status === 'complete', calls loadScreener()
            → calls checkScreenerProgress()
                → ... infinite loop
```

When no screener operation is running, the API returns `status: 'complete'`, which triggers `loadScreener()` again. This creates hundreds of re-renders per second, making clicks impossible.

## Solution Overview

Break the infinite loop by tracking whether a screener was actually running before calling `loadScreener()` on completion.

## Functional Requirements

### FR1: Prevent Infinite Loop
- `checkScreenerProgress()` should only call `loadScreener()` when transitioning from 'running' to 'complete'
- Not when status is already 'complete' and nothing was running

### FR2: Fix SEC Status Error
- `loadSecStatus()` should check if required DOM elements exist before updating
- Alternatively, only call it when the screener tab is active

## Technical Requirements

### File: `static/app.js`

#### Change 1: Track previous running state (around line 3146)
Add a variable to track if screener was running:
```javascript
let wasScreenerRunning = false;
```

#### Change 2: Update checkScreenerProgress logic (lines 3151-3196)
- When status is 'running', set `wasScreenerRunning = true`
- When status is not 'running':
  - Only call `loadScreener()` if `wasScreenerRunning === true && progress.status === 'complete'`
  - Reset `wasScreenerRunning = false` after handling

#### Change 3: Fix SEC status null check (line 3230-3234)
Add null check before setting textContent:
```javascript
const cikStatus = document.getElementById('sec-cik-status');
if (cikStatus) {  // Add this check
    // existing code
}
```

## Implementation Hints

The key pattern to follow:
```javascript
let wasScreenerRunning = false;

async function checkScreenerProgress() {
    const progress = await response.json();

    if (progress.status === 'running') {
        wasScreenerRunning = true;
        // ... existing running logic
    } else {
        // ... existing UI cleanup

        // Only reload if we were actually running and completed
        if (wasScreenerRunning && progress.status === 'complete') {
            wasScreenerRunning = false;
            loadScreener();
        }
    }
}
```

## Acceptance Criteria

1. ✓ Page loads without console errors
2. ✓ No infinite loop in network/console
3. ✓ Clicking on undervalued cards navigates to Research tab
4. ✓ Clicking on table rows navigates to Research tab
5. ✓ Starting a screener update shows progress
6. ✓ After screener completes, data refreshes once (not infinitely)

## Testing

1. Load the Market Analysis page
2. Open DevTools console - should see NO repeated error messages
3. Click on any undervalued card - should navigate to Research tab
4. Click on any table row - should navigate to Research tab
5. Start a Quick/Smart/Full update, let it complete
6. Verify data refreshes once after completion, not continuously

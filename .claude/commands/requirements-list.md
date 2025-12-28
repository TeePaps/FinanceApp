# Requirements List

Display all requirements with their current status.

## Instructions:

1. Check requirements/.current-requirement for active requirement
2. List all folders in requirements/ directory
3. For each folder, extract metadata from metadata.json

## Sorting:
1. Active first
2. Then by status: complete, incomplete
3. Then by date (newest first)

## Display Format:
```
📋 Requirements Overview

🔴 ACTIVE: user-authentication (Phase 3: Expert Questions - 2/5)
   Started: 2 hours ago
   "Add user authentication with OAuth support"

✅ COMPLETE: dashboard-widgets (10 questions)
   Completed: 3 days ago
   "Add customizable dashboard widgets"
   → Linked: PR #123

✅ COMPLETE: api-rate-limiting (10 questions)
   Completed: 1 week ago
   "Implement API rate limiting"
   → Linked: PR #118

⚠️ INCOMPLETE: notification-system (5 questions)
   Paused: 5 days ago
   "Add push notification system"
   Stopped at: Phase 2 - Context Discovery

---
📊 Summary:
- Total: 4 requirements
- Complete: 2 (50%)
- Active: 1
- Incomplete: 1
- Avg questions: 8.3
```

## Artifact Linking:
- Show linked development sessions
- Show linked PRs/commits
- Allow linking with /requirements-link [id] [artifact]

## Stale Detection:
- Mark as incomplete if > 7 days without activity
- Suggest resuming or closing

## Quick Actions:
- /requirements-current [id] - View details
- /requirements-status - Continue active
- /requirements-start - Begin new

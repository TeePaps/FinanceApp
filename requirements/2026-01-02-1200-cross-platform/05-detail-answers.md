# Detail Answers

## Q1: Should lxml remain as an optional dependency?
**Answer:** Yes (keep lxml optional, use html5lib as fallback)

## Q2: Should the restart script be converted to Python?
**Answer:** Yes (single cross-platform Python script)

## Q3: Should the existing restart-server.sh be kept?
**Answer:** No (remove it, replace with Python script)

## Q4: Should we add psutil as a dependency?
**Answer:** No (use platform-specific commands without extra dependency)

## Q5: Should the app warn on startup if dependencies are missing?
**Answer:** Yes (check and print helpful installation message)

---

## Summary of Decisions
1. Keep lxml as optional, add html5lib + beautifulsoup4 as required
2. Create `restart_server.py` that auto-detects platform
3. Remove `restart-server.sh` after migration
4. No new dependencies beyond html5lib + beautifulsoup4
5. Add startup dependency check with helpful messages

# Discovery Questions

Based on initial codebase analysis, the following potential cross-platform issues were identified:

**Potential Issues Found:**
1. `restart-server.sh` - Uses bash-specific commands (pkill, lsof, nohup) that don't exist on Windows
2. `requirements.txt` - Some optional packages may have Windows-specific issues (ib_async)
3. Path handling - Uses `os.path.join()` which should be cross-platform compatible
4. File logging - Uses standard Python logging, should work cross-platform

---

## Q1: Are you experiencing library installation errors on Windows?
**Default if unknown:** Yes (this is a common Windows issue with Python packages)

---

## Q2: Are you experiencing errors when running the Flask app itself (after installation)?
**Default if unknown:** Yes (given you mentioned "commands cause issues")

---

## Q3: Do you need the `restart-server.sh` script functionality on Windows?
**Default if unknown:** Yes (common development workflow need)

---

## Q4: Are you using any optional providers that require additional packages (IBKR, Alpaca)?
**Default if unknown:** No (these are commented out in requirements.txt)

---

## Q5: Do you need the app to work identically on both platforms, or just be runnable?
**Default if unknown:** Just runnable (minimal changes, same core functionality)

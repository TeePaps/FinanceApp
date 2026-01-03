# Detail Questions

Based on deep codebase analysis, these questions address specific implementation decisions:

---

## Q1: Should lxml remain an optional dependency for performance on systems that can install it?
**Default if unknown:** Yes (lxml is faster, keep as optional for Mac/Linux users)

---

## Q2: Should the restart script be converted to Python (restart_server.py) for cross-platform compatibility?
**Default if unknown:** Yes (Python is already required, avoids need for bash/batch)

---

## Q3: Should the existing restart-server.sh be kept alongside the new Python script for users who prefer it?
**Default if unknown:** No (avoid maintaining duplicate functionality)

---

## Q4: Should we add psutil as a dependency for cross-platform process management?
**Default if unknown:** No (can use platform-specific commands without extra dependencies)

---

## Q5: Should the app detect and warn users on startup if required dependencies are missing?
**Default if unknown:** Yes (helps users diagnose installation issues)

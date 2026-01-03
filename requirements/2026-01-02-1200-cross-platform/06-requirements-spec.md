# Requirements Specification: Cross-Platform Support

## Problem Statement
The FinanceApp currently has Windows compatibility issues:
1. **lxml installation fails** on Windows without Visual C++ Build Tools
2. **restart-server.sh** uses Unix-only commands (pkill, lsof, nohup)

## Solution Overview
Make the app fully cross-platform (Windows + macOS) by:
1. Adding html5lib as a fallback HTML parser
2. Replacing the shell script with a cross-platform Python script
3. Adding startup checks for missing dependencies

---

## Functional Requirements

### FR1: HTML Parsing Fallback
- Add html5lib + beautifulsoup4 as required dependencies
- Keep lxml as optional (faster when available)
- Update `pd.read_html()` calls to use available parser automatically
- **Acceptance:** App works on Windows without lxml installed

### FR2: Cross-Platform Restart Script
- Create `restart_server.py` that works on Windows and Mac
- Auto-detect platform using `platform.system()`
- Windows: Use `taskkill` to stop processes, `subprocess.Popen` to start
- macOS: Use `pkill` to stop processes, backgrounding to start
- Remove existing `restart-server.sh`
- **Acceptance:** Running `python restart_server.py` works on both platforms

### FR3: Dependency Startup Check
- On app startup, check if html5lib or lxml is available
- If neither is installed, print warning with installation instructions
- Don't block startup, just warn
- **Acceptance:** Clear message shown if html parser missing

---

## Technical Requirements

### TR1: requirements.txt Updates
```
# Current
lxml>=4.9.0

# Change to
# HTML parsing (html5lib is cross-platform, lxml is optional but faster)
beautifulsoup4>=4.12.0
html5lib>=1.1

# Optional: lxml for faster parsing (requires C compiler on Windows)
# lxml>=4.9.0
```

### TR2: services/indexes/providers.py Changes
- Lines 155, 244: Update `pd.read_html()` to handle missing lxml
- Option A: Explicit `flavor='bs4'` (always uses html5lib)
- Option B: Try lxml first, catch ImportError, fallback to bs4 (preserves speed when available)
- **Recommended:** Option B for best of both worlds

### TR3: restart_server.py Implementation
```python
# Platform detection
import platform
import subprocess
import sys
import os

def restart_server():
    if platform.system() == 'Windows':
        # Windows: taskkill + subprocess with CREATE_NEW_CONSOLE
        ...
    else:
        # macOS/Linux: pkill + nohup
        ...
```

### TR4: File Changes Summary
| File | Action |
|------|--------|
| `requirements.txt` | Add html5lib, beautifulsoup4; comment out lxml |
| `services/indexes/providers.py` | Update pd.read_html() calls |
| `restart_server.py` | Create new cross-platform script |
| `restart-server.sh` | Delete |
| `app.py` | Add startup dependency check |

---

## Implementation Hints

### HTML Parser Detection Pattern
```python
def get_html_parser_flavor():
    """Return best available HTML parser for pd.read_html()"""
    try:
        import lxml
        return None  # Let pandas auto-detect (will use lxml)
    except ImportError:
        return 'bs4'  # Fall back to html5lib
```

### Windows Process Kill Pattern
```python
import subprocess
# Kill by process name on Windows
subprocess.run(['taskkill', '/F', '/IM', 'python.exe', '/FI', 'WINDOWTITLE eq app.py'],
               capture_output=True)
# Or by port
subprocess.run(['netstat', '-ano'], capture_output=True)  # Find PID
subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
```

### Background Process on Windows
```python
import subprocess
import sys
CREATE_NEW_CONSOLE = 0x00000010
subprocess.Popen([sys.executable, 'app.py'],
                 creationflags=CREATE_NEW_CONSOLE)
```

---

## Acceptance Criteria

1. [ ] `pip install -r requirements.txt` succeeds on Windows without errors
2. [ ] App starts successfully on Windows: `python app.py`
3. [ ] Index provider (S&P 500 fetch) works on Windows
4. [ ] `python restart_server.py` stops and restarts server on Windows
5. [ ] `python restart_server.py` stops and restarts server on macOS
6. [ ] Startup shows warning if no HTML parser available
7. [ ] No regression on macOS functionality

---

## Assumptions
- Python 3.9+ is used on both platforms (already required)
- User has permission to kill processes on their system
- Port 8080 is used for the Flask server (from current config)

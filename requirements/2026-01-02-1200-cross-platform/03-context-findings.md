# Context Findings

## Issue 1: lxml Installation on Windows

### Root Cause
- `lxml` is a C-extension library that requires compilation on Windows
- Without Microsoft Visual C++ Build Tools, installation fails
- Used by `pandas.read_html()` in `services/indexes/providers.py`

### Files Affected
- `requirements.txt` - Lists lxml as dependency
- `services/indexes/providers.py:155, 244` - Uses `pd.read_html()` for parsing Wikipedia tables

### Solution: html5lib Alternative
- **html5lib** is a pure-Python alternative that doesn't require C compilation
- Can be used with pandas via `pd.read_html(url, flavor='html5lib')` or `flavor='bs4'`
- Tradeoffs: html5lib is slower but more forgiving with broken HTML

### Implementation Approach
1. Add `html5lib` and `beautifulsoup4` to requirements.txt
2. Keep lxml as optional (for users who can install it - it's faster)
3. Update `pd.read_html()` calls to auto-detect available parser or use `flavor='bs4'`

---

## Issue 2: restart-server.sh Unix-Only

### Root Cause
- Uses bash-specific commands: `pkill`, `lsof`, `nohup`
- These don't exist on Windows

### Files Affected
- `restart-server.sh` - Unix/Mac only shell script

### Solution: Cross-Platform Python Script
Create a Python-based restart script that:
1. Detects platform using `platform.system()`
2. Uses appropriate commands per platform:
   - **Windows**: `taskkill /F /IM python.exe` (with filtering), `start /B`
   - **macOS/Linux**: `pkill`, `nohup`
3. Single `restart_server.py` that works everywhere

---

## Issue 3: Requirements Organization

### Current State
- `requirements.txt` has optional packages commented out
- No clear separation between required vs optional

### Recommendation
- Add platform-specific notes in requirements.txt
- Add html5lib + beautifulsoup4 as fallback dependencies
- Document installation instructions for each platform

---

## Technical Constraints

1. **pandas.read_html() behavior**:
   - Default `flavor=None` tries lxml first, then falls back to html5lib
   - Must have either lxml OR (beautifulsoup4 + html5lib) installed
   - Explicit `flavor='bs4'` forces html5lib usage

2. **Python platform detection**:
   ```python
   import platform
   if platform.system() == 'Windows':
       # Windows-specific code
   ```

3. **Process management on Windows**:
   - No `pkill` - use `taskkill` or `psutil` library
   - No `lsof` - use `netstat` or Python's `psutil`
   - No `nohup` - use `subprocess.Popen` with `creationflags`

---

## Integration Points

- `services/indexes/providers.py` - Only file using pd.read_html()
- `restart-server.sh` - Only Unix shell script
- `app.py` - Flask entry point (already cross-platform)
- `requirements.txt` - Dependency management

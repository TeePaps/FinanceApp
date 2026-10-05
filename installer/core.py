"""
Shared install/update helpers for FinanceApp (standard library only).

Used by:
  - installer/installer.py  (imported from the unpacked release being installed)
  - installer/uninstall.py  (imported from <HOME>/versions/<current>/installer/)
  - services/updater.py     (imported from the running version's code dir)

Contract: docs/installer-updater-design.md.

Rules for this module:
  - stdlib only, Python 3.8+ syntax (it may be imported by an older interpreter
    in an emergency; the app itself runs on uv-managed 3.12).
  - No side effects at import time.
  - Every function that does slow or visible work takes an optional ``log``
    callable (``log(str)``) for progress messages; it defaults to a no-op.
  - Failures raise ``InstallError`` with a human-readable message; helpers that
    answer a question (find_uv, port_in_use, ...) return values instead.
"""

import base64
import datetime as _dt
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile

APP_NAME = "FinanceApp"
GITHUB_REPO = "TeePaps/FinanceApp"
GITHUB_API = "https://api.github.com/repos/%s" % GITHUB_REPO
DEFAULT_PORT = 8765
DEFAULT_PYTHON = "3.12"
STATE_FILE = "install.json"
UNINSTALL_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\FinanceApp"
UV_INSTALL_SH = "https://astral.sh/uv/install.sh"
UV_INSTALL_PS1 = "https://astral.sh/uv/install.ps1"
USER_AGENT = "FinanceApp-installer"
ZIP_RE = re.compile(r"^FinanceApp-(\d+\.\d+\.\d+(?:[-.+]?[0-9A-Za-z.]+)?)\.zip$")

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# Things in <HOME>/data that make up the user's data set (copy/backup/restore).
DATA_ITEMS = ("data_public", "data_private", "config.yaml", "archive")


class InstallError(Exception):
    """A step failed; the message is meant for the user."""


def _nolog(msg):
    pass


def _log(log):
    return log if log is not None else _nolog


def now_iso():
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------

def default_home():
    """Install root: ~/FinanceApp (mac/linux) or %LOCALAPPDATA%\\FinanceApp."""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, APP_NAME)
    return os.path.join(os.path.expanduser("~"), APP_NAME)


def versions_dir(home):
    return os.path.join(home, "versions")


def version_dir(home, version):
    return os.path.join(home, "versions", version)


def data_dir(home):
    return os.path.join(home, "data")


def run_dir(home):
    return os.path.join(home, "run")


def backups_dir(home):
    return os.path.join(home, "backups")


def venv_python(version_dir_path):
    """Path of the venv interpreter inside a version dir (may not exist)."""
    venv = os.path.join(version_dir_path, "venv")
    if IS_WINDOWS:
        return os.path.join(venv, "Scripts", "python.exe")
    for name in ("python3", "python"):
        p = os.path.join(venv, "bin", name)
        if os.path.exists(p):
            return p
    return os.path.join(venv, "bin", "python3")


def base_python(version_dir_path, windowed=False):
    """The interpreter a version's venv was built from (outside the install root).

    Read from venv/pyvenv.cfg ``home``. Used for launchers and the Windows
    uninstaller, which must not run from inside a directory they may delete.
    ``windowed`` returns pythonw.exe on Windows. Returns None if unknown.
    """
    cfg = os.path.join(version_dir_path, "venv", "pyvenv.cfg")
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.partition("=")
                if sep and key.strip().lower() == "home":
                    bindir = value.strip()
                    break
            else:
                return None
    except OSError:
        return None
    if IS_WINDOWS:
        cand = [os.path.join(bindir, "pythonw.exe" if windowed else "python.exe")]
    else:
        cand = [os.path.join(bindir, "python3"), os.path.join(bindir, "python")]
    for c in cand:
        if os.path.exists(c):
            return c
    return None


def ensure_home_dirs(home):
    """Create the install root skeleton (idempotent; never touches contents)."""
    for d in (home, versions_dir(home), data_dir(home), run_dir(home),
              backups_dir(home), os.path.join(data_dir(home), "data_public")):
        os.makedirs(d, exist_ok=True)
    priv = os.path.join(data_dir(home), "data_private")
    if not os.path.isdir(priv):
        os.makedirs(priv, mode=0o700, exist_ok=True)


# --------------------------------------------------------------------------
# install.json
# --------------------------------------------------------------------------

def load_state(home):
    """Return install.json as a dict, or None if there is no install here."""
    path = os.path.join(home, STATE_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise InstallError("Cannot read %s: %s" % (path, e))
    return data if isinstance(data, dict) else None


def new_state(port=DEFAULT_PORT, python=DEFAULT_PYTHON):
    ts = now_iso()
    return {"current": None, "previous": None, "port": int(port),
            "python": python, "extras": [], "installed_at": ts, "updated_at": ts}


def save_state(home, state):
    """Atomically write install.json (keeps the human-readable layout the
    launcher shell scripts grep: one ``"current": "X"`` per line)."""
    os.makedirs(home, exist_ok=True)
    path = os.path.join(home, STATE_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)
    return state


def switch_current(home, version):
    """Make ``version`` current; the old current becomes previous.

    Switching to the current previous version is therefore a swap (rollback).
    Returns the saved state.
    """
    state = load_state(home) or new_state()
    # A deliberate switch supersedes launch.py's "version X failed to start"
    # note (otherwise the UI would keep reporting an old failure).
    state.pop("last_failed", None)
    cur = state.get("current")
    if cur and cur != version:
        state["previous"] = cur
    elif state.get("previous") == version:
        state["previous"] = None
    state.setdefault("previous", None)
    state["current"] = version
    state["updated_at"] = now_iso()
    return save_state(home, state)


# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------

def parse_version(v):
    """Sortable key for "1.2.3", "v1.2.3", "1.2.3-beta.1", "1.2.3b1".

    A pre-release sorts before the matching final release.
    """
    v = (v or "").strip().lstrip("vV")
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$", v)
    if not m:
        return (0, 0, 0, 0, v)
    major, minor, patch, rest = m.groups()
    rest = rest.lstrip("-.+")
    return (int(major), int(minor or 0), int(patch or 0),
            0 if rest else 1, rest)


def is_newer(candidate, current):
    if not candidate:
        return False
    if not current:
        return True
    return parse_version(candidate) > parse_version(current)


# --------------------------------------------------------------------------
# uv
# --------------------------------------------------------------------------

def _uv_names():
    return ("uv.exe",) if IS_WINDOWS else ("uv",)


def find_uv():
    """Locate uv: $FINANCEAPP_UV, PATH, then the official installer's
    default locations. Returns an absolute path or None."""
    env = os.environ.get("FINANCEAPP_UV", "").strip()
    if env and os.path.isfile(env):
        return os.path.abspath(env)
    found = shutil.which("uv")
    if found:
        return os.path.abspath(found)
    home = os.path.expanduser("~")
    dirs = []
    for var in ("UV_INSTALL_DIR", "XDG_BIN_HOME"):
        if os.environ.get(var):
            dirs.append(os.environ[var])
    dirs += [os.path.join(home, ".local", "bin"), os.path.join(home, ".cargo", "bin")]
    for d in dirs:
        for n in _uv_names():
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return None


def ensure_uv(log=None):
    """Return the uv path, installing it with the official installer if missing.

    The installer is run with UV_NO_MODIFY_PATH=1: FinanceApp always calls uv
    by absolute path, so the user's shell profile is left alone.
    """
    log = _log(log)
    uv = find_uv()
    if uv:
        return uv
    log("uv not found; installing it with the official installer...")
    env = dict(os.environ, UV_NO_MODIFY_PATH="1")
    if IS_WINDOWS:
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "ByPass", "-c",
               "irm %s | iex" % UV_INSTALL_PS1]
    else:
        cmd = ["sh", "-c", "curl -LsSf %s | sh" % UV_INSTALL_SH]
    run_logged(cmd, log, env=env)
    uv = find_uv()
    if not uv:
        raise InstallError("uv was installed but could not be found "
                           "(looked on PATH and in ~/.local/bin).")
    log("uv installed: %s" % uv)
    return uv


def uv_env(extra=None):
    """Environment for uv calls: only uv-managed Pythons, never system Python."""
    env = dict(os.environ)
    env["UV_PYTHON_PREFERENCE"] = "only-managed"
    env.pop("VIRTUAL_ENV", None)
    if extra:
        env.update(extra)
    return env


def run_logged(cmd, log=None, cwd=None, env=None, timeout=None):
    """Run a command, streaming its output to ``log``. Raises InstallError
    on a non-zero exit (message includes the last output lines)."""
    log = _log(log)
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                **kwargs)
    except OSError as e:
        raise InstallError("Could not run %s: %s" % (cmd[0], e))
    tail = []
    timed_out = []
    timer = None
    if timeout:
        def _kill():
            timed_out.append(True)
            try:
                proc.kill()
            except OSError:
                pass
        timer = threading.Timer(timeout, _kill)
        timer.daemon = True
        timer.start()
    try:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                log("    " + line)
                tail = (tail + [line])[-15:]
        proc.stdout.close()
        rc = proc.wait()
    finally:
        if timer:
            timer.cancel()
    if timed_out:
        raise InstallError("Timed out after %ss: %s" % (timeout, " ".join(str(c) for c in cmd[:3])))
    if rc != 0:
        raise InstallError("Command failed (exit %s): %s\n%s"
                           % (rc, " ".join(str(c) for c in cmd[:4]), "\n".join(tail)))
    return "\n".join(tail)


# --------------------------------------------------------------------------
# Downloads / releases
# --------------------------------------------------------------------------

# A scheme of 2+ chars ("file:", "https:"), so Windows "C:\\..." stays a path.
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:")


def to_url(path_or_url):
    """Plain filesystem paths become file:// URLs; URLs pass through."""
    s = str(path_or_url)
    if _URL_RE.match(s):
        return s
    return pathlib.Path(os.path.abspath(os.path.expanduser(s))).as_uri()


def _open(url, timeout=60):
    req = urllib.request.Request(to_url(url), headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json, */*"})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_json(url, timeout=30):
    try:
        with _open(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise InstallError("Could not read %s: %s" % (url, e))


def download(url, dest, log=None):
    """Download ``url`` (http(s) or file://) to ``dest`` atomically."""
    log = _log(log)
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    tmp = dest + ".part"
    log("Downloading %s" % url)
    try:
        with _open(url, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1024 * 256)
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise InstallError("Download failed: %s (%s)" % (url, e))
    os.replace(tmp, dest)
    return dest


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sha256sums(text):
    """``sha256  filename`` lines -> {filename: sha256}."""
    out = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.match(r"^[0-9a-fA-F]{64}$", parts[0]):
            out[parts[-1].lstrip("*")] = parts[0].lower()
    return out


def _normalize_release(raw, base_url=None):
    tag = raw.get("tag_name") or raw.get("tag") or ""
    version = raw.get("version") or tag.lstrip("vV")
    assets = {}
    raw_assets = raw.get("assets") or []
    if isinstance(raw_assets, dict):
        raw_assets = [{"name": k, "browser_download_url": v} for k, v in raw_assets.items()]
    for a in raw_assets:
        name = a.get("name")
        url = a.get("browser_download_url") or a.get("url")
        if not name or not url:
            continue
        if base_url and not _URL_RE.match(url):
            url = urllib.parse.urljoin(base_url, url)  # relative to the feed file
        assets[name] = url
    return {
        "version": version,
        "tag": tag or ("v" + version if version else ""),
        "notes": raw.get("body") or raw.get("notes") or "",
        "html_url": raw.get("html_url") or "",
        "prerelease": bool(raw.get("prerelease")),
        "draft": bool(raw.get("draft")),
        "published_at": raw.get("published_at"),
        "assets": assets,
    }


def _pick_release(releases, channel="stable", version=None):
    cands = []
    for r in releases:
        if r["draft"] or not r["version"]:
            continue
        if version is not None:
            if parse_version(r["version"]) == parse_version(version):
                return r
            continue
        if r["prerelease"] and channel != "beta":
            continue
        cands.append(r)
    if not cands:
        return None
    return max(cands, key=lambda r: parse_version(r["version"]))


def fetch_latest_release(feed_url=None, channel="stable", version=None):
    """Find the newest release (or a specific ``version``).

    Source, in order: ``feed_url`` argument, $FINANCEAPP_UPDATE_FEED, then the
    GitHub API. A feed may be a GitHub release object, a list of them, or
    ``{"releases": [...]}``; it may be a plain path or a file:// / http(s)
    URL, and its asset URLs may be file:// or relative to the feed.
    Drafts are skipped; pre-releases only count on the "beta" channel.

    Returns {version, tag, notes, html_url, prerelease, assets: {name: url}}
    (plus draft/published_at). Raises InstallError if nothing matches.
    """
    feed_url = feed_url or os.environ.get("FINANCEAPP_UPDATE_FEED") or None
    if feed_url:
        url = to_url(feed_url)
        data = fetch_json(url)
        if isinstance(data, dict) and isinstance(data.get("releases"), list):
            data = data["releases"]
        raws = data if isinstance(data, list) else [data]
        releases = [_normalize_release(r, base_url=url) for r in raws if isinstance(r, dict)]
    elif version is not None:
        tag = version if version.startswith("v") else "v" + version
        releases = [_normalize_release(fetch_json("%s/releases/tags/%s" % (GITHUB_API, tag)))]
    elif channel == "beta":
        data = fetch_json("%s/releases?per_page=20" % GITHUB_API)
        releases = [_normalize_release(r) for r in (data if isinstance(data, list) else [])]
    else:
        releases = [_normalize_release(fetch_json("%s/releases/latest" % GITHUB_API))]
    rel = _pick_release(releases, channel=channel, version=version)
    if not rel:
        raise InstallError("No %s found in %s" % (
            "release %s" % version if version else "%s release" % channel,
            feed_url or GITHUB_API))
    return rel


def release_zip_name(release):
    """Name of the FinanceApp-X.Y.Z.zip asset in a normalized release, or None."""
    exact = "FinanceApp-%s.zip" % release.get("version")
    if exact in release.get("assets", {}):
        return exact
    for name in release.get("assets", {}):
        if ZIP_RE.match(name):
            return name
    return None


def download_release(release, dest_dir, log=None, require_checksum=False):
    """Download a release's zip into ``dest_dir`` and verify it against
    SHA256SUMS.txt (when the release has one; required if ``require_checksum``).
    Returns the zip path."""
    log = _log(log)
    name = release_zip_name(release)
    if not name:
        raise InstallError("Release %s has no FinanceApp-*.zip asset" % release.get("version"))
    zpath = download(release["assets"][name], os.path.join(dest_dir, name), log)
    sums_url = release["assets"].get("SHA256SUMS.txt")
    if sums_url:
        sums_path = download(sums_url, os.path.join(dest_dir, "SHA256SUMS.txt"), log)
        with open(sums_path, "r", encoding="utf-8") as f:
            sums = parse_sha256sums(f.read())
        verify_sha256(zpath, sums.get(name))
        log("Checksum OK (%s)" % name)
    elif require_checksum:
        raise InstallError("Release %s has no SHA256SUMS.txt" % release.get("version"))
    else:
        log("Warning: release has no SHA256SUMS.txt; skipping checksum")
    return zpath


def verify_sha256(path, expected):
    if not expected:
        raise InstallError("No checksum listed for %s" % os.path.basename(path))
    actual = sha256(path)
    if actual != expected.lower():
        raise InstallError("Checksum mismatch for %s (expected %s, got %s)"
                           % (os.path.basename(path), expected, actual))


# --------------------------------------------------------------------------
# Unpacking
# --------------------------------------------------------------------------

def zip_top_dir(zip_path):
    """The single top-level folder name of a release zip."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            tops = {n.replace("\\", "/").split("/", 1)[0] for n in z.namelist() if n.strip("/")}
    except (OSError, zipfile.BadZipFile) as e:
        raise InstallError("Not a valid release zip: %s (%s)" % (zip_path, e))
    tops.discard("")
    if len(tops) != 1:
        raise InstallError("Release zip must contain one top-level folder, found: %s"
                           % ", ".join(sorted(tops)[:5]))
    return tops.pop()


def zip_version(zip_path):
    """X.Y.Z from the zip's ``FinanceApp-X.Y.Z/`` folder."""
    top = zip_top_dir(zip_path)
    m = re.match(r"^FinanceApp-(.+)$", top)
    if not m:
        raise InstallError("Unexpected top-level folder %r in release zip" % top)
    return m.group(1)


def read_zip_member(zip_path, relpath):
    """Bytes of ``<top>/relpath`` inside a release zip, or None."""
    top = zip_top_dir(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        try:
            return z.read("%s/%s" % (top, relpath))
        except KeyError:
            return None


def _safe_extract(z, dest):
    dest_real = os.path.realpath(dest)
    for info in z.infolist():
        name = info.filename.replace("\\", "/")
        target = os.path.realpath(os.path.join(dest, name))
        if target != dest_real and not target.startswith(dest_real + os.sep):
            raise InstallError("Unsafe path in zip: %s" % info.filename)
        z.extract(info, dest)
        mode = (info.external_attr >> 16) & 0o777
        if mode and not IS_WINDOWS and not name.endswith("/"):
            try:
                os.chmod(target, mode | stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass


def rmtree(path, log=None):
    """shutil.rmtree that clears read-only bits (Windows) and never raises."""
    def onerror(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, onerror=onerror)
    elif os.path.lexists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    if os.path.lexists(path) and log:
        log("Warning: could not fully remove %s" % path)
    return not os.path.lexists(path)


def unpack_release(zip_path, home, log=None):
    """Extract a release zip to ``<home>/versions/<X.Y.Z>`` and return that dir.

    Extracts into a staging dir and renames into place, so a failed extract
    never leaves a half-written version dir. An existing dir for the same
    version (repair) is replaced, venv included.
    """
    log = _log(log)
    version = zip_version(zip_path)
    top = zip_top_dir(zip_path)
    vroot = versions_dir(home)
    os.makedirs(vroot, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".staging-%s-" % version, dir=vroot)
    log("Unpacking %s ..." % os.path.basename(zip_path))
    try:
        with zipfile.ZipFile(zip_path) as z:
            _safe_extract(z, staging)
        src = os.path.join(staging, top)
        if not os.path.isfile(os.path.join(src, "app.py")):
            raise InstallError("Release zip has no app.py; not a FinanceApp release")
        target = version_dir(home, version)
        trash = None
        if os.path.exists(target):
            trash = os.path.join(vroot, ".trash-%s-%d" % (version, int(time.time())))
            os.replace(target, trash)
        os.replace(src, target)
    finally:
        rmtree(staging)
    if trash:
        rmtree(trash, log)
    log("Unpacked to %s" % target)
    return target


def staging_dir(home, version):
    return os.path.join(versions_dir(home), ".staging-%s" % version)


def clean_staging(home, log=None):
    """Remove leftover ``.staging-*`` / ``.trash-*`` dirs under versions/
    (from an interrupted install or update). Returns the removed paths."""
    root = versions_dir(home)
    removed = []
    try:
        names = os.listdir(root)
    except OSError:
        return removed
    for name in names:
        if name.startswith((".staging-", ".trash-")):
            p = os.path.join(root, name)
            _log(log)("Removing leftover %s" % name)
            rmtree(p, log)
            removed.append(p)
    return removed


def _rename_retry(src, dst, attempts=10):
    """os.replace with retries: on Windows a scanner/indexer briefly holding a
    file inside a freshly written tree makes directory renames fail."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.5)


def stage_release(zip_path, home, log=None):
    """Extract a release zip to ``versions/.staging-<X.Y.Z>`` (replacing a
    stale one) and return that dir. Nothing outside the staging dir changes."""
    log = _log(log)
    version = zip_version(zip_path)
    top = zip_top_dir(zip_path)
    vroot = versions_dir(home)
    os.makedirs(vroot, exist_ok=True)
    clean_staging(home, log)
    staging = staging_dir(home, version)
    tmp = tempfile.mkdtemp(prefix=".staging-%s-x" % version, dir=vroot)
    log("Unpacking %s ..." % os.path.basename(zip_path))
    try:
        with zipfile.ZipFile(zip_path) as z:
            _safe_extract(z, tmp)
        src = os.path.join(tmp, top)
        if not os.path.isfile(os.path.join(src, "app.py")):
            raise InstallError("Release zip has no app.py; not a FinanceApp release")
        _rename_retry(src, staging)
    finally:
        rmtree(tmp)
    return staging


def promote_staged(home, staging, version, log=None):
    """Move a built + smoke-tested staging dir to ``versions/<version>``.

    An existing dir for that version (repair, or re-applying the "previous"
    version) is moved aside first and put back if the final rename fails, so
    the install is never left without a working copy of that version.
    """
    log = _log(log)
    target = version_dir(home, version)
    trash = None
    if os.path.exists(target):
        trash = os.path.join(versions_dir(home), ".trash-%s-%d" % (version, int(time.time())))
        try:
            _rename_retry(target, trash)
        except OSError as e:
            raise InstallError("Could not replace %s (is FinanceApp %s still running?): %s"
                               % (target, version, e))
    try:
        _rename_retry(staging, target)
    except OSError as e:
        if trash:
            try:
                _rename_retry(trash, target)
            except OSError:
                pass
        raise InstallError("Could not move the new version into place: %s" % e)
    if trash:
        rmtree(trash, log)
    log("Installed to %s" % target)
    return target


def prepare_version(zip_path, home, state=None, log=None, uv=None):
    """Unpack, build the venv and smoke-test a release in a staging dir, then
    move it to ``versions/<X.Y.Z>``. Returns that dir.

    If any step fails the staging dir is removed and ``versions/`` is exactly
    as before (an existing dir for the same version is untouched), so a failed
    repair / re-apply never breaks the current or previous version.
    """
    log = _log(log)
    version = zip_version(zip_path)
    staging = stage_release(zip_path, home, log)
    try:
        build_venv(home, staging, state, log=log, uv=uv)
        smoke_test(home, staging, log=log)
        return promote_staged(home, staging, version, log)
    except BaseException:
        rmtree(staging)
        raise


# --------------------------------------------------------------------------
# venv / smoke test
# --------------------------------------------------------------------------

def build_venv(home, version_dir_path, state=None, log=None, uv=None):
    """Create ``<version>/venv`` with uv-managed Python and install
    requirements.txt plus ``state["extras"]``. Returns the venv python path."""
    log = _log(log)
    state = state or load_state(home) or {}
    py = str(state.get("python") or DEFAULT_PYTHON)
    uv = uv or ensure_uv(log)
    env = uv_env()
    log("Ensuring Python %s (uv-managed)..." % py)
    run_logged([uv, "python", "install", py], log, env=env)
    venv = os.path.join(version_dir_path, "venv")
    if os.path.exists(venv):
        rmtree(venv, log)
    log("Creating virtual environment...")
    # --relocatable: the venv is built in a staging dir and renamed into place
    # (prepare_version), so nothing in it may embed its absolute path.
    try:
        run_logged([uv, "venv", "--relocatable", "--python", py, venv], log, env=env,
                   cwd=version_dir_path)
    except InstallError:
        # uv < 0.4.4 has no --relocatable; app.py is started as "python app.py",
        # so a plain venv still works after the rename (only console scripts
        # in venv/bin would carry the old path).
        rmtree(venv)
        run_logged([uv, "venv", "--python", py, venv], log, env=env, cwd=version_dir_path)
    vpy = venv_python(version_dir_path)
    req = os.path.join(version_dir_path, "requirements.txt")
    log("Installing dependencies (this can take a minute)...")
    run_logged([uv, "pip", "install", "--python", vpy, "-r", req], log, env=env,
               cwd=version_dir_path)
    extras = [e for e in (state.get("extras") or []) if isinstance(e, str) and e.strip()]
    if extras:
        log("Installing extras: %s" % ", ".join(extras))
        run_logged([uv, "pip", "install", "--python", vpy] + extras, log, env=env,
                   cwd=version_dir_path)
    return vpy


def smoke_test(home, version_dir_path, log=None, isolated=True, timeout=300):
    """Run ``venv python -c "import app"`` with FINANCEAPP_HOME set.

    With ``isolated`` (default) FINANCEAPP_HOME points at a throwaway temp
    dir, so the import cannot migrate or otherwise touch the real data before
    a backup is taken; pass ``isolated=False`` to test against ``home``.
    Returns the imported version string; raises InstallError on failure.
    """
    log = _log(log)
    vpy = venv_python(version_dir_path)
    if not os.path.exists(vpy):
        raise InstallError("No venv in %s" % version_dir_path)
    tmp = tempfile.mkdtemp(prefix="financeapp-smoke-") if isolated else None
    # No .pyc: the dir may be a staging dir that is renamed afterwards.
    env = dict(os.environ, FINANCEAPP_HOME=tmp or home, FINANCEAPP_DEBUG="0",
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    log("Smoke test: import app ...")
    try:
        out = run_logged([vpy, "-c",
                          "import app, version; print('SMOKE_OK', version.__version__)"],
                         None, cwd=version_dir_path, env=env, timeout=timeout)
    except InstallError as e:
        raise InstallError("Smoke test failed: %s" % e)
    finally:
        if tmp:
            rmtree(tmp)
    m = re.search(r"SMOKE_OK (\S+)", out)
    if not m:
        raise InstallError("Smoke test produced no result:\n%s" % out)
    log("Smoke test OK (version %s)" % m.group(1))
    return m.group(1)


# --------------------------------------------------------------------------
# Data copy / backup / restore
# --------------------------------------------------------------------------

_SKIP_SUFFIXES = ("-wal", "-shm", "-journal", ".seed-tmp", ".tmp", ".part", ".lock")


def _is_sqlite(path):
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def copy_sqlite(src, dst):
    """Consistent copy of a (possibly live, WAL-mode) SQLite db via the
    backup API; falls back to a file copy if the source can't be opened."""
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    tmp = dst + ".copy-tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    try:
        s = sqlite3.connect("file:%s?mode=ro" % urllib.request.pathname2url(
            os.path.abspath(src)), uri=True)
        try:
            d = sqlite3.connect(tmp)
            try:
                s.backup(d)
            finally:
                d.close()
        finally:
            s.close()
    except sqlite3.Error:
        shutil.copyfile(src, tmp)
    os.replace(tmp, dst)
    for sfx in ("-wal", "-shm", "-journal"):  # never pair a fresh copy with stale sidecars
        try:
            os.remove(dst + sfx)
        except OSError:
            pass


def restore_sqlite_into(src, dst, timeout=30):
    """Overwrite the SQLite db ``dst`` with the contents of ``src`` using the
    backup API *into* the existing file. Safe while a running server has
    ``dst`` open (other connections see the new data on their next read),
    and works on Windows, where an open file cannot be replaced."""
    s = sqlite3.connect("file:%s?mode=ro" % urllib.request.pathname2url(
        os.path.abspath(src)), uri=True)
    try:
        d = sqlite3.connect(dst, timeout=timeout)
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def copy_tree_safe(src, dst):
    """Copy a directory tree, using copy_sqlite for SQLite files and skipping
    SQLite sidecar/temp files and __pycache__. Returns number of files copied."""
    n = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        rel = os.path.relpath(root, src)
        out = os.path.normpath(os.path.join(dst, rel))
        os.makedirs(out, exist_ok=True)
        for name in files:
            if name.endswith(_SKIP_SUFFIXES) or name == ".DS_Store":
                continue
            s = os.path.join(root, name)
            d = os.path.join(out, name)
            if _is_sqlite(s):
                copy_sqlite(s, d)
            else:
                shutil.copy2(s, d)
            n += 1
    return n


def copy_data_items(src_root, dst_root, items=DATA_ITEMS, replace=False, log=None):
    """Copy the data items (data_public/, data_private/, config.yaml, archive/)
    that exist under ``src_root`` into ``dst_root``. With ``replace``, each
    destination item is removed before copying. Returns the items copied."""
    log = _log(log)
    copied = []
    os.makedirs(dst_root, exist_ok=True)
    for item in items:
        s = os.path.join(src_root, item)
        d = os.path.join(dst_root, item)
        if os.path.isdir(s):
            if replace and os.path.exists(d):
                rmtree(d, log)
            n = copy_tree_safe(s, d)
            if item == "data_private":
                try:
                    os.chmod(d, 0o700)
                except OSError:
                    pass
            log("  %s/ (%d files)" % (item, n))
            copied.append(item)
        elif os.path.isfile(s):
            if replace and os.path.exists(d):
                os.remove(d)
            if _is_sqlite(s):
                copy_sqlite(s, d)
            else:
                shutil.copy2(s, d)
            log("  %s" % item)
            copied.append(item)
    return copied


def has_user_data(home):
    """True if the install has a private.db or user config worth protecting."""
    d = data_dir(home)
    return (os.path.exists(os.path.join(d, "data_private", "private.db"))
            or os.path.exists(os.path.join(d, "config.yaml")))


def unique_dir(path):
    """``path`` or ``path-2``, ``path-3``, ... whichever does not exist yet."""
    if not os.path.exists(path):
        return path
    n = 2
    while os.path.exists("%s-%d" % (path, n)):
        n += 1
    return "%s-%d" % (path, n)


def backup_data(home, dest, log=None, include_state=True):
    """Full copy of ``<home>/data`` (+ install.json) into ``dest`` (created).

    The result has the same layout as a dev clone (data_public/,
    data_private/, config.yaml), so ``installer.py --restore-from dest``
    or ``--import-from dest`` can read it. Returns ``dest``.
    """
    log = _log(log)
    os.makedirs(dest, exist_ok=True)
    log("Backing up data to %s" % dest)
    copy_data_items(data_dir(home), dest, log=log)
    if include_state and os.path.isfile(os.path.join(home, STATE_FILE)):
        shutil.copy2(os.path.join(home, STATE_FILE), os.path.join(dest, STATE_FILE))
    priv = os.path.join(dest, "data_private", "private.db")
    if os.path.exists(priv):
        check_sqlite(priv)
    with open(os.path.join(dest, "README.txt"), "w", encoding="utf-8") as f:
        f.write("FinanceApp data backup (%s)\nFrom: %s\n\n"
                "To restore into a new install, run the installer with:\n"
                "  --restore-from \"%s\"\n" % (now_iso(), home, dest))
    return dest


def check_sqlite(path):
    """Raise InstallError unless ``PRAGMA quick_check`` passes."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % urllib.request.pathname2url(
            os.path.abspath(path)), uri=True)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
        finally:
            con.close()
    except sqlite3.Error as e:
        raise InstallError("Backup check failed for %s: %s" % (path, e))
    if not row or row[0] != "ok":
        raise InstallError("Backup check failed for %s: %s" % (path, row))


def snapshot_private(home, version, log=None):
    """Snapshot private.db + config.yaml to ``backups/pre-<version>/``
    (taken before switching to ``version``). Returns the snapshot dir."""
    log = _log(log)
    dest = unique_dir(os.path.join(backups_dir(home), "pre-%s" % version))
    os.makedirs(dest)
    copied = copy_data_items(data_dir(home), dest, items=("config.yaml",), log=log)
    priv = os.path.join(data_dir(home), "data_private", "private.db")
    if os.path.exists(priv):
        copy_sqlite(priv, os.path.join(dest, "data_private", "private.db"))
        copied.append("private.db")
    log("Snapshot (%s) -> %s" % (", ".join(copied) or "nothing", dest))
    return dest


def prune_backups(home, keep=2, log=None):
    """Keep the ``keep`` newest ``backups/pre-*`` snapshots. Returns removed paths."""
    root = backups_dir(home)
    try:
        snaps = [os.path.join(root, n) for n in os.listdir(root) if n.startswith("pre-")]
    except OSError:
        return []
    snaps.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    removed = []
    for p in snaps[keep:]:
        rmtree(p, log)
        removed.append(p)
    return removed


def import_data(src_root, home, log=None):
    """Copy data_public/, data_private/, config.yaml (and archive/) from a dev
    clone or an uninstaller backup into ``<home>/data``.

    Existing user data is first backed up to ``backups/pre-import-<ts>/``,
    then each provided item replaces the installed one. ``install.json``
    "extras" from a backup are merged into the install's state.
    Returns the list of items imported.
    """
    log = _log(log)
    src_root = os.path.abspath(os.path.expanduser(src_root))
    present = [i for i in DATA_ITEMS if os.path.exists(os.path.join(src_root, i))]
    if not present:
        raise InstallError("%s has no data_public/, data_private/ or config.yaml"
                           % src_root)
    ensure_home_dirs(home)
    if has_user_data(home):
        bak = unique_dir(os.path.join(backups_dir(home), "pre-import-%s"
                                      % _dt.datetime.now().strftime("%Y%m%d-%H%M%S")))
        backup_data(home, bak, log=log, include_state=False)
    log("Importing data from %s" % src_root)
    copied = copy_data_items(src_root, data_dir(home), replace=True, log=log)
    try:
        with open(os.path.join(src_root, STATE_FILE), "r", encoding="utf-8") as f:
            extras = json.load(f).get("extras") or []
    except (OSError, ValueError, AttributeError):
        extras = []
    state = load_state(home)
    if extras and state is not None:
        merged = list(state.get("extras") or [])
        for e in extras:
            if e not in merged:
                merged.append(e)
        state["extras"] = merged
        save_state(home, state)
    return copied


# --------------------------------------------------------------------------
# Versions housekeeping
# --------------------------------------------------------------------------

def prune_versions(home, log=None):
    """Delete version dirs other than current and previous (and leftover
    staging/trash dirs). Returns the removed paths."""
    state = load_state(home) or {}
    keep = {v for v in (state.get("current"), state.get("previous")) if v}
    root = versions_dir(home)
    removed = []
    try:
        names = os.listdir(root)
    except OSError:
        return removed
    for name in names:
        p = os.path.join(root, name)
        if name in keep or not os.path.isdir(p):
            continue
        _log(log)("Removing old version %s" % name)
        rmtree(p, log)
        removed.append(p)
    return removed


# --------------------------------------------------------------------------
# Ports / server control
# --------------------------------------------------------------------------

def port_in_use(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def free_port(start=DEFAULT_PORT, host="127.0.0.1", limit=200):
    """First port >= start that nothing is listening on."""
    for port in range(int(start), int(start) + limit):
        if not port_in_use(port, host):
            return port
    raise InstallError("No free port found in %d-%d" % (start, start + limit - 1))


def health(port, timeout=2):
    """Parsed /healthz JSON from the local server, or None."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/healthz" % int(port),
                                    timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def run_launcher(home, command, log=None, python=None, timeout=120):
    """Run ``<home>/launch.py <command>`` and return its exit code."""
    launch = os.path.join(home, "launch.py")
    if not os.path.isfile(launch):
        return 1
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run([python or sys.executable, launch, command],
                             cwd=home, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, timeout=timeout, **kwargs)
    except (OSError, subprocess.SubprocessError) as e:
        _log(log)("launch.py %s failed: %s" % (command, e))
        return 1
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        if line.strip():
            _log(log)("    " + line)
    return out.returncode


# --------------------------------------------------------------------------
# Launchers / shortcuts
# --------------------------------------------------------------------------

def install_home_files(home, version_dir_path, log=None, uv=None):
    """Copy launch.py + uninstall.py from the version's installer/ dir into
    HOME and write the "Uninstall FinanceApp" script. Returns written paths."""
    log = _log(log)
    written = []
    src = os.path.join(version_dir_path, "installer")
    for name in ("launch.py", "uninstall.py"):
        s = os.path.join(src, name)
        if not os.path.isfile(s):
            raise InstallError("Release is missing installer/%s" % name)
        d = os.path.join(home, name)
        shutil.copy2(s, d + ".tmp")
        os.replace(d + ".tmp", d)
        written.append(d)
    uv = uv or find_uv() or ""
    if IS_WINDOWS:
        path = os.path.join(home, "Uninstall FinanceApp.bat")
        py = base_python(version_dir_path) or ""
        _write(path, _UNINSTALL_BAT.format(home=home, py=py, uv=uv), crlf=True)
    else:
        name = "Uninstall FinanceApp.command" if IS_MAC else "uninstall-financeapp.sh"
        path = os.path.join(home, name)
        _write(path, _posix_runner(home, "uninstall.py", '"$@"', uv, interactive=True))
        os.chmod(path, 0o755)
    written.append(path)
    log("Installed launch.py, uninstall.py and %s" % os.path.basename(path))
    return written


def _write(path, text, crlf=False):
    if crlf:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(path + ".tmp", "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


def _sh_quote(s):
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def _posix_runner(home, script, args, uv, interactive=False, logfile=None):
    """Shell script that runs <home>/<script> with the *current* version's venv
    python (read from install.json at run time, so it survives updates),
    falling back to ``uv run`` with uv-managed Python."""
    redirect = ' >>"$H/run/%s" 2>&1' % logfile if logfile else ""
    # Launched from Finder (logfile): tell launch.py there is no terminal, so
    # it reports failures in a dialog instead of only in the log.
    gui = "export FINANCEAPP_GUI=1\n" if logfile else ""
    tail = ('\nif [ -t 1 ]; then echo; { read -r -p "Press Return to close this window..." _ '
            '</dev/tty; } 2>/dev/null || true; fi\n' if interactive else "\n")
    return """#!/bin/bash
# Generated by the FinanceApp installer.
H={home}
UV={uv}
cd "$HOME" || cd /
{gui}mkdir -p "$H/run" 2>/dev/null || true
CUR=$(sed -n 's/^[[:space:]]*"current"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$H/install.json" 2>/dev/null | head -n 1)
PY="$H/versions/$CUR/venv/bin/python3"
if [ -n "$CUR" ] && [ -x "$PY" ]; then
  "$PY" "$H/{script}" {args}{redirect}
elif [ -x "$UV" ]; then
  UV_PYTHON_PREFERENCE=only-managed "$UV" run --no-project --python 3.12 "$H/{script}" {args}{redirect}
else
  echo "FinanceApp: cannot find Python for $H (re-run the installer)."{redirect}
  {alert}
fi{tail}""".format(home=_sh_quote(home), uv=_sh_quote(uv), script=script, args=args,
                   redirect=redirect, tail=tail, gui=gui,
                   alert=("osascript -e 'display alert \"FinanceApp\" message \"Cannot find "
                          "Python. Please re-run the FinanceApp installer.\"' >/dev/null 2>&1 || true"
                          if IS_MAC else "true"))


_UNINSTALL_BAT = r"""@echo off
rem Generated by the FinanceApp installer.
setlocal
cd /d "%USERPROFILE%"
set "FA_HOME={home}"
set "FA_PY={py}"
set "FA_UV={uv}"
rem Each branch is one parenthesized block: cmd parses it whole, so it still
rem finishes after uninstall.py deletes this file.
if exist "%FA_PY%" (
  "%FA_PY%" "%FA_HOME%\uninstall.py" %*
  exit /b
)
if exist "%FA_UV%" (
  set "UV_PYTHON_PREFERENCE=only-managed"
  "%FA_UV%" run --no-project --python 3.12 "%FA_HOME%\uninstall.py" %*
  exit /b
)
echo Could not find Python for FinanceApp. Delete "%FA_HOME%" manually.
pause
"""

_INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>FinanceApp</string>
  <key>CFBundleDisplayName</key><string>FinanceApp</string>
  <key>CFBundleIdentifier</key><string>com.teepaps.financeapp.launcher</string>
  <key>CFBundleVersion</key><string>{version}</string>
  <key>CFBundleShortVersionString</key><string>{version}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>FinanceApp</string>
  <key>LSMinimumSystemVersion</key><string>10.13</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


def mac_app_path():
    return os.path.join(os.path.expanduser("~"), "Applications", "FinanceApp.app")


def _install_mac_app(home, state, log):
    app = mac_app_path()
    macos = os.path.join(app, "Contents", "MacOS")
    os.makedirs(macos, exist_ok=True)
    _write(os.path.join(app, "Contents", "Info.plist"),
           _INFO_PLIST.format(version=state.get("current") or "0"))
    exe = os.path.join(macos, "FinanceApp")
    _write(exe, _posix_runner(home, "launch.py", "open", find_uv() or "",
                              logfile="launch.log"))
    os.chmod(exe, 0o755)
    try:  # nudge LaunchServices; harmless if it fails
        os.utime(app, None)
    except OSError:
        pass
    log("Created %s" % app)
    return [app]


def _install_linux_desktop(home, state, log):
    apps = os.path.join(os.environ.get("XDG_DATA_HOME") or
                        os.path.join(os.path.expanduser("~"), ".local", "share"),
                        "applications")
    os.makedirs(apps, exist_ok=True)
    runner = os.path.join(home, "financeapp-open.sh")
    _write(runner, _posix_runner(home, "launch.py", "open", find_uv() or "",
                                 logfile="launch.log"))
    os.chmod(runner, 0o755)
    desktop = os.path.join(apps, "financeapp.desktop")
    _write(desktop, "[Desktop Entry]\nType=Application\nName=FinanceApp\n"
                    "Comment=Stock portfolio valuation\nExec=\"%s\"\nTerminal=false\n"
                    "Categories=Office;Finance;\n" % runner)
    log("Created %s" % desktop)
    return [desktop]


def _powershell(script, env_extra=None, timeout=60):
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    env = dict(os.environ)
    env.update(env_extra or {})
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", enc],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        env=env, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode != 0:
        raise InstallError("PowerShell failed: %s" % out.stderr.decode("utf-8", "replace"))
    return out.stdout.decode("utf-8", "replace")


_PS_SHORTCUTS = r"""
$ErrorActionPreference = 'Stop'
# Paths are read back by Python as UTF-8 (non-ASCII user names); PS 5.1
# would otherwise write them in the OEM code page.
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$items = $env:FA_SHORTCUTS | ConvertFrom-Json
$sh = New-Object -ComObject WScript.Shell
foreach ($i in $items) {
  $dir = [Environment]::GetFolderPath($i.folder)
  if ($i.sub) { $dir = Join-Path $dir $i.sub; New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $p = Join-Path $dir ($i.name + '.lnk')
  $s = $sh.CreateShortcut($p)
  $s.TargetPath = $i.target
  $s.Arguments = $i.args
  $s.WorkingDirectory = $i.wd
  $s.Description = $i.desc
  if ($i.icon) { $s.IconLocation = $i.icon }
  $s.Save()
  Write-Output $p
}
"""


def _install_windows(home, state, log):
    vdir = version_dir(home, state["current"])
    pyw = base_python(vdir, windowed=True)
    if not pyw:
        raise InstallError("Cannot locate pythonw.exe for %s" % vdir)
    launch = os.path.join(home, "launch.py")
    uninst = os.path.join(home, "Uninstall FinanceApp.bat")
    items = [
        {"folder": "Desktop", "sub": "", "name": "FinanceApp", "target": pyw,
         "args": '"%s" open' % launch, "wd": home, "desc": "Open FinanceApp", "icon": ""},
        {"folder": "Programs", "sub": "FinanceApp", "name": "FinanceApp", "target": pyw,
         "args": '"%s" open' % launch, "wd": home, "desc": "Open FinanceApp", "icon": ""},
        {"folder": "Programs", "sub": "FinanceApp", "name": "Uninstall FinanceApp",
         "target": uninst, "args": "", "wd": home, "desc": "Uninstall FinanceApp", "icon": ""},
    ]
    out = _powershell(_PS_SHORTCUTS, {"FA_SHORTCUTS": json.dumps(items)})
    created = [l.strip() for l in out.splitlines() if l.strip().lower().endswith(".lnk")]
    for p in created:
        log("Created %s" % p)
    _write_uninstall_registry(home, state, uninst)
    log("Registered in Apps & features")
    return created


def _write_uninstall_registry(home, state, uninst_bat):
    import winreg  # noqa: windows only
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_REG_KEY, 0,
                            winreg.KEY_WRITE) as k:
        vals = {
            "DisplayName": "FinanceApp",
            "DisplayVersion": state.get("current") or "",
            "Publisher": "TeePaps",
            "InstallLocation": home,
            "UninstallString": 'cmd.exe /c ""%s""' % uninst_bat,
            "URLInfoAbout": "https://github.com/%s" % GITHUB_REPO,
        }
        for name, value in vals.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        for name in ("NoModify", "NoRepair"):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 1)


def install_launchers(home, state=None, log=None):
    """Create OS launchers for the current version and record them in
    install.json "launchers". Idempotent; call again after an update so
    Windows shortcuts / registry DisplayVersion stay current.

    Mac: ~/Applications/FinanceApp.app. Windows: Desktop + Start Menu
    shortcuts, Start Menu "Uninstall FinanceApp", HKCU uninstall entry.
    Linux: ~/.local/share/applications/financeapp.desktop.
    """
    log = _log(log)
    state = state or load_state(home)
    if not state or not state.get("current"):
        raise InstallError("No current version in %s" % home)
    if IS_MAC:
        made = _install_mac_app(home, state, log)
    elif IS_WINDOWS:
        made = _install_windows(home, state, log)
    else:
        made = _install_linux_desktop(home, state, log)
    fresh = load_state(home) or state
    fresh["launchers"] = made
    save_state(home, fresh)
    return made


def remove_launchers(home, state=None, log=None):
    """Remove everything install_launchers created (best effort)."""
    log = _log(log)
    state = state or load_state(home) or {}
    paths = list(state.get("launchers") or [])
    if IS_MAC and mac_app_path() not in paths:
        paths.append(mac_app_path())
    for p in paths:
        if not os.path.lexists(p):
            continue
        # Only launcher files we create, and only if they point at THIS home
        # (another install root may own ~/Applications/FinanceApp.app).
        if not p.endswith((".app", ".lnk", ".desktop")):
            continue
        if p.endswith((".app", ".desktop")) and not _launcher_targets(p, home):
            log("Leaving %s (it belongs to another install)" % p)
            continue
        rmtree(p, log)
        log("Removed %s" % p)
        parent = os.path.dirname(p)
        if os.path.basename(parent) == "FinanceApp" and IS_WINDOWS:
            try:
                os.rmdir(parent)  # Start Menu\Programs\FinanceApp if now empty
            except OSError:
                pass
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REG_KEY) as k:
                loc = winreg.QueryValueEx(k, "InstallLocation")[0]
            if os.path.normcase(os.path.abspath(loc)) == os.path.normcase(os.path.abspath(home)):
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REG_KEY)
                log("Removed Apps & features entry")
        except OSError:
            pass


def _launcher_targets(path, home):
    """True if the .app bundle / .desktop launcher at ``path`` runs ``home``."""
    script = path
    if path.endswith(".app"):
        script = os.path.join(path, "Contents", "MacOS", "FinanceApp")
    elif path.endswith(".desktop"):
        script = os.path.join(home, "financeapp-open.sh")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return script in f.read()
        except OSError:
            return False
    try:
        with open(script, "r", encoding="utf-8") as f:
            return ("H=%s\n" % _sh_quote(home)) in f.read()
    except OSError:
        return False


def purge_uv(log=None):
    """--purge: remove uv-managed Pythons, uv's cache, and uv itself if it is
    in the official installer's location (a Homebrew/pip uv is left alone)."""
    log = _log(log)
    uv = find_uv()
    if not uv:
        log("uv not found; nothing to purge")
        return
    for cmd in ([uv, "cache", "clean"], [uv, "python", "uninstall", "--all"]):
        try:
            run_logged(cmd, log, env=uv_env())
        except InstallError as e:
            log("Warning: %s" % e)
    official = os.path.join(os.path.expanduser("~"), ".local", "bin")
    if os.path.normcase(os.path.dirname(uv)) == os.path.normcase(official):
        for n in ("uv", "uvx", "uvw") if not IS_WINDOWS else ("uv.exe", "uvx.exe", "uvw.exe"):
            p = os.path.join(official, n)
            if os.path.exists(p):
                try:
                    os.remove(p)
                    log("Removed %s" % p)
                except OSError as e:
                    log("Warning: could not remove %s: %s" % (p, e))
    else:
        log("uv at %s was not installed by FinanceApp; leaving it" % uv)

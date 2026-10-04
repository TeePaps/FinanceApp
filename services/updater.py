"""
In-app updater (installed mode) - see docs/installer-updater-design.md, "Updater".

All install mechanics (download, checksum, unpack, venv, smoke test, snapshot,
switch, prune, launchers) come from installer/core.py, which ships inside the
release zip and is loaded from paths.CODE_DIR/installer/core.py, so the helpers
always match the running version. This module only orchestrates them:

    check()      latest release for the configured channel, cached in
                 RUN_DIR/update_check.json ($FINANCEAPP_UPDATE_FEED honoured)
    start_apply() background job: download -> verify -> unpack -> venv ->
                 smoke test -> snapshot -> switch -> home files/launchers ->
                 prune -> spawn detached "launch.py restart"
    rollback()   schema check, optional restore of backups/pre-<current>/,
                 swap current/previous, restart
    settings     user config.yaml "updates:" section (auto_check, channel)

Dev mode (not installed, or CODE_DIR is a git checkout): check() works so the
UI can show the latest version, but apply/rollback refuse.
"""

import importlib.util
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import urllib.request
from datetime import datetime, timezone

import paths
from version import __version__

IS_WINDOWS = platform.system() == "Windows"
CHANNELS = ("stable", "beta")
STARTUP_CHECK_DELAY = 30          # seconds after server start
CHECK_INTERVAL_HOURS = 24
RESTART_DELAY = 1.0               # let the HTTP response go out before restarting
RESTART_WATCHDOG = 300           # seconds; launch.py should have stopped us by then
DEV_MESSAGE = ("This copy runs from a development checkout; update it with "
               "'git pull' (self-update is only available in installed copies).")

_core = None
_core_lock = threading.Lock()
_job_lock = threading.Lock()
_check_lock = threading.Lock()
_settings_lock = threading.Lock()
_auto_scheduler = None

_job = {"state": "idle", "step": None, "message": None, "error": None,
        "action": None, "version": None, "from_version": None,
        "started_at": None, "finished_at": None, "log": []}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def core():
    """installer/core.py of the running version (loaded by path, cached)."""
    global _core
    if _core is not None:
        return _core
    with _core_lock:
        if _core is None:
            path = os.path.join(paths.CODE_DIR, "installer", "core.py")
            if not os.path.isfile(path):
                raise RuntimeError("installer/core.py is missing from %s" % paths.CODE_DIR)
            spec = importlib.util.spec_from_file_location("financeapp_installer_core", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _core = mod
    return _core


def is_git_checkout():
    return os.path.exists(os.path.join(paths.CODE_DIR, ".git"))


def mode():
    """"installed" only when running from <HOME>/versions/<X> of a real install."""
    if not paths.IS_INSTALLED or is_git_checkout():
        return "dev"
    parent = os.path.dirname(os.path.normcase(os.path.realpath(paths.CODE_DIR)))
    expected = os.path.normcase(os.path.realpath(os.path.join(paths.INSTALL_HOME, "versions")))
    if parent != expected or not os.path.isfile(os.path.join(paths.INSTALL_HOME, "install.json")):
        return "dev"
    return "installed"


def _home():
    return paths.INSTALL_HOME


def _check_file():
    return os.path.join(paths.RUN_DIR, "update_check.json")


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _install_state():
    if mode() != "installed":
        return {}
    try:
        return core().load_state(_home()) or {}
    except Exception:
        return {}


# --------------------------------------------------------------------------
# settings (user config.yaml "updates:" section)
# --------------------------------------------------------------------------

def _yaml():
    from user_config import _yaml as make_yaml
    return make_yaml()


def get_settings():
    """{auto_check: bool, channel: "stable"|"beta", auto_check_configured: bool|None}.

    auto_check unset (null) means: on when installed, off in dev mode.
    """
    from user_config import ensure_user_config
    ensure_user_config()
    section = {}
    try:
        with open(paths.USER_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = _yaml().load(f) or {}
        if isinstance(data, dict) and isinstance(data.get("updates"), dict):
            section = data["updates"]
    except Exception:
        section = {}
    configured = section.get("auto_check")
    configured = bool(configured) if isinstance(configured, bool) else None
    channel = str(section.get("channel") or "stable").strip().lower()
    if channel not in CHANNELS:
        channel = "stable"
    auto = configured if configured is not None else (mode() == "installed")
    return {"auto_check": auto, "channel": channel, "auto_check_configured": configured}


def save_settings(auto_check=None, channel=None):
    """Write auto_check / channel into config.yaml (comments preserved)."""
    if channel is not None:
        channel = str(channel).strip().lower()
        if channel not in CHANNELS:
            raise ValueError("channel must be one of: %s" % ", ".join(CHANNELS))
    from user_config import ensure_user_config
    ensure_user_config()
    cfg_path = paths.USER_CONFIG_FILE
    with _settings_lock:
        y = _yaml()
        data = None
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = y.load(f)
        except FileNotFoundError:
            data = None
        if data is None:
            from ruamel.yaml.comments import CommentedMap
            data = CommentedMap()
        if not isinstance(data, dict):
            raise ValueError("%s is not a mapping" % cfg_path)
        section = data.get("updates")
        if not isinstance(section, dict):
            from ruamel.yaml.comments import CommentedMap
            section = CommentedMap()
            data["updates"] = section
        if auto_check is not None:
            section["auto_check"] = bool(auto_check)
        if channel is not None:
            section["channel"] = channel
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            y.dump(data, f)
        os.replace(tmp, cfg_path)
    # services/providers/config.py keeps the whole document cached and dumps
    # it on save; refresh it so a later provider save keeps our section.
    try:
        from services.providers import config as pconf
        if pconf._full_yaml is not None and os.path.abspath(pconf.CONFIG_FILE) == os.path.abspath(cfg_path):
            pconf._load_yaml()
    except Exception:
        pass
    if channel is not None:
        cached = _read_json(_check_file())
        if cached.get("channel") and cached.get("channel") != channel:
            try:
                os.remove(_check_file())
            except OSError:
                pass
    return get_settings()


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------

def get_cached_check():
    return _read_json(_check_file())


def check():
    """Look up the latest release for the configured channel; cache and return it."""
    settings = get_settings()
    channel = settings["channel"]
    with _check_lock:
        cached = _read_json(_check_file())
        if cached.get("channel") != channel:
            cached = {}
        result = dict(cached)
        result.update({"checked_at": _now_iso(), "channel": channel,
                       "feed": os.environ.get("FINANCEAPP_UPDATE_FEED") or None,
                       "error": None})
        try:
            rel = core().fetch_latest_release(channel=channel)
            result.update({
                "latest": rel["version"], "tag": rel.get("tag"),
                "notes": rel.get("notes") or "", "html_url": rel.get("html_url") or "",
                "prerelease": bool(rel.get("prerelease")),
                "published_at": rel.get("published_at"),
                "has_zip": bool(core().release_zip_name(rel)),
            })
        except Exception as e:
            result["error"] = str(e)
        try:
            _write_json(_check_file(), result)
        except OSError as e:
            print("[Updater] Could not write %s: %s" % (_check_file(), e))
        if result["error"]:
            print("[Updater] Update check failed: %s" % result["error"])
        else:
            print("[Updater] Latest %s release: %s (running %s)"
                  % (channel, result.get("latest"), __version__))
        return result


def _is_newer(candidate, current):
    try:
        return core().is_newer(candidate, current)
    except Exception:
        return False


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def get_job():
    with _job_lock:
        j = dict(_job)
        j["log"] = list(_job["log"][-40:])
    return j


def get_status():
    m = mode()
    settings = get_settings()
    cached = get_cached_check()
    if cached.get("channel") and cached.get("channel") != settings["channel"]:
        cached = {}
    latest = cached.get("latest")
    state = _install_state()
    previous = state.get("previous") if m == "installed" else None
    prev_ok = bool(previous) and os.path.isdir(os.path.join(_home(), "versions", previous))
    data = {
        "mode": m,
        "current": __version__,
        "installed_current": state.get("current") if m == "installed" else None,
        "latest": latest,
        "available": bool(latest) and _is_newer(latest, __version__),
        "notes": cached.get("notes") or "",
        "html_url": cached.get("html_url") or "",
        "prerelease": bool(cached.get("prerelease")),
        "published_at": cached.get("published_at"),
        "checked_at": cached.get("checked_at"),
        "check_error": cached.get("error"),
        "previous": previous if prev_ok else None,
        "previous_newer": bool(prev_ok and _is_newer(previous, __version__)),
        "last_failed": state.get("last_failed") if m == "installed" else None,
        "auto_check": settings["auto_check"],
        "channel": settings["channel"],
        "can_update": m == "installed",
        "dev_message": DEV_MESSAGE if m == "dev" else None,
        "home": _home() if m == "installed" else None,
        "log_file": os.path.join(paths.RUN_DIR, "server.log"),
        "job": get_job(),
    }
    return data


# --------------------------------------------------------------------------
# job plumbing
# --------------------------------------------------------------------------

def _job_update(**kw):
    with _job_lock:
        _job.update(kw)
        msg = kw.get("message")
        if msg:
            _job["log"].append(msg)
            del _job["log"][:-200]


def _job_log(msg):
    msg = str(msg).rstrip()
    if not msg:
        return
    print("[Updater] %s" % msg.strip())
    with _job_lock:
        _job["log"].append(msg.strip())
        del _job["log"][:-200]
        # Keep the one-line message for the UI to the step-level lines.
        if not msg.startswith("    "):
            _job["message"] = msg.strip()


def _try_begin(action, version):
    """Claim the single job slot. Returns an error string or None."""
    with _job_lock:
        if _job["state"] in ("running", "restarting"):
            return "An update job is already in progress (%s)." % (_job.get("action") or "update")
        _job.update({"state": "running", "step": "starting", "message": None,
                     "error": None, "action": action, "version": version,
                     "from_version": __version__, "started_at": _now_iso(),
                     "finished_at": None, "log": []})
    return None


def _fail(message):
    _job_update(state="error", error=message, message=message, finished_at=_now_iso())
    print("[Updater] FAILED: %s" % message)


# --------------------------------------------------------------------------
# restart
# --------------------------------------------------------------------------

def _child_env():
    env = dict(os.environ)  # keeps FINANCEAPP_UPDATE_FEED etc.
    for k in list(env):
        if k.startswith("WERKZEUG_") or k in ("FINANCEAPP_SERVER_LOG", "VIRTUAL_ENV", "PYTHONPATH"):
            env.pop(k, None)
    return env


# Windows: taskkill /T (used by restart_server.py to stop this server) kills
# the whole process tree, which would include a launch.py we spawned directly.
# A short-lived trampoline starts launch.py and exits, so launch.py's parent is
# gone and it is no longer part of this server's tree.
_WIN_TRAMPOLINE = (
    "import json,subprocess,sys\n"
    "cmd=json.loads(sys.argv[1]); flags=int(sys.argv[2]); log=open(sys.argv[3],'a')\n"
    "subprocess.Popen(cmd, cwd=sys.argv[4], stdin=subprocess.DEVNULL, stdout=log,\n"
    "                 stderr=subprocess.STDOUT, creationflags=flags, close_fds=True)\n"
)


def spawn_restart(home, vdir):
    """Start ``<vdir venv python> <home>/launch.py restart`` fully detached.

    launch.py stops this server (pid file / port), starts the version that
    install.json now names, and reverts to "previous" if it fails its health
    check. install.json must be complete before this is called.
    """
    c = core()
    py = c.venv_python(vdir)
    if not os.path.exists(py):
        py = sys.executable
    launch = os.path.join(home, "launch.py")
    cmd = [py, launch, "restart"]
    log_path = os.path.join(paths.RUN_DIR, "update-restart.log")
    os.makedirs(paths.RUN_DIR, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write("=== %s: %s ===\n" % (_now_iso(), " ".join(cmd)))
    env = _child_env()
    if IS_WINDOWS:
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
                 | getattr(subprocess, "CREATE_NO_WINDOW", 0x8000000))
        subprocess.Popen([py, "-c", _WIN_TRAMPOLINE, json.dumps(cmd), str(flags), log_path, home],
                         cwd=home, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True)
    else:
        with open(log_path, "a", encoding="utf-8") as fh:
            subprocess.Popen(cmd, cwd=home, env=env, stdin=subprocess.DEVNULL,
                             stdout=fh, stderr=subprocess.STDOUT,
                             start_new_session=True, close_fds=True)
    print("[Updater] Spawned %s" % " ".join(cmd))
    # launch.py normally stops this process within seconds. If we are still
    # here much later, the restart did not happen: free the job slot.
    wd = threading.Timer(RESTART_WATCHDOG, _restart_watchdog, args=(log_path,))
    wd.daemon = True
    wd.start()


def _restart_watchdog(log_path):
    with _job_lock:
        if _job["state"] != "restarting":
            return
    _fail("FinanceApp did not restart (see %s). install.json already names the new "
          "version; restart FinanceApp to finish." % log_path)


def _refresh_home_files(home, vdir, state):
    """Contract: after switching versions refresh launch.py/uninstall.py and,
    when this install has launchers, the launchers (Windows shortcut targets,
    registry DisplayVersion, Mac .app plist). Failures are warnings."""
    c = core()
    try:
        c.install_home_files(home, vdir, log=_job_log)
    except Exception as e:
        _job_log("Warning: could not refresh launch.py/uninstall.py: %s" % e)
    if state.get("launchers"):
        try:
            c.install_launchers(home, log=_job_log)
        except Exception as e:
            _job_log("Warning: could not refresh launchers: %s" % e)


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------

def start_apply(version=None):
    """Start the background update job. Returns (ok, data_or_error)."""
    if mode() != "installed":
        return False, DEV_MESSAGE
    settings = get_settings()
    target = version
    if not target:
        cached = get_cached_check()
        if cached.get("channel") != settings["channel"] or not cached.get("latest") \
                or cached.get("error"):
            cached = check()
        if cached.get("error"):
            return False, "Could not check for updates: %s" % cached["error"]
        target = cached.get("latest")
    target = str(target or "").strip().lstrip("vV")
    if not target or not _is_newer(target, __version__):
        return False, "FinanceApp %s is up to date." % __version__
    err = _try_begin("update", target)
    if err:
        return False, err
    t = threading.Thread(target=_apply_worker, args=(target, settings["channel"]),
                         name="updater-apply", daemon=True)
    t.start()
    return True, {"version": target, "job": get_job()}


def _apply_worker(target, channel):
    c = core()
    home = _home()
    orig_state = None
    switched = False
    vdir = None
    tmpdir = None
    try:
        orig_state = c.load_state(home) or {}
        if orig_state.get("current") != __version__:
            raise c.InstallError(
                "install.json says %s but %s is running; restart FinanceApp first."
                % (orig_state.get("current"), __version__))

        _job_update(step="download", message="Downloading FinanceApp %s..." % target)
        rel = c.fetch_latest_release(channel=channel, version=target)
        tmpdir = tempfile.mkdtemp(prefix="update-%s-" % target, dir=paths.RUN_DIR)
        # A custom feed (testing) may omit SHA256SUMS.txt; GitHub releases must have it.
        require = not os.environ.get("FINANCEAPP_UPDATE_FEED")
        zpath = c.download_release(rel, tmpdir, log=_job_log, require_checksum=require)
        zv = c.zip_version(zpath)
        if c.parse_version(zv) != c.parse_version(target):
            raise c.InstallError("Downloaded zip is version %s, expected %s" % (zv, target))

        _job_update(step="unpack", message="Unpacking %s..." % target)
        vdir = c.unpack_release(zpath, home, log=_job_log)
        c.rmtree(tmpdir)
        tmpdir = None

        _job_update(step="venv", message="Installing dependencies (this can take a minute)...")
        c.build_venv(home, vdir, orig_state, log=_job_log)

        _job_update(step="smoke_test", message="Testing the new version...")
        c.smoke_test(home, vdir, log=_job_log)

        _job_update(step="backup", message="Backing up your data...")
        if c.has_user_data(home):
            c.snapshot_private(home, target, log=_job_log)
            c.prune_backups(home, log=_job_log)

        _job_update(step="switch", message="Switching to %s..." % target)
        state = c.switch_current(home, target)
        switched = True
        _refresh_home_files(home, vdir, state)

        _job_update(step="prune", message="Removing old versions...")
        c.prune_versions(home, log=_job_log)

        # install.json is complete (current=new, previous=old) from here on.
        _job_update(step="restart", state="restarting",
                    message="Restarting FinanceApp %s..." % target)
        spawn_restart(home, vdir)
        _job_update(finished_at=_now_iso())
    except Exception as e:
        msg = str(e) or e.__class__.__name__
        if switched and orig_state:
            try:
                c.save_state(home, orig_state)
                _job_log("Restored install.json (still on %s)" % __version__)
                old_vdir = os.path.join(home, "versions", __version__)
                c.install_home_files(home, old_vdir)
            except Exception as e2:
                _job_log("Warning: could not restore install.json: %s" % e2)
        elif vdir and orig_state and target not in (orig_state.get("current"), orig_state.get("previous")):
            c.rmtree(vdir)  # half-built new version; nothing points at it
        _fail(msg)
    finally:
        if tmpdir:
            c.rmtree(tmpdir)


# --------------------------------------------------------------------------
# rollback
# --------------------------------------------------------------------------

_SCHEMA_RE = {
    "public": re.compile(r"^SCHEMA_VERSION_PUBLIC\s*=\s*(\d+)", re.M),
    "private": re.compile(r"^SCHEMA_VERSION_PRIVATE\s*=\s*(\d+)", re.M),
}


def expected_schema(vdir):
    """{"public": n|None, "private": n|None} read from a version's database.py."""
    out = {"public": None, "private": None}
    try:
        with open(os.path.join(vdir, "database.py"), "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return out
    for key, rx in _SCHEMA_RE.items():
        m = rx.search(text)
        if m:
            out[key] = int(m.group(1))
    return out


def schema_check(prev_vdir):
    """Compare the previous version's expected schema with the live DBs."""
    import database
    actual = {"public": database.get_schema_version(paths.PUBLIC_DB_PATH),
              "private": database.get_schema_version(paths.PRIVATE_DB_PATH)}
    expected = expected_schema(prev_vdir)
    incompatible, warnings = [], []
    for key in ("public", "private"):
        exp, act = expected[key], actual[key]
        if exp is None:
            warnings.append("Could not determine the %s.db schema the previous version "
                            "expects; assuming it is compatible." % key)
        elif act is not None and act > exp:
            incompatible.append(key)
    return {"expected": expected, "actual": actual,
            "incompatible": incompatible, "warnings": warnings}


def _restore_private(backup_dir):
    """Copy backups/pre-<current>/ (private.db + config.yaml) back into data/.

    private.db is restored with SQLite's backup API *into* the live file, so
    it is safe while this server still has it open (and works on Windows,
    where an open file cannot be replaced).
    """
    c = core()
    restored = []
    src_db = os.path.join(backup_dir, "data_private", "private.db")
    if os.path.isfile(src_db):
        dst_db = paths.PRIVATE_DB_PATH
        src = sqlite3.connect("file:%s?mode=ro" % urllib.request.pathname2url(
            os.path.abspath(src_db)), uri=True)
        try:
            dst = sqlite3.connect(dst_db, timeout=30)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        restored.append("private.db")
    src_cfg = os.path.join(backup_dir, "config.yaml")
    if os.path.isfile(src_cfg):
        tmp = paths.USER_CONFIG_FILE + ".restore-tmp"
        shutil.copy2(src_cfg, tmp)
        os.replace(tmp, paths.USER_CONFIG_FILE)
        restored.append("config.yaml")
    if not restored:
        raise c.InstallError("Backup %s has nothing to restore" % backup_dir)
    return restored


def rollback(restore_data=False):
    """Switch back to the previous version. Returns (ok, data_or_error).

    ok=True with data["needs_restore"] means nothing was changed: the previous
    version expects an older private.db schema, and the caller must confirm
    restore_data=True (restores backups/pre-<current>/, losing changes made
    since the update).
    """
    if mode() != "installed":
        return False, DEV_MESSAGE
    c = core()
    home = _home()
    state = c.load_state(home) or {}
    prev = state.get("previous")
    cur = state.get("current")
    if not prev:
        return False, "There is no previous version to revert to."
    prev_vdir = os.path.join(home, "versions", prev)
    if not os.path.isfile(os.path.join(prev_vdir, "app.py")) or \
            not os.path.exists(c.venv_python(prev_vdir)):
        return False, "Previous version %s is not available on disk." % prev
    if cur != __version__:
        return False, ("install.json says %s but %s is running; restart FinanceApp first."
                       % (cur, __version__))

    sc = schema_check(prev_vdir)
    backup_dir = os.path.join(c.backups_dir(home), "pre-%s" % cur)
    has_backup = os.path.isfile(os.path.join(backup_dir, "data_private", "private.db"))
    needs_restore = "private" in sc["incompatible"]
    warnings = list(sc["warnings"])
    if "public" in sc["incompatible"]:
        warnings.append("public.db was upgraded by %s; %s may not read it fully. It is "
                        "rebuildable market data (delete data/data_public/public.db to "
                        "reseed it)." % (cur, prev))
    if needs_restore and not restore_data:
        return True, {"needs_restore": True, "previous": prev, "current": cur,
                      "backup": backup_dir if has_backup else None,
                      "schema": sc, "warnings": warnings}
    if restore_data and not has_backup:
        return False, "No pre-update backup found at %s." % backup_dir

    err = _try_begin("rollback", prev)
    if err:
        return False, err
    try:
        restored = []
        if restore_data:
            _job_update(step="backup", message="Saving current data before restoring...")
            safety = c.unique_dir(os.path.join(c.backups_dir(home), "rollback-from-%s-%s"
                                               % (cur, datetime.now().strftime("%Y%m%d-%H%M%S"))))
            os.makedirs(safety)
            c.copy_data_items(c.data_dir(home), safety, items=("config.yaml",), log=_job_log)
            if os.path.exists(paths.PRIVATE_DB_PATH):
                c.copy_sqlite(paths.PRIVATE_DB_PATH,
                              os.path.join(safety, "data_private", "private.db"))
            _job_update(step="restore", message="Restoring data from %s..." % backup_dir)
            restored = _restore_private(backup_dir)
        _job_update(step="switch", message="Switching to %s..." % prev)
        state = c.switch_current(home, prev)
        _refresh_home_files(home, prev_vdir, state)
        _job_update(step="restart", state="restarting",
                    message="Restarting FinanceApp %s..." % prev)
        # Delay so the HTTP response is sent before launch.py stops us.
        threading.Timer(RESTART_DELAY, _spawn_or_fail, args=(home, prev_vdir)).start()
    except Exception as e:
        _fail(str(e))
        return False, "Rollback failed: %s" % e
    return True, {"needs_restore": False, "version": prev, "restored": restored,
                  "warnings": warnings, "job": get_job()}


def _spawn_or_fail(home, vdir):
    try:
        spawn_restart(home, vdir)
        _job_update(finished_at=_now_iso())
    except Exception as e:
        _fail("Could not start the restart: %s. Restart FinanceApp manually." % e)


# --------------------------------------------------------------------------
# automatic checks
# --------------------------------------------------------------------------

def _auto_check_job():
    try:
        if get_settings()["auto_check"]:
            check()
    except Exception as e:
        print("[Updater] Auto-check error: %s" % e)


def start_auto_check():
    """Schedule a check ~30s after startup and every 24h (only runs when the
    auto_check setting is on: default on when installed, off in dev mode)."""
    global _auto_scheduler
    if _auto_scheduler is not None:
        return
    try:
        from datetime import timedelta
        from apscheduler.schedulers.background import BackgroundScheduler
        sched = BackgroundScheduler(daemon=True)
        sched.add_job(_auto_check_job, "interval", hours=CHECK_INTERVAL_HOURS,
                      next_run_time=datetime.now() + timedelta(seconds=STARTUP_CHECK_DELAY),
                      id="update_check", replace_existing=True,
                      max_instances=1, coalesce=True, misfire_grace_time=3600)
        sched.start()
        _auto_scheduler = sched
    except Exception as e:
        print("[Updater] Could not schedule update checks: %s" % e)


def shutdown():
    global _auto_scheduler
    if _auto_scheduler is not None:
        try:
            _auto_scheduler.shutdown(wait=False)
        except Exception:
            pass
        _auto_scheduler = None

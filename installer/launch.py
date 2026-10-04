#!/usr/bin/env python3
"""
FinanceApp launcher for an installed copy (standard library only).

Lives at <HOME>/launch.py (copied from the release's installer/launch.py on
install/update). Reads <HOME>/install.json, sets FINANCEAPP_HOME,
FINANCEAPP_PORT and FINANCEAPP_DEBUG=0, then runs the current version's
restart_server.py with that version's venv python.

Usage:
    python launch.py [--home DIR] <start|stop|restart|status|open> [--json] [--no-revert]

    start     Start the server if it is not running.
    stop      Stop the server.
    restart   Restart. If the new server fails its health check and install.json
              has a "previous" version that is still on disk, current/previous are
              swapped and the previous version is started (exit code 2 when that
              fallback succeeds). --no-revert disables this.
    status    Report status (--json passes through restart_server's JSON output).
    open      Start if needed, then open http://127.0.0.1:<port> in the browser.

When started without a console (pythonw.exe on Windows, the macOS .app),
output goes to <HOME>/run/launch.log and failures are shown in a dialog.
"""

import json
import os
import platform
import subprocess
import sys
import webbrowser
from datetime import datetime

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
COMMANDS = ("start", "stop", "restart", "status", "open")
EXIT_REVERTED = 2


def _headless():
    # FINANCEAPP_GUI=1 is set by the macOS .app / Linux .desktop runner, whose
    # stdout is redirected to run/launch.log (so it is not None).
    return sys.stdout is None or not hasattr(sys.stdout, "write") or \
        os.path.basename(sys.executable).lower() == "pythonw.exe" or \
        os.environ.get("FINANCEAPP_GUI") == "1"


class Out(object):
    """print() replacement that also works under pythonw (no stdout)."""

    def __init__(self, home):
        self.fh = None
        if _headless():
            try:
                os.makedirs(os.path.join(home, "run"), exist_ok=True)
                self.fh = open(os.path.join(home, "run", "launch.log"), "a", encoding="utf-8")
                self.fh.write("=== launch.py %s %s ===\n"
                              % (" ".join(sys.argv[1:]), datetime.now().isoformat(timespec="seconds")))
            except OSError:
                self.fh = None

    def __call__(self, msg=""):
        if self.fh:
            self.fh.write(msg + "\n")
            self.fh.flush()
        elif sys.stdout is not None:
            print(msg)
            sys.stdout.flush()


def load_state(home):
    with open(os.path.join(home, "install.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(home, state):
    path = os.path.join(home, "install.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    os.replace(path + ".tmp", path)


def venv_python(vdir):
    if IS_WINDOWS:
        p = os.path.join(vdir, "venv", "Scripts", "python.exe")
    else:
        p = os.path.join(vdir, "venv", "bin", "python3")
        if not os.path.exists(p):
            p = os.path.join(vdir, "venv", "bin", "python")
    return p if os.path.exists(p) else None


def run_server_cmd(home, state, cmd, extra, out):
    """Run versions/<current>/restart_server.py <cmd>; return its exit code."""
    version = state.get("current")
    vdir = os.path.join(home, "versions", str(version))
    script = os.path.join(vdir, "restart_server.py")
    if not version or not os.path.isfile(script):
        out("FinanceApp: version %r is not installed in %s (re-run the installer)."
            % (version, home))
        return 1
    # restart_server.py is stdlib-only, so any Python can run it; it starts
    # app.py with the version's own venv itself.
    py = venv_python(vdir) or sys.executable
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    env.pop("FINANCEAPP_GUI", None)  # the server (and launch.py it spawns) has no GUI role
    env.update({
        "FINANCEAPP_HOME": home,
        "FINANCEAPP_PORT": str(int(state.get("port") or 8765)),
        "FINANCEAPP_DEBUG": "0",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run([py, script, cmd] + list(extra), cwd=vdir, env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, **kwargs)
    except OSError as e:
        out("FinanceApp: could not run %s: %s" % (py, e))
        return 1
    text = proc.stdout.decode("utf-8", "replace").rstrip()
    if text:
        out(text)
    return proc.returncode


def try_revert(home, state, out):
    """After a failed restart: swap current/previous and start previous."""
    prev = state.get("previous")
    cur = state.get("current")
    if not prev or prev == cur or not os.path.isdir(os.path.join(home, "versions", prev)):
        return 1
    out("Version %s failed to start; reverting to %s." % (cur, prev))
    state["current"], state["previous"] = prev, cur
    state["last_failed"] = {"version": cur, "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    state["updated_at"] = state["last_failed"]["at"]
    save_state(home, state)
    rc = run_server_cmd(home, state, "restart", [], out)
    if rc == 0:
        out("Reverted: running %s." % prev)
        return EXIT_REVERTED
    out("Previous version %s also failed to start." % prev)
    return 1


def alert(message):
    """Best-effort GUI error for launches without a terminal."""
    try:
        if IS_MAC:
            subprocess.run(["osascript", "-e", 'display alert "FinanceApp" message %s'
                            % json.dumps(message)], timeout=120)
        elif IS_WINDOWS:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "FinanceApp", 0x10)
    except Exception:
        pass


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    home = os.path.dirname(os.path.abspath(__file__))
    if "--home" in args:
        i = args.index("--home")
        try:
            home = args[i + 1]
        except IndexError:
            print("--home needs a value")
            return 1
        del args[i:i + 2]
    home = os.path.abspath(os.path.expanduser(home))
    no_revert = "--no-revert" in args
    args = [a for a in args if a != "--no-revert"]
    cmd = args[0] if args else "open"
    extra = args[1:]
    out = Out(home)

    if cmd not in COMMANDS or (extra and (cmd != "status" or extra != ["--json"])):
        out("Usage: launch.py [--home DIR] <%s> [--json] [--no-revert]" % "|".join(COMMANDS))
        return 1
    try:
        state = load_state(home)
    except (OSError, ValueError) as e:
        out("FinanceApp is not installed in %s (%s)." % (home, e))
        if _headless():
            alert("FinanceApp is not installed correctly in %s. Please re-run the installer." % home)
        return 1

    if cmd == "restart":
        rc = run_server_cmd(home, state, "restart", [], out)
        if rc != 0 and not no_revert:
            rc = try_revert(home, state, out)
        return rc

    if cmd == "open":
        rc = run_server_cmd(home, state, "start", [], out)
        port = int(state.get("port") or 8765)
        if rc != 0:
            msg = ("FinanceApp could not start. See %s for details."
                   % os.path.join(home, "run", "server.log"))
            out(msg)
            if _headless():
                alert(msg)
            return rc
        url = "http://127.0.0.1:%d" % port
        out("Opening %s" % url)
        try:
            webbrowser.open(url)
        except Exception as e:
            out("Could not open a browser (%s); visit %s" % (e, url))
        return 0

    return run_server_cmd(home, state, cmd, extra, out)


if __name__ == "__main__":
    sys.exit(main())

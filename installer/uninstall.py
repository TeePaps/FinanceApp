#!/usr/bin/env python3
"""
FinanceApp uninstaller (standard library only).

Lives at <HOME>/uninstall.py; run via "Uninstall FinanceApp.command" (Mac),
the Start Menu / Apps & features entry (Windows), or directly:

    python uninstall.py [--yes] [--delete-data] [--purge] [--home DIR] [--backup-dir DIR]

Steps: stop the server -> back up data to ~/FinanceApp-data-backup-YYYYMMDD[-N]/
(unless --delete-data) -> remove launchers/shortcuts/registry entry -> delete
the install root. --purge also removes uv's cache and uv-managed Pythons, and
uv itself if it lives in ~/.local/bin.

Interactive (no --yes): asks for confirmation; data is kept by default and
deleting it requires typing DELETE. --backup-dir (or $FINANCEAPP_BACKUP_DIR)
overrides the backup location (used by tests).

Shared helpers come from installer/core.py of the installed version (or the
newest version dir that has one). Windows: the script re-runs itself from a
temp copy, with the base Python outside the install root, so nothing it
deletes is in use.
"""

import argparse
import datetime
import glob
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile

IS_WINDOWS = platform.system() == "Windows"
_HANDED_OFF = False


def log(msg=""):
    print(msg)
    sys.stdout.flush()


def parse_args(argv):
    p = argparse.ArgumentParser(description="Uninstall FinanceApp.")
    p.add_argument("--yes", "-y", action="store_true", help="do not ask questions")
    p.add_argument("--delete-data", action="store_true",
                   help="delete data instead of backing it up")
    p.add_argument("--purge", action="store_true",
                   help="also remove uv's cache, uv-managed Pythons, and uv in ~/.local/bin")
    p.add_argument("--home", help="install root (default: this script's directory)")
    p.add_argument("--backup-dir", help="where to put the data backup "
                   "(default: ~/FinanceApp-data-backup-YYYYMMDD[-N])")
    p.add_argument("--core", help=argparse.SUPPRESS)          # set on Windows re-exec
    p.add_argument("--from-temp", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args(argv)


def _inside(path, root):
    try:
        path = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(root))
        return path == root or path.startswith(root + os.sep)
    except (OSError, ValueError):
        return False


def find_core(home, explicit=None):
    """Path of an installer/core.py to use, or None."""
    if explicit and os.path.isfile(explicit):
        return explicit
    cands = []
    try:
        with open(os.path.join(home, "install.json"), "r", encoding="utf-8") as f:
            state = json.load(f)
        for key in ("current", "previous"):
            if state.get(key):
                cands.append(os.path.join(home, "versions", state[key], "installer", "core.py"))
    except (OSError, ValueError, AttributeError):
        pass
    cands += sorted(glob.glob(os.path.join(home, "versions", "*", "installer", "core.py")),
                    reverse=True)
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def load_core(path):
    spec = importlib.util.spec_from_file_location("financeapp_installer_core", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def outside_python(home):
    """A Python executable that does not live under ``home``."""
    for exe in (getattr(sys, "_base_executable", None), sys.executable):
        if exe and os.path.isfile(exe) and not _inside(exe, home):
            return exe
    cfg = os.path.join(sys.prefix, "pyvenv.cfg")
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition("=")
                if k.strip().lower() == "home":
                    exe = os.path.join(v.strip(), "python.exe")
                    if os.path.isfile(exe):
                        return exe
    except OSError:
        pass
    return None


def reexec_from_temp(args, home, core_path):
    """Windows: copy this script (+ core.py) to %TEMP% and run it there with a
    Python outside the install root. Returns an exit code, or None to continue
    in-process."""
    py = outside_python(home)
    if not py:
        log("Warning: no Python outside %s found; continuing in place." % home)
        return None
    tmp = tempfile.mkdtemp(prefix="financeapp-uninstall-")
    script = os.path.join(tmp, "uninstall.py")
    shutil.copy2(os.path.abspath(__file__), script)
    cmd = [py, script, "--from-temp", "--home", home]
    if core_path:
        shutil.copy2(core_path, os.path.join(tmp, "core.py"))
        cmd += ["--core", os.path.join(tmp, "core.py")]
    for flag in ("yes", "delete_data", "purge"):
        if getattr(args, flag):
            cmd.append("--" + flag.replace("_", "-"))
    if args.backup_dir:
        cmd += ["--backup-dir", args.backup_dir]
    if _inside(sys.executable, home):
        # Our own interpreter is in the tree being deleted: hand off and exit.
        subprocess.Popen(cmd, cwd=tmp)
        return 0
    return subprocess.call(cmd, cwd=tmp)


def ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        return ""


def default_backup_dir():
    base = os.path.join(os.path.expanduser("~"), "FinanceApp-data-backup-%s"
                        % datetime.date.today().strftime("%Y%m%d"))
    if not os.path.exists(base):
        return base
    n = 2
    while os.path.exists("%s-%d" % (base, n)):
        n += 1
    return "%s-%d" % (base, n)


def stop_server(home):
    launch = os.path.join(home, "launch.py")
    if not os.path.isfile(launch):
        return
    log("Stopping FinanceApp...")
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run([sys.executable, launch, "stop"], cwd=home, timeout=60,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, **kwargs)
    except (OSError, subprocess.SubprocessError) as e:
        log("Warning: could not stop the server: %s" % e)


def fallback_backup(home, dest):
    """Plain copy (server already stopped) when core.py is unavailable."""
    src = os.path.join(home, "data")
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*-wal", "*-shm", "*-journal"))
    if os.path.isfile(os.path.join(home, "install.json")):
        shutil.copy2(os.path.join(home, "install.json"), dest)
    return dest


def remove_tree(path):
    def onerror(func, p, exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=onerror)
    return not os.path.exists(path)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    here = os.path.dirname(os.path.abspath(__file__))
    home = os.path.abspath(os.path.expanduser(args.home or here))
    rc = run(args, home)
    if IS_WINDOWS and not _HANDED_OFF and not args.yes and sys.stdin and sys.stdin.isatty():
        ask("\nPress Enter to close...")
    return rc


def run(args, home):
    if not os.path.isfile(os.path.join(home, "install.json")):
        log("No FinanceApp install found in %s" % home)
        return 1
    core_path = find_core(home, args.core)

    if IS_WINDOWS and not args.from_temp:
        rc = reexec_from_temp(args, home, core_path)
        if rc is not None:
            global _HANDED_OFF
            _HANDED_OFF = True  # the temp copy did the work (and the prompts)
            return rc

    # Never keep our cwd inside the tree we delete.
    os.chdir(os.path.expanduser("~"))
    core = None
    if core_path:
        try:
            core = load_core(core_path)
        except Exception as e:  # broken install: carry on with fallbacks
            log("Warning: could not load %s: %s" % (core_path, e))

    delete_data = args.delete_data
    log("FinanceApp uninstaller")
    log("  Install folder: %s" % home)
    if not args.yes:
        if ask("Uninstall FinanceApp? [y/N] ").strip().lower() not in ("y", "yes"):
            log("Cancelled.")
            return 1
        if not delete_data:
            ans = ask("Your data (portfolio, settings) will be backed up to your home folder.\n"
                      "Press Enter to keep a backup, or type DELETE to delete it permanently: ")
            delete_data = ans.strip() == "DELETE"
        elif ask("--delete-data: type DELETE to confirm deleting your data: ").strip() != "DELETE":
            log("Cancelled.")
            return 1

    stop_server(home)

    backup = None
    if not delete_data and os.path.isdir(os.path.join(home, "data")):
        backup = os.path.abspath(os.path.expanduser(
            args.backup_dir or os.environ.get("FINANCEAPP_BACKUP_DIR") or default_backup_dir()))
        if os.path.exists(backup) and os.listdir(backup):
            log("Backup folder %s already exists and is not empty; aborting." % backup)
            return 1
        try:
            if core:
                core.backup_data(home, backup, log=log)
            else:
                fallback_backup(home, backup)
        except Exception as e:
            log("Backup failed (%s). Nothing was removed." % e)
            return 1
        log("Data backed up to %s" % backup)

    if core:
        try:
            core.remove_launchers(home, log=log)
        except Exception as e:
            log("Warning: could not remove launchers: %s" % e)
    else:
        log("Warning: installer/core.py not found; shortcuts may need manual removal.")

    log("Removing %s ..." % home)
    if not remove_tree(home):
        log("Warning: some files in %s could not be removed." % home)

    if args.purge:
        if core:
            core.purge_uv(log=log)
        else:
            log("Warning: --purge needs installer/core.py; skipped.")

    log("")
    log("FinanceApp has been uninstalled.")
    if backup:
        log("Your data is in %s" % backup)
        log("To restore it later, reinstall with: --restore-from \"%s\"" % backup)
    return 0


if __name__ == "__main__":
    sys.exit(main())

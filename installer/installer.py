#!/usr/bin/env python3
"""
FinanceApp installer (standard library only; runnable as a single file).

Normally started by a bootstrap (install.sh / install.ps1 / Install-FinanceApp.*)
as ``uv run --no-project --python 3.12 installer.py [args]``.

How it stays standalone: this file only knows how to obtain a release zip
(--zip, $FINANCEAPP_INSTALLER_ZIP, or the release feed / GitHub API) and verify
it against SHA256SUMS.txt. It then loads ``installer/core.py`` straight out of
that zip and does everything else with it, so the helpers used are always the
ones shipped with the version being installed.

Usage:
  installer.py [--home DIR] [--port N] [--version X.Y.Z] [--zip PATH]
               [--feed-url URL] [--import-from DIR] [--restore-from DIR]
               [--no-launch] [--no-shortcuts] [--yes]

Re-running on an existing install offers repair (same version) or upgrade /
downgrade; user data in <HOME>/data is never touched except by --import-from /
--restore-from, which first back it up to <HOME>/backups/pre-import-*.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import platform
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

GITHUB_API = "https://api.github.com/repos/TeePaps/FinanceApp"
IS_WINDOWS = platform.system() == "Windows"


def log(msg=""):
    print(msg)
    sys.stdout.flush()


def die(msg, code=1):
    log("")
    log("ERROR: %s" % msg)
    sys.exit(code)


# --------------------------------------------------------------------------
# Minimal release lookup (just enough to get the zip; core.py does the rest)
# --------------------------------------------------------------------------

_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:")  # 2+ char scheme; "C:\\" is a path


def _to_url(s):
    if _URL_RE.match(s):
        return s
    return pathlib.Path(os.path.abspath(os.path.expanduser(s))).as_uri()


def _get(url, timeout=120):
    req = urllib.request.Request(url, headers={
        "User-Agent": "FinanceApp-installer", "Accept": "application/vnd.github+json, */*"})
    return urllib.request.urlopen(req, timeout=timeout)


def _get_json(url):
    try:
        with _get(url, 30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        die("Could not read release info from %s: %s" % (url, e))


def _vkey(v):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(.*)$", v or "")
    if not m:
        return (0, 0, 0, 0, v or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
            0 if m.group(4) else 1, m.group(4))


def find_release_zip(feed_url, version):
    """Return (zip_url, sha256sums_url_or_None, version) for the wanted release."""
    if feed_url:
        base = _to_url(feed_url)
        data = _get_json(base)
        if isinstance(data, dict) and isinstance(data.get("releases"), list):
            data = data["releases"]
        rels = data if isinstance(data, list) else [data]
    elif version:
        base = None
        rels = [_get_json("%s/releases/tags/v%s" % (GITHUB_API, version.lstrip("v")))]
    else:
        base = None
        rels = [_get_json("%s/releases/latest" % GITHUB_API)]
    best = None
    for r in rels:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        v = (r.get("tag_name") or r.get("tag") or "").lstrip("vV") or r.get("version", "")
        if version and _vkey(v) != _vkey(version.lstrip("v")):
            continue
        if not version and r.get("prerelease"):
            continue
        if best is None or _vkey(v) > _vkey(best[0]):
            best = (v, r)
    if not best:
        die("No matching release found (%s)" % (feed_url or GITHUB_API))
    v, r = best
    assets = r.get("assets") or []
    if isinstance(assets, dict):
        assets = [{"name": k, "browser_download_url": u} for k, u in assets.items()]
    urls = {}
    for a in assets:
        u = a.get("browser_download_url") or a.get("url")
        if a.get("name") and u:
            if base and not _URL_RE.match(u):
                u = urllib.parse.urljoin(base, u)
            urls[a["name"]] = u
    zname = "FinanceApp-%s.zip" % v
    if zname not in urls:
        zname = next((n for n in urls if re.match(r"^FinanceApp-.+\.zip$", n)), None)
    if not zname:
        die("Release %s has no FinanceApp-*.zip asset" % v)
    return urls[zname], urls.get("SHA256SUMS.txt"), v


def fetch_zip(feed_url, version, workdir):
    zurl, sums_url, v = find_release_zip(feed_url, version)
    name = zurl.rstrip("/").rsplit("/", 1)[-1]
    if not name.endswith(".zip"):
        name = "FinanceApp-%s.zip" % v
    dest = os.path.join(workdir, name)
    log("Downloading FinanceApp %s ..." % v)
    try:
        with _get(zurl) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f, 1024 * 256)
    except Exception as e:
        die("Download failed: %s (%s)" % (zurl, e))
    if sums_url:
        with _get(sums_url, 30) as r:
            sums = r.read().decode("utf-8", "replace")
        expected = None
        for line in sums.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1].lstrip("*") == name:
                expected = parts[0].lower()
        h = hashlib.sha256()
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        if not expected or h.hexdigest() != expected:
            die("Checksum verification failed for %s" % name)
        log("Checksum OK.")
    else:
        log("Warning: no SHA256SUMS.txt in the release; checksum not verified.")
    return dest


def load_core_from_zip(zip_path, workdir):
    """Extract <top>/installer/core.py from the release zip and import it."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [n for n in z.namelist() if n.replace("\\", "/").endswith("/installer/core.py")
                     and n.replace("\\", "/").count("/") == 2]
            if not names:
                die("%s does not contain installer/core.py (release too old for this installer?)"
                    % os.path.basename(zip_path))
            path = os.path.join(workdir, "core.py")
            with open(path, "wb") as f:
                f.write(z.read(names[0]))
    except zipfile.BadZipFile as e:
        die("Not a valid zip: %s (%s)" % (zip_path, e))
    spec = importlib.util.spec_from_file_location("financeapp_installer_core", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# Interaction
# --------------------------------------------------------------------------

def ask_yes(prompt, default=True, assume=False):
    """Yes/no question. Reads the terminal even when stdin is a pipe
    (``curl | bash``); with no terminal (or --yes) returns the default."""
    if assume:
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        if IS_WINDOWS:
            ans = input(prompt + suffix)
        else:
            with open("/dev/tty", "r+") as tty:
                tty.write(prompt + suffix)
                tty.flush()
                ans = tty.readline()
    except (OSError, EOFError):
        return default
    ans = (ans or "").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes")


def parse_args(argv):
    p = argparse.ArgumentParser(prog="installer.py", description="Install FinanceApp.")
    p.add_argument("--home", help="install root (default ~/FinanceApp or %%LOCALAPPDATA%%\\FinanceApp)")
    p.add_argument("--port", type=int, help="port (default 8765, or the next free one)")
    p.add_argument("--version", help="install this version instead of the latest")
    p.add_argument("--zip", default=os.environ.get("FINANCEAPP_INSTALLER_ZIP") or None,
                   help="install from a local release zip (offline/testing); "
                        "default $FINANCEAPP_INSTALLER_ZIP")
    p.add_argument("--feed-url", default=os.environ.get("FINANCEAPP_UPDATE_FEED") or None,
                   help="release feed JSON (path or URL) instead of the GitHub API")
    p.add_argument("--import-from", help="copy data from a dev clone (data_public/, "
                                         "data_private/, config.yaml)")
    p.add_argument("--restore-from", help="restore an uninstaller backup folder")
    p.add_argument("--no-launch", action="store_true", help="do not start the app")
    p.add_argument("--no-shortcuts", action="store_true",
                   help="do not create the .app / shortcuts / Apps & features entry")
    p.add_argument("--yes", "-y", action="store_true", help="answer yes to all questions")
    a = p.parse_args(argv)
    if a.import_from and a.restore_from:
        p.error("use either --import-from or --restore-from, not both")
    return a


# --------------------------------------------------------------------------
# Main flow
# --------------------------------------------------------------------------

def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if sys.version_info < (3, 8):
        die("Python 3.8+ is required to run the installer.")
    work = tempfile.mkdtemp(prefix="financeapp-install-")
    try:
        return install(args, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def install(args, work):
    log("FinanceApp installer")
    log("")
    if args.zip:
        zip_path = os.path.abspath(os.path.expanduser(args.zip))
        if not os.path.isfile(zip_path):
            die("Zip not found: %s" % zip_path)
    else:
        zip_path = fetch_zip(args.feed_url, args.version, work)

    core = load_core_from_zip(zip_path, work)
    try:
        return _install(core, args, zip_path)
    except core.InstallError as e:
        die(str(e))


def _install(core, args, zip_path):
    version = core.zip_version(zip_path)
    if args.version and core.parse_version(args.version) != core.parse_version(version):
        raise core.InstallError("--version %s does not match the zip (%s)" % (args.version, version))
    home = os.path.abspath(os.path.expanduser(args.home or core.default_home()))
    state = core.load_state(home)
    log("Version:  %s" % version)
    log("Location: %s" % home)

    if state and state.get("current"):
        cur = state["current"]
        if cur == version:
            q = "FinanceApp %s is already installed. Repair it (rebuild code and Python environment)?" % cur
        elif core.is_newer(version, cur):
            q = "Upgrade FinanceApp %s -> %s?" % (cur, version)
        else:
            q = "Installed version %s is newer. Downgrade to %s?" % (cur, version)
        if args.import_from or args.restore_from:
            log("Your current data will be backed up to %s, then replaced."
                % os.path.join(core.backups_dir(home), "pre-import-*"))
        else:
            log("Your data in %s will not be touched." % core.data_dir(home))
        if not ask_yes(q, default=True, assume=args.yes):
            log("Nothing changed.")
            return 0
        mode = "repair" if cur == version else "upgrade"
    else:
        if os.path.isdir(home) and os.listdir(home) and not os.path.isfile(
                os.path.join(home, "install.json")):
            raise core.InstallError("%s exists and is not a FinanceApp install. Move it away "
                                    "or choose another --home." % home)
        mode = "install"

    # Stop a running copy before replacing its code / checking its port.
    if state:
        core.run_launcher(home, "stop", log=log)

    # Port: keep the saved one unless --port is given; pick the next free one.
    want = args.port or (state or {}).get("port") or core.DEFAULT_PORT
    port = core.free_port(want)
    if port != want:
        log("Port %d is in use; using %d." % (want, port))

    core.ensure_home_dirs(home)
    if state is None:
        state = core.new_state(port=port)
        state["current"] = None
        core.save_state(home, state)  # marks HOME as ours from now on
    state["port"] = port
    state.setdefault("python", core.DEFAULT_PYTHON)
    state.setdefault("extras", [])
    core.save_state(home, state)

    try:
        vdir = _build(core, args, home, state, version, zip_path, mode, port)
    except BaseException:
        if mode == "upgrade" and not args.no_launch:
            log("Install failed; restarting the existing version %s..." % state.get("current"))
            core.run_launcher(home, "start", log=log)
        raise
    return _finish(core, args, home, version, vdir, port)


def _build(core, args, home, state, version, zip_path, mode, port):
    log("")
    log("[1/5] Preparing uv and Python %s" % state["python"])
    uv = core.ensure_uv(log)
    log("  uv: %s" % uv)

    log("[2/5] Unpacking %s" % version)
    vdir = core.unpack_release(zip_path, home, log=log)

    log("[3/5] Building the Python environment")
    core.build_venv(home, vdir, state, log=log, uv=uv)
    core.smoke_test(home, vdir, log=log)

    log("[4/5] Setting up data")
    if args.import_from or args.restore_from:
        src = args.import_from or args.restore_from
        core.import_data(src, home, log=log)
        state = core.load_state(home)
    elif mode == "upgrade" and core.has_user_data(home):
        core.snapshot_private(home, version, log=log)
        core.prune_backups(home, log=log)

    state = core.switch_current(home, version)
    if mode == "install":
        state["installed_at"] = state.get("installed_at") or core.now_iso()
    state["port"] = port
    core.save_state(home, state)
    core.install_home_files(home, vdir, log=log, uv=uv)
    core.prune_versions(home, log=log)
    return vdir


def _finish(core, args, home, version, vdir, port):
    log("[5/5] Launchers")
    if args.no_shortcuts:
        log("  skipped (--no-shortcuts)")
    else:
        try:
            core.install_launchers(home, log=log)
        except Exception as e:  # shortcuts are a convenience; don't fail the install
            log("  Warning: could not create launchers: %s" % e)

    log("")
    log("FinanceApp %s is installed in %s" % (version, home))
    log("  URL:       http://127.0.0.1:%d" % port)
    log("  Start:     \"%s\" \"%s\" open" % (core.venv_python(vdir), os.path.join(home, "launch.py")))
    if core.IS_MAC and not args.no_shortcuts:
        log("  Launcher:  ~/Applications/FinanceApp.app")
    log("  Uninstall: \"%s\"" % os.path.join(
        home, "Uninstall FinanceApp.bat" if core.IS_WINDOWS else
        ("Uninstall FinanceApp.command" if core.IS_MAC else "uninstall-financeapp.sh")))

    if not args.no_launch:
        log("")
        log("Starting FinanceApp...")
        rc = core.run_launcher(home, "open", log=log, python=core.venv_python(vdir))
        if rc != 0:
            log("FinanceApp did not start; see %s"
                % os.path.join(core.run_dir(home), "server.log"))
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())

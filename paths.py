"""
Single source of truth for FinanceApp file locations.

Standard library only: restart_server.py imports this with system Python,
before any venv exists.

Two modes (see docs/installer-updater-design.md):
  - Dev mode (FINANCEAPP_HOME unset): everything lives in the repo, exactly
    as before - data_public/, data_private/, config.yaml, logs/.
  - Installed mode (FINANCEAPP_HOME set by launch.py): code lives in
    <HOME>/versions/<X.Y.Z>/ (CODE_DIR) and all mutable state lives under
    <HOME>/data and <HOME>/run, so it survives version switches.
"""

import os
import shutil
import sqlite3
from urllib.request import pathname2url

CODE_DIR = os.path.dirname(os.path.abspath(__file__))

_home = os.environ.get("FINANCEAPP_HOME", "").strip()
INSTALL_HOME = os.path.abspath(os.path.expanduser(_home)) if _home else None
IS_INSTALLED = bool(INSTALL_HOME)

if IS_INSTALLED:
    DATA_ROOT = os.path.join(INSTALL_HOME, "data")
    DATA_PUBLIC_DIR = os.path.join(DATA_ROOT, "data_public")
    DATA_PRIVATE_DIR = os.path.join(DATA_ROOT, "data_private")
    USER_CONFIG_FILE = os.path.join(DATA_ROOT, "config.yaml")
    RUN_DIR = os.path.join(INSTALL_HOME, "run")
    ARCHIVE_DIR = os.path.join(DATA_ROOT, "archive")
else:
    DATA_ROOT = CODE_DIR
    DATA_PUBLIC_DIR = os.path.join(CODE_DIR, "data_public")
    DATA_PRIVATE_DIR = os.path.join(CODE_DIR, "data_private")
    USER_CONFIG_FILE = os.path.join(CODE_DIR, "config.yaml")
    RUN_DIR = os.path.join(CODE_DIR, "logs")
    ARCHIVE_DIR = os.path.join(CODE_DIR, "archive")

DEFAULT_CONFIG_FILE = os.path.join(CODE_DIR, "config.defaults.yaml")
SEED_PUBLIC_DB = os.path.join(CODE_DIR, "data_public", "public.db")

PUBLIC_DB_PATH = os.path.join(DATA_PUBLIC_DIR, "public.db")
PRIVATE_DB_PATH = os.path.join(DATA_PRIVATE_DIR, "private.db")

# Dev keeps the historical 8080; installed mode normally gets FINANCEAPP_PORT
# from launch.py (install.json "port"), falling back to the installer default.
DEFAULT_PORT = 8765 if IS_INSTALLED else 8080


def default_port():
    try:
        return int(os.environ.get("FINANCEAPP_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


_dirs_ready = False


def ensure_dirs():
    """Create the data/run directories and seed public.db. Idempotent."""
    global _dirs_ready
    if _dirs_ready:
        return
    for d in (DATA_PUBLIC_DIR, RUN_DIR):
        os.makedirs(d, exist_ok=True)
    if not os.path.isdir(DATA_PRIVATE_DIR):
        os.makedirs(DATA_PRIVATE_DIR, mode=0o700, exist_ok=True)
    seed_public_db()
    _dirs_ready = True


def seed_public_db():
    """Copy the shipped public.db into DATA_PUBLIC_DIR if it has none.

    No-op in dev mode (seed and target are the same file). Uses SQLite's
    backup API so a seed that is in WAL mode is copied consistently; falls
    back to a plain file copy. Returns True if a copy was made.
    """
    if os.path.exists(PUBLIC_DB_PATH) or not os.path.isfile(SEED_PUBLIC_DB):
        return False
    if os.path.normcase(os.path.realpath(SEED_PUBLIC_DB)) == \
            os.path.normcase(os.path.realpath(PUBLIC_DB_PATH)):
        return False
    os.makedirs(DATA_PUBLIC_DIR, exist_ok=True)
    tmp = PUBLIC_DB_PATH + ".seed-tmp"
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
        src = sqlite3.connect("file:%s?mode=ro" % pathname2url(SEED_PUBLIC_DB),
                              uri=True)
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error:
        try:
            shutil.copyfile(SEED_PUBLIC_DB, tmp)
        except OSError:
            return False
    os.replace(tmp, PUBLIC_DB_PATH)
    print("[paths] Seeded %s from %s" % (PUBLIC_DB_PATH, SEED_PUBLIC_DB))
    return True

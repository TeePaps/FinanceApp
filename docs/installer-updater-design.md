# Installer / Updater / Uninstaller — Design Contract

All phases build against this contract. If you need to change it, update this file.

GitHub repo: `TeePaps/FinanceApp` (public). Releases are tagged `vX.Y.Z`.

## Modes

- **Dev mode** (git clone, e.g. `~/Apps/Claude/FinanceApp`): `FINANCEAPP_HOME` is unset.
  Everything behaves exactly as today: `data_public/`, `data_private/`, `config.yaml`, `logs/`
  inside the repo, port 8080, debug on. Self-update is disabled (UI says "dev mode — use git pull").
- **Installed mode**: `FINANCEAPP_HOME` is set to the install root by `launch.py`.
  Port from `install.json` (default 8765), `FINANCEAPP_DEBUG=0`.

## Install root layout

Mac: `~/FinanceApp`  ·  Windows: `%LOCALAPPDATA%\FinanceApp`

```
<HOME>/
  install.json            installer state (see below)
  launch.py               copied from repo installer/launch.py on install/update
  uninstall.py            copied from repo installer/uninstall.py on install/update
  versions/
    1.5.0/                unpacked release (code) — current
      venv/               that version's venv (built with uv)
    1.4.2/                previous (kept for rollback; at most current + 1 previous)
  data/
    data_public/          public.db, json caches
    data_private/         private.db, secrets
    config.yaml           user config (created from config.defaults.yaml)
  run/                    pid files + logs (server.log etc.)
  backups/
    pre-1.5.0/            private.db + config.yaml snapshot taken before switching to 1.5.0
  Uninstall FinanceApp.command   (Mac) / Uninstall FinanceApp.bat (Windows)
```

`install.json`:
```json
{
  "current": "1.5.0",
  "previous": "1.4.2",          // or null
  "port": 8765,
  "python": "3.12",
  "extras": [],                  // optional pip packages to reinstall each update, e.g. ["alpaca-py>=0.10.0"]
  "installed_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

## paths.py (repo root) — single source of truth for locations

```
CODE_DIR            dir containing paths.py
INSTALL_HOME        Path(FINANCEAPP_HOME) or None
IS_INSTALLED        bool(INSTALL_HOME)
DATA_PUBLIC_DIR     HOME/data/data_public   | CODE_DIR/data_public
DATA_PRIVATE_DIR    HOME/data/data_private  | CODE_DIR/data_private
USER_CONFIG_FILE    HOME/data/config.yaml   | CODE_DIR/config.yaml
DEFAULT_CONFIG_FILE CODE_DIR/config.defaults.yaml
RUN_DIR             HOME/run                | CODE_DIR/logs
SEED_PUBLIC_DB      CODE_DIR/data_public/public.db (copied only if DATA_PUBLIC_DIR has none)
```
Also provided (added in Phase 1): `DATA_ROOT` (HOME/data | CODE_DIR), `ARCHIVE_DIR`
(HOME/data/archive | CODE_DIR/archive, legacy migration archive), `PUBLIC_DB_PATH`,
`PRIVATE_DB_PATH`, `DEFAULT_PORT` (8765 installed | 8080 dev), `default_port()` (FINANCEAPP_PORT
or DEFAULT_PORT), `ensure_dirs()` (creates data/run dirs, data_private 0700, then
`seed_public_db()`; called from config.py and database.py), `seed_public_db()` (SQLite backup
API so a WAL-mode seed copies consistently; no-op when seed and target are the same file).
paths.py is stdlib-only; restart_server.py puts its own dir on sys.path and imports it.

restart_server.py in installed mode: `RUN_DIR/server.log` / `server.pid` with no port suffix
(one server per install; dev keeps `logs/server-<port>.*` for non-8080 ports). It passes
`FINANCEAPP_PORT` to the child explicitly. app.py defaults `FINANCEAPP_DEBUG` to 0 when installed.
`config.yaml` becomes untracked/gitignored; `config.defaults.yaml` is tracked. On load, if the
user config is missing it is created from defaults; missing keys are filled from defaults
(never overwrite existing user values).
Implemented in `user_config.ensure_user_config()` (ruamel round-trip, run once per process,
called by both config.py and services/providers/config.py before reading USER_CONFIG_FILE;
default comments copied for inserted keys where ruamel allows; never raises).
Caveat for existing dev clones: the commit that renames config.yaml deletes the tracked file,
so `git pull` removes a local config.yaml — copy it aside before pulling and restore after
(otherwise it is recreated from defaults and local customizations are lost).

`version.py`: `__version__ = "X.Y.Z"`. `/healthz` includes `"version"`.

Schema versioning: `PRAGMA user_version` on public.db and private.db, set by `database.py`
init/migrations. `database.SCHEMA_VERSION_PUBLIC` / `SCHEMA_VERSION_PRIVATE` constants.
A release's `min_schema` for rollback is the schema version its code expects.
Both start at 1. `_init_*_database()` stamps the version at the end (never lowers it; warns if
the file is newer than the code). On import, database.py runs `init_database()` if either file
is missing or its `user_version` is below the expected constant, so existing DBs get their
idempotent migrations and stamp on first start. `database.get_schema_version(path)` reads it.

## uv / Python / dependencies

- uv in its default location (`~/.local/bin/uv`, `%USERPROFILE%\.local\bin\uv.exe`), installed
  by the bootstrap if missing via the official installer.
- Python via `uv python install 3.12` (does not touch system Python).
- Per-version venv: `uv venv --python 3.12 <version>/venv` then
  `uv pip install --python <version>/venv -r <version>/requirements.txt` plus `install.json.extras`.
- Shared helpers live in repo `installer/core.py` (stdlib only), used by installer.py,
  the updater (`services/updater.py`) and uninstall.py:
  `find_uv()`, `ensure_uv()`, `default_home()`, `load_state(home)`, `save_state(home, state)`,
  `download(url, dest)`, `sha256(path)`, `fetch_latest_release(feed_url=None)`,
  `unpack_release(zip, home) -> version_dir`, `build_venv(home, version_dir, state)`,
  `smoke_test(home, version_dir)` (runs `venv python -c "import app"` with FINANCEAPP_HOME set),
  `switch_current(home, version)`, `prune_versions(home)`, `install_launchers(home)`,
  `free_port(start=8765)`.

## Release assets (built by GitHub Action on tag push)

- `FinanceApp-X.Y.Z.zip` — top-level folder `FinanceApp-X.Y.Z/`; excludes `data_private/`,
  `_ARCHIVE/`, `_IDEAS/`, `backup/`, `playwright-mcp/`, `venv/`, `logs/`, `requirements/`
  (the spec dir), `.github/`, `__pycache__/`. Includes `data_public/public.db` as seed.
- `SHA256SUMS.txt` — `sha256  filename` lines for every asset.
- `installer.py` (copy of repo `installer/installer.py`; bundles or downloads core.py — it must
  be runnable standalone, so it downloads the zip and imports core from the unpacked release,
  or inlines what it needs).
- `install.sh`, `install.ps1`, `Install-FinanceApp.command`, `Install-FinanceApp.bat`.

Latest release lookup: `GET https://api.github.com/repos/TeePaps/FinanceApp/releases/latest`
(skip drafts; pre-releases only on beta channel). Testing override: `--feed-url` /
`FINANCEAPP_UPDATE_FEED` pointing at a local JSON file of the same shape whose asset
`browser_download_url`s may be `file://` URLs.

## Entry points

- Mac one-liner: `curl -fsSL https://raw.githubusercontent.com/TeePaps/FinanceApp/main/install.sh | bash`
- Windows one-liner: `irm https://raw.githubusercontent.com/TeePaps/FinanceApp/main/install.ps1 | iex`
- README buttons → `https://github.com/TeePaps/FinanceApp/releases/latest/download/Install-FinanceApp.command` / `.bat`
- Bootstraps: ensure uv → download `installer.py` (latest release asset) →
  `uv run --python 3.12 installer.py [args]`.
- `installer.py` args: `--home`, `--port`, `--version`, `--zip PATH` (local zip, offline/testing),
  `--feed-url`, `--import-from PATH` (copy data_public/data_private/config.yaml from a dev clone),
  `--restore-from PATH` (uninstaller backup), `--no-launch`, `--no-shortcuts`.
- Launchers: Mac `~/Applications/FinanceApp.app` (minimal bundle whose executable runs
  `uv run --python 3.12 <HOME>/launch.py open`, or the current venv's python directly);
  Windows Desktop + Start Menu `.lnk` (created via PowerShell WScript.Shell), Start Menu
  "Uninstall FinanceApp", and HKCU `...\CurrentVersion\Uninstall\FinanceApp` entry (winreg).
- `launch.py <start|stop|restart|status|open>`: reads install.json, sets `FINANCEAPP_HOME`,
  `FINANCEAPP_PORT`, `FINANCEAPP_DEBUG=0`, runs `versions/<current>/venv` python on
  `versions/<current>/restart_server.py <cmd>`; `open` = start if not running, then open browser.
- No code signing. README documents Gatekeeper (right-click → Open) and SmartScreen
  (More info → Run anyway).

## Updater (installed mode)

`services/updater.py` + `routes/update.py` blueprint:
- `GET /api/update/status` → `{success, data: {mode, current, latest, available, notes, html_url,
  previous, checked_at, auto_check, channel, job: {state, step, message, error}}}`
- `POST /api/update/check` → force check.
- `POST /api/update/apply` → background job: download → verify sha256 → unpack to
  `versions/NEW` → build venv (requirements + extras) → smoke test → snapshot private.db +
  config.yaml to `backups/pre-NEW/` → update install.json (previous=old, current=new) →
  copy launch.py/uninstall.py → prune (keep current + previous; prune backups to last 2) →
  spawn detached `launch.py restart` → old process exits. If the new server fails health
  check, launch.py restart reverts install.json to previous and starts it.
- `POST /api/update/rollback` `{restore_data: bool}` → if previous's expected schema <
  current db `user_version` and not restore_data → return `{needs_restore: true}`;
  restore_data copies `backups/pre-<current>/` back. Then swap current/previous, restart.
- `POST /api/update/settings` `{auto_check, channel}` stored in user config.yaml `updates:` section.
- Auto-check at startup and every 24h via APScheduler; cache result in `run/update_check.json`.
- Dev mode: status returns `mode: "dev"`, apply/rollback refuse.

UI (`static/app.js`, templates): top banner when `available` (dismiss per-version in
localStorage), progress modal polling status then `/healthz` until version changes, reload.
Settings → "About & Updates": version, Check now, auto-check toggle, beta channel toggle,
previous version + Revert.

## Uninstaller

`uninstall.py [--yes] [--delete-data] [--purge]`: stop server on saved port → backup data to
`~/FinanceApp-data-backup-YYYYMMDD[-N]/` unless `--delete-data` (interactive: default keep,
typing DELETE required to delete) → remove versions/, launchers, shortcuts, registry entry,
install root. `--purge` also removes uv + its pythons. Windows: since uninstall.py may live in
the dir it deletes, re-exec from a temp copy.

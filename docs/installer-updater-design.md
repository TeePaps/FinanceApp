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
Optional keys added by Phase 3: `"launchers": [paths created by install_launchers]`,
`"last_failed": {"version", "at"}` (set by launch.py when a restart reverts). Written with
indent=2 (one `"current": "X"` per line — the Mac launcher scripts sed it).

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

  As implemented (Phase 3; all take an optional `log` callable, raise `core.InstallError`
  with a user-readable message, no import-time side effects, Python 3.8+ syntax):
  - `fetch_latest_release(feed_url=None, channel="stable", version=None)` → `{version, tag,
    notes, html_url, prerelease, draft, published_at, assets: {name: url}}`. Feed = arg, else
    `$FINANCEAPP_UPDATE_FEED`, else GitHub API (`/releases/latest`; beta → `/releases` list;
    `version=` → `/releases/tags/vX`). A feed may be a path or URL holding one release object,
    a list, or `{"releases": [...]}`; asset URLs may be `file://` or relative to the feed.
  - `download_release(release, dest_dir, log, require_checksum=False)` → zip path, verified
    against `SHA256SUMS.txt` when the release has one. Also `release_zip_name`, `sha256`,
    `verify_sha256`, `parse_sha256sums`, `parse_version`, `is_newer`, `zip_version`.
  - `unpack_release` extracts to a staging dir then renames (same version = repair: replaced,
    venv included). `build_venv(home, vdir, state, log, uv=None)` returns the venv python;
    uv is always called with `UV_PYTHON_PREFERENCE=only-managed`.
  - Phase 6: installer and updater no longer unpack straight into `versions/<X>`.
    `prepare_version(zip, home, state, log, uv)` = `stage_release` (unpack to
    `versions/.staging-<X>`, after `clean_staging` removes stale `.staging-*`/`.trash-*`) →
    `build_venv` (`uv venv --relocatable`, falls back to a plain venv on old uv) → `smoke_test`
    (no .pyc written) → `promote_staged` (existing `versions/<X>` moved to `.trash-*`, staging
    renamed in, trash put back if that fails). Any failure removes the staging dir and leaves
    `versions/` unchanged, so a failed repair or a re-apply of the version kept as "previous"
    never breaks it. The updater calls the same three steps itself (to report progress).
    `switch_current` clears `install.json.last_failed`.
  - `smoke_test(home, vdir, isolated=True)`: by default FINANCEAPP_HOME is a throwaway temp
    dir, so importing the new code cannot migrate the real DBs before the pre-update snapshot.
  - Data: `backup_data(home, dest)` (full data copy + install.json + README.txt, dev-clone
    layout), `snapshot_private(home, version)` → `backups/pre-<version>/`, `prune_backups(home,
    keep=2)`, `import_data(src, home)` (used by --import-from and --restore-from: backs current
    data up to `backups/pre-import-<ts>/`, then replaces each provided item; merges backup
    install.json `extras`). SQLite files are always copied with the backup API; `-wal/-shm/
    -journal/.lock` files are skipped.
  - `install_home_files(home, vdir)` copies launch.py + uninstall.py into HOME and writes the
    "Uninstall FinanceApp" script. **Phase 4: the updater should call `install_home_files` and
    `install_launchers` after switching** (refreshes Windows shortcut targets and the registry
    DisplayVersion). `run_launcher(home, cmd)`, `health(port)`, `port_in_use(port)`,
    `remove_launchers(home)`, `purge_uv()`, `base_python(vdir, windowed=False)`.

## Release assets (built by GitHub Action on tag push)

- `FinanceApp-X.Y.Z.zip` — top-level folder `FinanceApp-X.Y.Z/`; excludes `data_private/`,
  `_ARCHIVE/`, `_IDEAS/`, `backup/`, `playwright-mcp/`, `venv/`, `logs/`, `requirements/`
  (the spec dir), `.github/`, `.claude/`, `__pycache__/`. Includes `data_public/public.db` as seed.
  Built by `scripts/build_release.py VERSION [--out dist/] [--ref HEAD] [--installer-dir DIR]`
  from the committed tree via `git archive` (tracked files only; the seed is the committed
  public.db blob, not the skip-worktree working copy; uncommitted edits are not shipped).
  Fails unless `version.py` at the ref equals VERSION. Installer assets are copied from
  `installer/` (missing ones skipped with a warning). `scripts/release.py X.Y.Z [--dry-run]
  [--push]` bumps version.py, commits "Release vX.Y.Z", tags; `.github/workflows/release.yml`
  publishes on `v*.*.*` tag push (`prerelease` when the version contains `-`).
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

- Mac one-liner: `curl -fsSL https://github.com/TeePaps/FinanceApp/releases/latest/download/install.sh | bash`
- Windows one-liner: `irm https://github.com/TeePaps/FinanceApp/releases/latest/download/install.ps1 | iex`
  (Phase 3 change: the bootstraps live in `installer/`, not the repo root, so the one-liners
  use the release assets, which also keeps bootstrap and installer.py from the same release.)
- README buttons → `https://github.com/TeePaps/FinanceApp/releases/latest/download/Install-FinanceApp-mac.zip` / `Install-FinanceApp.bat`
- Bootstraps: ensure uv → download `installer.py` (latest release asset) →
  `uv run --python 3.12 installer.py [args]`.
- `installer.py` args: `--home`, `--port`, `--version`, `--zip PATH` (local zip, offline/testing),
  `--feed-url`, `--import-from PATH` (copy data_public/data_private/config.yaml from a dev clone),
  `--restore-from PATH` (uninstaller backup), `--no-launch`, `--no-shortcuts`, plus `--yes`
  (no prompts; prompts read `/dev/tty` so `curl | bash` still asks; no terminal = defaults).
  Defaults: `--zip` ← `$FINANCEAPP_INSTALLER_ZIP`, `--feed-url` ← `$FINANCEAPP_UPDATE_FEED`.
  Standalone strategy: installer.py inlines only "find the release zip + verify SHA256SUMS",
  then imports `installer/core.py` extracted from that zip, so the helpers always match the
  version being installed. Re-run on an install: same version → repair (re-unpack + rebuild
  venv), other version → upgrade/downgrade (snapshot `backups/pre-X/`); data never touched
  except by --import-from/--restore-from. Saved port kept unless `--port`; a busy port moves
  to the next free one. If an upgrade fails after stopping the server, the old one is restarted.
- Bootstrap env overrides (offline tests): `FINANCEAPP_INSTALLER_PY` (local installer.py),
  `FINANCEAPP_INSTALLER_URL`, `FINANCEAPP_INSTALLER_ZIP`, `FINANCEAPP_UPDATE_FEED`,
  `FINANCEAPP_UV`; `Install-FinanceApp.command`/`.bat` run the `install.sh`/`install.ps1` next
  to them if present, else download the release asset (`FINANCEAPP_INSTALL_SH_URL` /
  `FINANCEAPP_INSTALL_PS1_URL`); `FINANCEAPP_NO_PAUSE=1` skips the final pause. With
  `irm | iex`, pass installer args via `$env:FINANCEAPP_INSTALL_ARGS`. uv is installed with
  `UV_NO_MODIFY_PATH=1` (always called by absolute path; shell profiles untouched).
- A `.command` downloaded by a browser loses its execute bit, so `build_release.py` also
  produces `Install-FinanceApp-mac.zip` (the .command stored with mode 0755; listed in
  SHA256SUMS) and the README macOS button links that zip. The bare `.command` stays an asset.
- Launchers: Mac `~/Applications/FinanceApp.app` (minimal bundle whose executable runs
  `uv run --python 3.12 <HOME>/launch.py open`, or the current venv's python directly);
  Windows Desktop + Start Menu `.lnk` (created via PowerShell WScript.Shell), Start Menu
  "Uninstall FinanceApp", and HKCU `...\CurrentVersion\Uninstall\FinanceApp` entry (winreg).
- `launch.py <start|stop|restart|status|open>`: reads install.json, sets `FINANCEAPP_HOME`,
  `FINANCEAPP_PORT`, `FINANCEAPP_DEBUG=0`, runs `versions/<current>/venv` python on
  `versions/<current>/restart_server.py <cmd>`; `open` = start if not running, then open browser.
  Details: HOME = the script's own dir (or `--home`); `status --json` passes through; `restart`
  failure with a `previous` dir on disk swaps current/previous, records
  `install.json.last_failed = {version, at}`, starts previous and exits **2** (`--no-revert`
  disables). Without a console (pythonw / .app) output goes to `run/launch.log` and errors
  show a dialog (the .app/.desktop runner sets `FINANCEAPP_GUI=1` for this, since its stdout is
  redirected rather than missing; launch.py drops it from the server's environment).
- Launcher implementation: the Mac `.app` executable (and the "Uninstall FinanceApp.command")
  is a bash script that reads `current` from install.json at run time (sed) and runs that
  venv's python, falling back to `uv run`, so it survives updates without rewriting. Windows
  `.lnk`s target the uv-managed base `pythonw.exe` (from the venv's `pyvenv.cfg`) with
  `"<HOME>\launch.py" open`, so they don't depend on a version dir; "Uninstall FinanceApp.bat"
  uses the base `python.exe`. Created paths are recorded in `install.json.launchers`;
  removal only touches a `.app`/`.desktop`/registry entry that points at this HOME. Linux:
  `~/.local/share/applications/financeapp.desktop`. `--no-shortcuts` skips all of these
  (the HOME uninstall script is always written).
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

As implemented (Phases 4–5):
- `services/updater.py` loads `installer/core.py` from `paths.CODE_DIR` by path and only
  orchestrates core helpers. Mode is `"installed"` only when `FINANCEAPP_HOME` is set, CODE_DIR
  is `<HOME>/versions/<X>` (not a git checkout) and install.json exists; otherwise `"dev"`
  (check works, apply/rollback return `success:false` with a "use git pull" message).
- Status also returns `installed_current`, `prerelease`, `published_at`, `check_error`,
  `previous_newer` (after a revert the "previous" is newer; UI says "Switch back to"),
  `last_failed` (from launch.py), `can_update`, `dev_message`, `home`, `log_file`; `job` also has
  `action` (update|rollback), `version`, `from_version`, `started_at`, `finished_at`, `log`.
  Job states: idle | running | restarting | error. One job at a time (409 otherwise).
  `POST /api/update/apply` accepts optional `{version}` (default: cached latest); 202 on start.
- Cache `RUN_DIR/update_check.json` (dev: `logs/`) holds the last result per channel; a
  failed check keeps the last good `latest` and sets `error`. Changing channel drops the cache.
- Checksums: `SHA256SUMS.txt` is required for GitHub releases; with `$FINANCEAPP_UPDATE_FEED`
  set it is verified when present (test feeds may omit it).
- Apply failure before the switch removes the half-built version dir (unless it is the
  current/previous); a failure after the switch restores the original install.json.
  `install_home_files` always runs after a switch; `install_launchers` only when install.json
  has a non-empty `launchers` list (so `--no-shortcuts` installs never get `~/Applications`
  or Start Menu entries from an update). Failures there are warnings.
- Restart: `<new current venv python> <HOME>/launch.py restart`, spawned after install.json is
  final; macOS/Linux `start_new_session=True`, Windows `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|
  CREATE_NO_WINDOW` via a short-lived trampoline (restart_server's `taskkill /T` would otherwise
  kill launch.py as part of this server's process tree). Output → `run/update-restart.log`.
  The old server does not exit by itself: launch.py's stop (pid file) ends it. If it is still
  alive 300 s later the job turns into an error so the UI can say so. Rollback spawns after a
  1 s delay so its HTTP response is sent first. Env passes through (FINANCEAPP_UPDATE_FEED
  reaches the new server; launch.py/restart_server already copy os.environ).
  `restart_server.py`'s Unix port fallback now uses `lsof -ti tcp:PORT -sTCP:LISTEN`; a bare
  `:PORT` also matched clients and could kill the browser polling during the restart.
- Rollback schema check: previous version's expected schema = `SCHEMA_VERSION_PUBLIC/PRIVATE`
  regex-read from `versions/<prev>/database.py` (missing → warning, treated as compatible).
  Only a newer **private.db** triggers `needs_restore` (`backup` = `backups/pre-<current>` or
  null if missing → UI refuses). A newer public.db (rebuildable) is a warning only.
  `restore_data` first saves current private.db + config.yaml to
  `backups/rollback-from-<cur>-<ts>/`, then restores private.db with the SQLite backup API
  *into* the live file (safe while open, works on Windows) and replaces config.yaml.
- Settings: `updates: {auto_check: null, channel: stable}` in config.defaults.yaml;
  `auto_check: null` = on when installed, off in dev. Written with ruamel round-trip; the
  providers config cache is refreshed so a later provider save keeps the section.
- Auto-check: a separate APScheduler BackgroundScheduler in updater (the price scheduler can
  be disabled/paused by the user, so it is not reused): first run 30 s after start, then every
  24 h; each run re-reads `auto_check`, so toggling needs no restart. Started from app.py's
  serving-process block only (not under the test client / `import app`).
- UI: banner hidden in dev mode unless the user clicked Check now (then it says to use git
  pull, no Update button). Progress modal polls status every 1 s; once status is unreachable
  or the job is `restarting` it polls `/healthz` every 2 s for up to 3 min, reloads on a new
  version, reports a launch.py revert (`last_failed`) or a timeout pointing at
  `run/server.log` / `update-restart.log`.

## Uninstaller

`uninstall.py [--yes] [--delete-data] [--purge]`: stop server on saved port → backup data to
`~/FinanceApp-data-backup-YYYYMMDD[-N]/` unless `--delete-data` (interactive: default keep,
typing DELETE required to delete) → remove versions/, launchers, shortcuts, registry entry,
install root. `--purge` also removes uv + its pythons. Windows: since uninstall.py may live in
the dir it deletes, re-exec from a temp copy.

As implemented: also `--home DIR` (default: the script's dir) and `--backup-dir DIR` /
`$FINANCEAPP_BACKUP_DIR` (tests). The backup is `core.backup_data` (data_public/,
data_private/, config.yaml, archive/, install.json, README.txt), private.db is
`quick_check`ed, and nothing is removed if the backup fails or the target is non-empty.
core.py is loaded from `versions/<current|previous|any>/installer/core.py`; without it the
uninstaller still backs up (plain copy) and deletes, but warns that shortcuts may remain.
Windows re-exec copies uninstall.py + core.py to `%TEMP%` and runs them with a Python outside
HOME (waits if the parent is outside HOME, otherwise hands off and exits). `--purge` runs
`uv cache clean` + `uv python uninstall --all` (all uv-managed Pythons) and deletes uv/uvx only
if they are in `~/.local/bin` (a Homebrew/pip uv is left alone).

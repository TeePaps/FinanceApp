"""
Backup & export routes blueprint.

Endpoints:
- GET /api/backup/info                       - data locations and file sizes
- GET /api/backup/download?include_secrets=1&include_market=0
                                             - zip of the user's data (see _README)
- GET /api/backup/export/<transactions|stocks>.csv
                                             - one private table as CSV
- POST /api/backup/inspect                   - describe an uploaded backup zip
                                               (changes nothing)
- POST /api/backup/restore                   - restore an uploaded backup zip
                                               (form: restore_config=1,
                                               restore_secrets=1, restore_market=0)

The zip uses the same layout as an installer/uninstaller backup folder
(data_private/, data_public/, config.yaml), so once unzipped it can be passed
to ``installer.py --restore-from``.
"""

import csv
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import zipfile
import zlib
from datetime import datetime

from flask import Blueprint, Response, jsonify, request

import paths
from version import __version__
from services.providers.secrets import SECRETS_FILE

backup_bp = Blueprint('backup', __name__, url_prefix='/api/backup')

CSV_TABLES = ('stocks', 'transactions')


def _ok(data, code=200):
    return jsonify({'success': True, 'data': data}), code


def _err(message, code=400):
    return jsonify({'success': False, 'error': message}), code


def _flag(name, default):
    value = request.args.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _size(path):
    try:
        return os.path.getsize(path) if os.path.isfile(path) else None
    except OSError:
        return None


def _snapshot_sqlite(src, dst):
    """Consistent copy of a live (WAL-mode) SQLite db via the backup API."""
    # A normal (not mode=ro) connection: as the last connection to close it
    # can clean up the -wal/-shm sidecars instead of leaving them behind.
    s = sqlite3.connect(src, timeout=30.0)
    try:
        d = sqlite3.connect(dst)
        try:
            s.backup(d)
            # Self-contained file: no -wal sidecar needed to read it.
            d.execute('PRAGMA journal_mode=DELETE')
        finally:
            d.close()
    finally:
        s.close()


def _table_csv(db_path, table):
    """CSV text (header + all columns, all rows) for a private-db table."""
    if table not in CSV_TABLES:
        raise ValueError('Unknown table: %s' % table)
    con = sqlite3.connect(db_path, timeout=30.0)
    try:
        cur = con.execute('SELECT * FROM %s ORDER BY %s'
                          % (table, 'ticker' if table == 'stocks' else 'id'))
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([c[0] for c in cur.description])
        writer.writerows(cur.fetchall())
        return buf.getvalue()
    finally:
        con.close()


def _readme(created, contents, include_secrets):
    lines = [
        'FinanceApp backup',
        '=================',
        '',
        'App version: %s' % __version__,
        'Created:     %s' % created,
        'Made from:   %s copy (%s)' % ('installed' if paths.IS_INSTALLED else 'development',
                                       paths.DATA_ROOT),
        '',
        'Contents',
        '--------',
    ]
    lines += ['  %-28s %s' % (name, desc) for name, desc in contents]
    if not include_secrets:
        lines += ['', 'API keys (secrets.json) were NOT included in this backup.']
    lines += [
        '',
        'The csv/ files are a readable copy of your stocks and transactions for',
        'spreadsheets; they are not needed to restore.',
        '',
        'How to restore',
        '--------------',
        'First unzip this file. The unzipped folder has the same layout as a',
        'FinanceApp data folder (data_private/, data_public/, config.yaml).',
        '',
        'Installed copy (macOS / Windows):',
        '  1. Quit FinanceApp.',
        '  2. Run the installer again with --restore-from pointing at the unzipped',
        '     folder, e.g. on macOS:',
        '       curl -fsSL https://github.com/TeePaps/FinanceApp/releases/latest/download/install.sh \\',
        '         | bash -s -- --restore-from "/path/to/unzipped-folder"',
        '     (or run installer.py --restore-from "/path/to/unzipped-folder").',
        '  The installer first saves your current data to <install>/backups/pre-import-*,',
        '  then replaces each item present in the backup (data_private/ is replaced as',
        '  a whole folder, so a backup without secrets.json also drops saved API keys;',
        '  data_public/ is only replaced if it is in the backup).',
        '',
        'Development copy (git checkout):',
        '  1. Stop the server (python3 restart_server.py stop, or Ctrl+C).',
        '  2. Copy data_private/private.db (and data_private/secrets.json if present)',
        '     into the checkout\'s data_private/ folder, and config.yaml into the',
        '     checkout root. Delete any old private.db-wal / private.db-shm files there.',
        '  3. Optionally copy data_public/public.db into data_public/ (it can also be',
        '     rebuilt by running the screener).',
        '  4. Start the server again (python3 restart_server.py restart).',
        '',
    ]
    return '\n'.join(lines)


def _build_backup(tmpdir, include_secrets, include_market):
    """Write the backup zip into ``tmpdir``; returns (zip_path, download_name)."""
    now = datetime.now()
    name = 'FinanceApp-backup-%s-%s.zip' % (__version__, now.strftime('%Y%m%d-%H%M%S'))
    zip_path = os.path.join(tmpdir, name)

    if not os.path.isfile(paths.PRIVATE_DB_PATH):
        raise FileNotFoundError('No private database found at %s' % paths.PRIVATE_DB_PATH)

    priv_snap = os.path.join(tmpdir, 'private.db')
    _snapshot_sqlite(paths.PRIVATE_DB_PATH, priv_snap)

    contents = [('data_private/private.db', 'Your stocks and transactions (SQLite)')]
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(priv_snap, 'data_private/private.db')

        if include_secrets and os.path.isfile(SECRETS_FILE):
            zf.write(SECRETS_FILE, 'data_private/secrets.json')
            contents.append(('data_private/secrets.json', 'API keys (keep this file private)'))

        if os.path.isfile(paths.USER_CONFIG_FILE):
            zf.write(paths.USER_CONFIG_FILE, 'config.yaml')
            contents.append(('config.yaml', 'Your settings'))

        if include_market and os.path.isfile(paths.PUBLIC_DB_PATH):
            pub_snap = os.path.join(tmpdir, 'public.db')
            _snapshot_sqlite(paths.PUBLIC_DB_PATH, pub_snap)
            zf.write(pub_snap, 'data_public/public.db')
            contents.append(('data_public/public.db', 'Market data (rebuildable)'))

        for table in CSV_TABLES:
            arc = 'csv/%s.csv' % table
            zf.writestr(arc, _table_csv(priv_snap, table))
            contents.append((arc, 'All %s as CSV' % table))

        contents.append(('README.txt', 'This file'))
        zf.writestr('README.txt', _readme(now.isoformat(timespec='seconds'),
                                          contents, include_secrets))
    return zip_path, name


@backup_bp.route('/info', methods=['GET'])
def backup_info():
    try:
        return _ok({
            'version': __version__,
            'mode': 'installed' if paths.IS_INSTALLED else 'dev',
            'data_dir': paths.DATA_ROOT,
            'files': {
                'private_db': {'path': paths.PRIVATE_DB_PATH, 'size': _size(paths.PRIVATE_DB_PATH)},
                'public_db': {'path': paths.PUBLIC_DB_PATH, 'size': _size(paths.PUBLIC_DB_PATH)},
                'config': {'path': paths.USER_CONFIG_FILE, 'size': _size(paths.USER_CONFIG_FILE)},
                'secrets': {'path': SECRETS_FILE, 'size': _size(SECRETS_FILE)},
            },
        })
    except Exception as e:
        return _err('Could not read backup info: %s' % e, 500)


@backup_bp.route('/download', methods=['GET'])
def backup_download():
    include_secrets = _flag('include_secrets', True)
    include_market = _flag('include_market', False)
    tmpdir = tempfile.mkdtemp(prefix='financeapp-backup-')
    try:
        zip_path, name = _build_backup(tmpdir, include_secrets, include_market)
        size = os.path.getsize(zip_path)
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return _err('Backup failed: %s' % e, 500)

    def stream():
        # The temp dir goes away when streaming ends, fails or is abandoned
        # (the WSGI server closes the generator, which runs the finally).
        try:
            with open(zip_path, 'rb') as f:
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    return Response(stream(), mimetype='application/zip', direct_passthrough=True,
                    headers={'Content-Disposition': 'attachment; filename="%s"' % name,
                             'Content-Length': str(size),
                             'Cache-Control': 'no-store'})


@backup_bp.route('/export/<table>.csv', methods=['GET'])
def export_csv(table):
    if table not in CSV_TABLES:
        return _err('Unknown export: %s (use stocks or transactions)' % table, 404)
    try:
        if not os.path.isfile(paths.PRIVATE_DB_PATH):
            return _err('No private database found', 404)
        text = _table_csv(paths.PRIVATE_DB_PATH, table)
    except Exception as e:
        return _err('Export failed: %s' % e, 500)
    name = 'FinanceApp-%s-%s.csv' % (table, datetime.now().strftime('%Y%m%d'))
    return Response(text, mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="%s"' % name,
                             'Cache-Control': 'no-store'})


# ---------------------------------------------------------------------------
# Import / restore
# ---------------------------------------------------------------------------

class RestoreError(Exception):
    """A backup that cannot be restored; the message is shown to the user."""


_MB = 1024 * 1024
MAX_UPLOAD_BYTES = 4096 * _MB
MAX_ZIP_ENTRIES = 10000
# Members a restore reads (path inside the backup -> max uncompressed size).
# Nothing else in the zip is ever extracted.
RESTORE_MEMBERS = {
    'data_private/private.db': 1024 * _MB,
    'data_private/secrets.json': 1 * _MB,
    'config.yaml': 5 * _MB,
    'data_public/public.db': 4096 * _MB,
    'README.txt': 1 * _MB,
}
PRIVATE_TABLES = ('stocks', 'transactions')
PUBLIC_TABLES = ('tickers', 'valuations')

_restore_lock = threading.Lock()


def _form_flag(name, default):
    value = request.form.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _core():
    from services.updater import core
    return core()


def _save_upload(tmpdir):
    """Stream the uploaded file to ``tmpdir``; returns its path."""
    upload = request.files.get('file')
    if upload is None or not upload.filename:
        raise RestoreError('No backup file was uploaded.')
    dest = os.path.join(tmpdir, 'upload.zip')
    total = 0
    with open(dest, 'wb') as out:
        while True:
            chunk = upload.stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise RestoreError('The uploaded file is too large to be a FinanceApp backup.')
            out.write(chunk)
    return dest


def _unsafe_name(name):
    if name.startswith('/') or re.match(r'^[A-Za-z]:', name):
        return True
    return any(part == '..' for part in name.split('/'))


def _scan_zip(zf):
    """Validate every entry name; return {backup-relative path: ZipInfo} for
    the members a restore uses (handles one top-level wrapper folder)."""
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise RestoreError('The zip has too many entries to be a FinanceApp backup.')
    files = {}
    for info in infos:
        name = info.filename.replace('\\', '/')
        if _unsafe_name(name):
            raise RestoreError('Unsafe path in zip (%s); refusing to use it.' % info.filename)
        if name.endswith('/') or name.startswith('__MACOSX/') or '/__MACOSX/' in name:
            continue
        files[name] = info

    prefix = None
    if 'data_private/private.db' in files:
        prefix = ''
    else:
        tops = {n.split('/', 1)[0] for n in files if '/' in n}
        loose = [n for n in files if '/' not in n and os.path.basename(n) != '.DS_Store']
        if len(tops) == 1 and not loose:
            top = tops.pop() + '/'
            if top + 'data_private/private.db' in files:
                prefix = top
    if prefix is None:
        raise RestoreError('This zip does not look like a FinanceApp backup '
                           '(no data_private/private.db inside).')

    members = {}
    for rel, limit in RESTORE_MEMBERS.items():
        info = files.get(prefix + rel)
        if info is None:
            continue
        if info.file_size > limit:
            raise RestoreError('%s in the backup is unexpectedly large (%s); refusing to use it.'
                               % (rel, formatted_size(info.file_size)))
        members[rel] = info
    return members


def formatted_size(n):
    return '%.1f MB' % (n / float(_MB)) if n >= _MB else '%d bytes' % n


def _extract(zf, info, dest, limit):
    """Copy one member to ``dest`` (never more than ``limit`` bytes)."""
    total = 0
    with zf.open(info) as src, open(dest, 'wb') as out:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise RestoreError('%s in the backup is larger than allowed.' % info.filename)
            out.write(chunk)
    return dest


def _check_backup_db(path, label, tables, max_version):
    """Validate an extracted SQLite file. Returns {schema_version, counts}."""
    with open(path, 'rb') as f:
        if f.read(16) != b'SQLite format 3\x00':
            raise RestoreError('%s in the backup is not a SQLite database.' % label)
    try:
        con = sqlite3.connect(path)
        try:
            # Our own temp copy: make it self-contained (no -wal needed) so it
            # can be read and restored from consistently.
            con.execute('PRAGMA journal_mode=DELETE')
            rows = con.execute('PRAGMA integrity_check').fetchall()
            if not rows or rows[0][0] != 'ok':
                detail = '; '.join(str(r[0]) for r in rows[:3])
                raise RestoreError('%s in the backup is damaged (integrity check: %s).'
                                   % (label, detail))
            have = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            missing = [t for t in tables if t not in have]
            if missing:
                raise RestoreError('%s in the backup is missing table(s): %s.'
                                   % (label, ', '.join(missing)))
            version = con.execute('PRAGMA user_version').fetchone()[0]
            counts = {t: con.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0]
                      for t in tables}
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        raise RestoreError('%s in the backup could not be read: %s' % (label, e))
    if version > max_version:
        raise RestoreError(
            'This backup is from a newer version of FinanceApp (%s schema %d; this version '
            'supports %d). Update the app first, then restore.' % (label, version, max_version))
    return {'schema_version': version, 'counts': counts}


def _parse_readme(text):
    out = {}
    for key, field in (('app_version', 'App version'), ('created', 'Created')):
        m = re.search(r'^%s:\s*(.+?)\s*$' % re.escape(field), text, re.M)
        if m:
            out[key] = m.group(1)
    return out


def _load_backup(zip_path, workdir, want_market):
    """Open + validate the backup; extract what a restore needs into
    ``workdir``. Raises RestoreError. Returns a description dict."""
    import database
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise RestoreError('Not a valid zip file (%s).' % e)
    try:
        members = _scan_zip(zf)
        out = {'contents': {
            'private_db': True,
            'secrets': 'data_private/secrets.json' in members,
            'config': 'config.yaml' in members,
            'public_db': 'data_public/public.db' in members,
        }, 'files': {}}
        try:
            if 'README.txt' in members:
                text = zf.read(members['README.txt']).decode('utf-8', 'replace')
                out.update(_parse_readme(text))

            priv = _extract(zf, members['data_private/private.db'],
                            os.path.join(workdir, 'private.db'),
                            RESTORE_MEMBERS['data_private/private.db'])
            out['private'] = _check_backup_db(priv, 'private.db', PRIVATE_TABLES,
                                              database.SCHEMA_VERSION_PRIVATE)
            out['files']['private_db'] = priv

            if out['contents']['config']:
                raw = zf.read(members['config.yaml'])
                try:
                    from user_config import _yaml
                    data = _yaml().load(raw.decode('utf-8'))
                except Exception as e:
                    raise RestoreError('config.yaml in the backup is not valid YAML: %s' % e)
                if data is not None and not isinstance(data, dict):
                    raise RestoreError('config.yaml in the backup is not a settings file.')
                cfg = os.path.join(workdir, 'config.yaml')
                with open(cfg, 'wb') as f:
                    f.write(raw)
                out['files']['config'] = cfg

            if out['contents']['secrets']:
                try:
                    secrets = json.loads(zf.read(members['data_private/secrets.json'])
                                         .decode('utf-8'))
                except ValueError as e:
                    raise RestoreError('secrets.json in the backup is not valid JSON: %s' % e)
                if not isinstance(secrets, dict):
                    raise RestoreError('secrets.json in the backup is not a key list.')
                out['secrets'] = secrets
                out['secret_names'] = sorted(secrets.keys())

            if out['contents']['public_db']:
                info = members['data_public/public.db']
                out['public'] = {'size': info.file_size}
                if want_market:
                    pub = _extract(zf, info, os.path.join(workdir, 'public.db'),
                                   RESTORE_MEMBERS['data_public/public.db'])
                    out['public'].update(_check_backup_db(
                        pub, 'public.db', PUBLIC_TABLES, database.SCHEMA_VERSION_PUBLIC))
                    out['files']['public_db'] = pub
        except (zipfile.BadZipFile, zlib.error, EOFError) as e:
            raise RestoreError('The zip is damaged: %s' % e)
    finally:
        zf.close()
    return out


def _public_schema_only(zip_path, workdir):
    """Schema version of the backup's public.db (for inspect), or an error."""
    import database
    with zipfile.ZipFile(zip_path) as zf:
        members = _scan_zip(zf)
        pub = _extract(zf, members['data_public/public.db'], os.path.join(workdir, 'public.db'),
                       RESTORE_MEMBERS['data_public/public.db'])
    try:
        con = sqlite3.connect(pub)
        try:
            version = con.execute('PRAGMA user_version').fetchone()[0]
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return None, False
    return version, version <= database.SCHEMA_VERSION_PUBLIC


def _safety_root():
    """Where pre-restore snapshots go: <INSTALL_HOME>/backups when installed,
    the gitignored <repo>/backup/ in a dev checkout."""
    if paths.IS_INSTALLED:
        return os.path.join(paths.INSTALL_HOME, 'backups')
    return os.path.join(paths.CODE_DIR, 'backup')


def _safety_snapshot(include_public):
    c = _core()
    root = _safety_root()
    os.makedirs(root, exist_ok=True)
    dest = c.unique_dir(os.path.join(root, 'pre-restore-%s'
                                     % datetime.now().strftime('%Y%m%d-%H%M%S')))
    os.makedirs(os.path.join(dest, 'data_private'), mode=0o700)
    saved = []
    if os.path.isfile(paths.PRIVATE_DB_PATH):
        c.copy_sqlite(paths.PRIVATE_DB_PATH, os.path.join(dest, 'data_private', 'private.db'))
        saved.append('data_private/private.db')
    if os.path.isfile(SECRETS_FILE):
        shutil.copy2(SECRETS_FILE, os.path.join(dest, 'data_private', 'secrets.json'))
        saved.append('data_private/secrets.json')
    if os.path.isfile(paths.USER_CONFIG_FILE):
        shutil.copy2(paths.USER_CONFIG_FILE, os.path.join(dest, 'config.yaml'))
        saved.append('config.yaml')
    if include_public and os.path.isfile(paths.PUBLIC_DB_PATH):
        c.copy_sqlite(paths.PUBLIC_DB_PATH, os.path.join(dest, 'data_public', 'public.db'))
        saved.append('data_public/public.db')
    with open(os.path.join(dest, 'README.txt'), 'w', encoding='utf-8') as f:
        f.write('FinanceApp data saved before restoring a backup (%s)\n'
                'From: %s\n\nContents: %s\n\n'
                'Same layout as a backup zip: to undo the restore, zip this folder and\n'
                'restore it from Settings > Backup & Export.\n'
                % (datetime.now().isoformat(timespec='seconds'), paths.DATA_ROOT,
                   ', '.join(saved) or 'nothing'))
    return dest


def _live_counts():
    con = sqlite3.connect(paths.PRIVATE_DB_PATH, timeout=30.0)
    try:
        return {t: con.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0]
                for t in PRIVATE_TABLES}
    finally:
        con.close()


def _apply_restore(backup, restore_config, restore_secrets, restore_market):
    """Save current data, then write the validated backup into the live data.
    Returns the response data."""
    files = backup['files']
    do_config = restore_config and 'config' in files
    do_secrets = restore_secrets and 'secrets' in backup
    do_market = restore_market and 'public_db' in files

    safety = _safety_snapshot(include_public=do_market)
    restored = []
    try:
        return _write_restore(backup, files, safety, restored,
                              do_config, do_secrets, do_market)
    except Exception as e:
        raise RuntimeError('%s (restored so far: %s). Your previous data was saved to %s'
                           % (e, ', '.join(restored) or 'nothing', safety))


def _write_restore(backup, files, safety, restored, do_config, do_secrets, do_market):
    import database
    c = _core()
    warnings = []
    restart = False

    os.makedirs(paths.DATA_PRIVATE_DIR, exist_ok=True)
    c.restore_sqlite_into(files['private_db'], paths.PRIVATE_DB_PATH)
    # Brings an older backup up to this version's schema (idempotent).
    database._init_private_database()
    restored.append('private.db')

    if do_market:
        os.makedirs(paths.DATA_PUBLIC_DIR, exist_ok=True)
        c.restore_sqlite_into(files['public_db'], paths.PUBLIC_DB_PATH)
        database._init_public_database()
        restored.append('public.db')
        restart = True  # in-memory market caches throughout the app
        try:
            from routes.valuation import invalidate_valuation_memo
            invalidate_valuation_memo()
            import data_manager
            data_manager._ticker_index_cache = None
            from services.providers import get_orchestrator
            get_orchestrator().clear_cache()
        except Exception as e:
            warnings.append('Could not clear cached market data: %s' % e)

    if do_config:
        tmp = paths.USER_CONFIG_FILE + '.restore-tmp'
        shutil.copyfile(files['config'], tmp)
        os.replace(tmp, paths.USER_CONFIG_FILE)
        try:
            from user_config import fill_missing_defaults
            fill_missing_defaults(paths.USER_CONFIG_FILE)
        except Exception as e:
            warnings.append('Could not add new default settings to config.yaml: %s' % e)
        try:
            from services.providers.config import reload_config
            reload_config()
        except Exception as e:
            warnings.append('Could not reload provider settings: %s' % e)
        restored.append('config.yaml')
        restart = True  # app settings (config.py) are read at startup

    if do_secrets:
        from services.providers.secrets import replace_secrets
        replace_secrets(backup['secrets'])
        restored.append('secrets.json')

    return {
        'restored': restored,
        'safety_backup': safety,
        'counts': _live_counts(),
        'restart_recommended': restart,
        'secrets_kept': not do_secrets,
        'warnings': warnings,
    }


@backup_bp.route('/inspect', methods=['POST'])
def backup_inspect():
    """Describe an uploaded backup zip without changing anything."""
    import database
    tmpdir = tempfile.mkdtemp(prefix='financeapp-inspect-')
    try:
        zip_path = _save_upload(tmpdir)
        info = _load_backup(zip_path, tmpdir, want_market=False)
        data = {
            'app_version': info.get('app_version'),
            'created': info.get('created'),
            'contents': info['contents'],
            'private': info['private'],
            'secret_names': info.get('secret_names', []),
            'public': None,
            'supported_schema': {'private': database.SCHEMA_VERSION_PRIVATE,
                                 'public': database.SCHEMA_VERSION_PUBLIC},
            'current_counts': _live_counts() if os.path.isfile(paths.PRIVATE_DB_PATH) else None,
            'safety_dir': _safety_root(),
        }
        if info['contents']['public_db']:
            version, ok = _public_schema_only(zip_path, tmpdir)
            data['public'] = {'size': info['public']['size'], 'schema_version': version,
                              'restorable': ok}
            if version is None:
                data['public']['problem'] = 'Market data in this backup is not a readable database.'
            elif not ok:
                data['public']['problem'] = ('Market data in this backup is from a newer '
                                             'version of FinanceApp; update the app first.')
        return _ok(data)
    except RestoreError as e:
        return _err(str(e), 400)
    except Exception as e:
        return _err('Could not read the backup: %s' % e, 500)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@backup_bp.route('/restore', methods=['POST'])
def backup_restore():
    """Replace the current data with an uploaded backup zip.

    Everything is validated first; current data is then saved to a
    pre-restore-<timestamp>/ folder before anything is overwritten.
    """
    restore_config = _form_flag('restore_config', True)
    restore_secrets = _form_flag('restore_secrets', True)
    restore_market = _form_flag('restore_market', False)

    if not _restore_lock.acquire(blocking=False):
        return _err('A restore is already in progress.', 409)
    tmpdir = tempfile.mkdtemp(prefix='financeapp-restore-')
    try:
        if restore_market:
            from services.screener import is_running
            if is_running():
                return _err('The screener is running; stop it (or restore without market '
                            'data) before restoring.', 409)
        zip_path = _save_upload(tmpdir)
        backup = _load_backup(zip_path, tmpdir, want_market=restore_market)
        try:
            os.remove(zip_path)  # free disk space before copying databases
        except OSError:
            pass
        data = _apply_restore(backup, restore_config, restore_secrets, restore_market)
        print('[Backup] Restored %s (safety copy: %s)'
              % (', '.join(data['restored']), data['safety_backup']))
        return _ok(data)
    except RestoreError as e:
        return _err(str(e), 400)
    except Exception as e:
        return _err('Restore failed: %s' % e, 500)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        _restore_lock.release()

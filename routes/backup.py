"""
Backup & export routes blueprint.

Endpoints:
- GET /api/backup/info                       - data locations and file sizes
- GET /api/backup/download?include_secrets=1&include_market=0
                                             - zip of the user's data (see _README)
- GET /api/backup/export/<transactions|stocks>.csv
                                             - one private table as CSV

The zip uses the same layout as an installer/uninstaller backup folder
(data_private/, data_public/, config.yaml), so once unzipped it can be passed
to ``installer.py --restore-from``.
"""

import csv
import io
import os
import shutil
import sqlite3
import tempfile
import zipfile
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

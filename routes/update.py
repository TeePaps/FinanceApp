"""
In-app updater routes blueprint (see docs/installer-updater-design.md, "Updater").

Endpoints:
- GET  /api/update/status    - mode, current/latest/previous versions, settings, job progress
- POST /api/update/check     - force a release check now
- POST /api/update/apply     - start the background update job (installed mode only)
- POST /api/update/rollback  - {restore_data: bool} revert to the previous version
- POST /api/update/settings  - {auto_check: bool, channel: "stable"|"beta"}
"""

from flask import Blueprint, jsonify, request

from services import updater

update_bp = Blueprint('update', __name__, url_prefix='/api/update')


def _ok(data, code=200):
    return jsonify({'success': True, 'data': data}), code


def _err(message, code=400):
    return jsonify({'success': False, 'error': message}), code


@update_bp.route('/status', methods=['GET'])
def update_status():
    try:
        return _ok(updater.get_status())
    except Exception as e:
        return _err('Could not read update status: %s' % e, 500)


@update_bp.route('/check', methods=['POST'])
def update_check():
    try:
        updater.check()
        return _ok(updater.get_status())
    except Exception as e:
        return _err('Update check failed: %s' % e, 500)


@update_bp.route('/apply', methods=['POST'])
def update_apply():
    body = request.get_json(silent=True) or {}
    ok, result = updater.start_apply(version=body.get('version'))
    if not ok:
        return _err(result, 409 if 'in progress' in str(result) else 400)
    return _ok(result, 202)


@update_bp.route('/rollback', methods=['POST'])
def update_rollback():
    body = request.get_json(silent=True) or {}
    ok, result = updater.rollback(restore_data=bool(body.get('restore_data')))
    if not ok:
        return _err(result, 409 if 'in progress' in str(result) else 400)
    return _ok(result)


@update_bp.route('/settings', methods=['POST'])
def update_settings():
    body = request.get_json(silent=True) or {}
    auto_check = body.get('auto_check')
    if auto_check is not None and not isinstance(auto_check, bool):
        return _err('auto_check must be true or false')
    try:
        settings = updater.save_settings(auto_check=auto_check, channel=body.get('channel'))
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        return _err('Could not save update settings: %s' % e, 500)
    return _ok(settings)

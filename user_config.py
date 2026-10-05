"""
User config bootstrap: paths.USER_CONFIG_FILE created from / topped up with
paths.DEFAULT_CONFIG_FILE.

- User config missing  -> copied verbatim from the defaults (comments intact).
- Keys missing in user -> deep-filled from the defaults, inserted with their
  default comments where ruamel allows. Existing user values are never
  overwritten, and a key whose user value is a non-mapping is left alone.

Called by config.py and services/providers/config.py before they read the
user config. Runs the merge once per process.
"""

import copy
import os
import shutil
import threading

import paths

_lock = threading.Lock()
_done = False


def _yaml():
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    y.default_flow_style = False
    y.indent(mapping=2, sequence=2, offset=2)
    return y


def _fill_missing(user, defaults):
    """Insert keys from defaults missing in user, recursively. Returns count."""
    added = 0
    keys = list(defaults.keys())
    for idx, key in enumerate(keys):
        dval = defaults[key]
        if key not in user:
            value = copy.deepcopy(dval)
            # Keep the defaults' ordering when the user map is a CommentedMap.
            pos = len(user)
            for prev in reversed(keys[:idx]):
                if prev in user:
                    pos = list(user.keys()).index(prev) + 1
                    break
            if hasattr(user, 'insert'):
                user.insert(pos, key, value)
                try:
                    comment = defaults.ca.items.get(key)
                    if comment:
                        user.ca.items[key] = copy.deepcopy(comment)
                except AttributeError:
                    pass
            else:
                user[key] = value
            added += 1
        elif isinstance(dval, dict) and isinstance(user.get(key), dict):
            added += _fill_missing(user[key], dval)
    return added


def fill_missing_defaults(user_path=None, default_path=None):
    """Deep-fill keys missing in ``user_path`` from the defaults file.

    Existing user values are never overwritten. Returns the number of keys
    added. Raises on unreadable YAML (callers decide how to report it).
    """
    user_path = user_path or paths.USER_CONFIG_FILE
    default_path = default_path or paths.DEFAULT_CONFIG_FILE
    if not os.path.isfile(default_path):
        return 0
    y = _yaml()
    with open(default_path, 'r', encoding='utf-8') as f:
        defaults = y.load(f)
    with open(user_path, 'r', encoding='utf-8') as f:
        user = y.load(f)
    if not isinstance(defaults, dict):
        return 0
    if user is None:
        user = copy.deepcopy(defaults)
        added = len(defaults)
    elif not isinstance(user, dict):
        raise ValueError("%s is not a mapping" % user_path)
    else:
        added = _fill_missing(user, defaults)
    if added:
        tmp = user_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            y.dump(user, f)
        os.replace(tmp, user_path)
    return added


def ensure_user_config():
    """Make sure USER_CONFIG_FILE exists and has every default key. Never raises."""
    global _done
    if _done:
        return
    with _lock:
        if _done:
            return
        _done = True
        user_path = paths.USER_CONFIG_FILE
        default_path = paths.DEFAULT_CONFIG_FILE
        if not os.path.isfile(default_path):
            return
        try:
            if not os.path.exists(user_path):
                os.makedirs(os.path.dirname(user_path), exist_ok=True)
                shutil.copyfile(default_path, user_path)
                print("[Config] Created %s from defaults" % user_path)
                return
            added = fill_missing_defaults(user_path, default_path)
            if added:
                print("[Config] Added %d missing default key(s) to %s" % (added, user_path))
        except Exception as e:  # a bad user file must not stop the app
            print("[Config] Warning: could not reconcile %s with defaults: %s" % (user_path, e))

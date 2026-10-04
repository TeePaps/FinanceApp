"""
Size-based rotation for the detached server's stdout/stderr log.

Standard library only: restart_server.py imports this without the venv.

Two users:
  - restart_server.py calls needs_rotation()/rotate() before spawning a
    server that is not running.
  - The running server calls start_rotation_watch(), because the launcher
    only redirects fds 1/2 into the file once and cannot rotate it later.
"""

import atexit
import os
import sys
import threading
import time


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


MAX_BYTES = _env_int("FINANCEAPP_SERVER_LOG_MAX_BYTES", 5 * 1024 * 1024)
BACKUP_COUNT = max(1, _env_int("FINANCEAPP_SERVER_LOG_BACKUPS", 5))


def needs_rotation(path):
    try:
        return os.path.getsize(path) > MAX_BYTES
    except OSError:
        return False


def rotate(path):
    """Shift path.N-1 -> path.N ... path -> path.1. Returns True on success.

    Only the oldest backup beyond BACKUP_COUNT is dropped (replaced by the
    shift). Never raises: on OSError returns False.
    """
    try:
        if not os.path.exists(path):
            return False
        # Highest index first so no newer backup is overwritten before it moves.
        for i in range(BACKUP_COUNT - 1, 0, -1):
            src = "%s.%d" % (path, i)
            if os.path.exists(src):
                os.replace(src, "%s.%d" % (path, i + 1))
        os.replace(path, path + ".1")
        return True
    except OSError:
        return False


def _flush_std():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass


def start_rotation_watch(path, interval_seconds=60):
    """Make fds 1/2 feed a pump thread that owns `path` and rotates it by size.

    Rotating by re-pointing fds 1/2 with dup2 while the server is running is
    not safe: dup2 is not atomic against concurrent writers on macOS (writers
    see EBADF in the window, measured ~12% of writes under load). So fds 1/2
    are re-pointed exactly once, here, at a pipe, before the server spawns its
    threads. The pump copies the pipe into the log and does the rename/reopen
    itself, so no writer is ever disturbed and no line is lost. It also works
    on Windows, where this process no longer holds the file via fds 1/2.

    Returns the pump thread, or None if setup failed (the server then keeps
    writing to the file directly, unrotated).
    """
    try:
        _flush_std()
        out = open(path, "ab")
        read_fd, write_fd = os.pipe()
        os.dup2(write_fd, 1)
        os.dup2(write_fd, 2)
        os.close(write_fd)
    except Exception as e:
        try:
            print("server_log: rotation disabled: %s" % e)
        except Exception:
            pass
        return None

    def _pump():
        f = out
        last_check = time.time()
        while True:
            try:
                data = os.read(read_fd, 65536)
            except OSError:
                break
            if not data:  # every writer closed (shutdown)
                break
            try:
                if f is not None:
                    f.write(data)
                    f.flush()
                    if time.time() - last_check >= interval_seconds:
                        last_check = time.time()
                        if needs_rotation(path):
                            f.close()
                            f = None
                            rotate(path)
                if f is None:
                    f = open(path, "ab")
            except Exception:
                # Keep draining the pipe no matter what, or the server blocks.
                try:
                    f = open(path, "ab")
                except Exception:
                    f = None
        try:
            if f is not None:
                f.close()
        except Exception:
            pass

    t = threading.Thread(target=_pump, name="server-log-pump", daemon=True)
    t.start()

    def _drain_at_exit():
        # Point fds 1/2 back at the file so the pipe's write end closes, then
        # let the pump finish writing whatever is still queued.
        _flush_std()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND)
            os.dup2(fd, 1)
            os.dup2(fd, 2)
            os.close(fd)
        except OSError:
            return
        t.join(2)

    atexit.register(_drain_at_exit)
    return t

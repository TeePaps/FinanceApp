#!/usr/bin/env python3
"""
Cross-platform server launcher for FinanceApp.

Works on Windows, macOS, and Linux with a stdlib-only implementation.
Manages the Flask server as a detached background process, tracked via
a PID file and verified with a /healthz check.

Usage:
    python restart_server.py [restart|start|stop|status|setup]

    restart  Stop any running server, then start a new one (default).
    start    Start the server if it is not already running.
    stop     Stop the server.
    status   Report whether the server is running and healthy.
    setup    Create the venv, install requirements, and prepare the repo.
"""

import os
import sys
import time
import platform
import subprocess
import signal
import urllib.request

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_PORT = int(os.environ.get("FINANCEAPP_PORT", "8080"))
SERVER_HOST = os.environ.get("FINANCEAPP_HOST", "127.0.0.1")
LOG_FILE = os.path.join(BASE_DIR, "logs", "server.log")
PID_FILE = os.path.join(BASE_DIR, "logs", "server.pid")

# Always use 127.0.0.1 for the health check, never "localhost" -- on
# Windows "localhost" can resolve to ::1 first and the connection hangs
# or fails even though the server is up on 127.0.0.1.
SERVER_URL = "http://127.0.0.1:%d" % SERVER_PORT
HEALTH_URL = SERVER_URL + "/healthz"

IS_WINDOWS = platform.system() == "Windows"


def get_python_executable():
    """Get the path to the Python executable in the venv, if present."""
    if IS_WINDOWS:
        venv_python = os.path.join(BASE_DIR, "venv", "Scripts", "python.exe")
    else:
        venv_python = os.path.join(BASE_DIR, "venv", "bin", "python3")
        if not os.path.exists(venv_python):
            fallback = os.path.join(BASE_DIR, "venv", "bin", "python")
            if os.path.exists(fallback):
                venv_python = fallback

    if os.path.exists(venv_python):
        return venv_python

    print("venv not found -- run: python restart_server.py setup")
    return sys.executable


def is_pid_alive(pid):
    """Check whether a process with the given PID is currently running."""
    if pid is None:
        return False
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5
            )
            # CSV output quotes each field, so match the exact PID field
            # rather than a substring (PID 123 must not match 1234).
            return '"%d"' % pid in result.stdout
        else:
            os.kill(pid, 0)
            return True
    except (OSError, subprocess.SubprocessError):
        return False


def read_pid_file():
    """Read the PID file, returning an int or None if missing/invalid."""
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def write_pid_file(pid):
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(pid))


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def check_health(timeout=2):
    """Single health check attempt against /healthz."""
    try:
        urllib.request.urlopen(HEALTH_URL, timeout=timeout)
        return True
    except Exception:
        return False


def wait_healthy(timeout=30):
    """Poll /healthz until it responds or the timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check_health(timeout=2):
            return True
        time.sleep(0.5)
    return False


def _stop_by_pid_file():
    pid = read_pid_file()
    if pid is None:
        return

    if is_pid_alive(pid):
        print("  Stopping PID %d (from PID file)..." % pid)
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid), "/T"],
                                capture_output=True, timeout=5)
            else:
                os.kill(pid, signal.SIGTERM)
        except (OSError, subprocess.SubprocessError) as e:
            print("  Warning: graceful stop failed: %s" % e)

        deadline = time.time() + 5
        while time.time() < deadline and is_pid_alive(pid):
            time.sleep(0.25)

        if is_pid_alive(pid):
            print("  PID %d still alive, forcing..." % pid)
            try:
                if IS_WINDOWS:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                    capture_output=True, timeout=5)
                else:
                    os.kill(pid, signal.SIGKILL)
            except (OSError, subprocess.SubprocessError) as e:
                print("  Warning: force stop failed: %s" % e)

    remove_pid_file()


def _stop_by_port_unix():
    try:
        result = subprocess.run(
            ["lsof", "-ti", ":%d" % SERVER_PORT],
            capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError) as e:
        print("  Warning: lsof unavailable (%s), skipping port fallback" % e)
        return

    for pid_str in result.stdout.strip().splitlines():
        pid_str = pid_str.strip()
        if not pid_str:
            continue
        try:
            pid = int(pid_str)
        except ValueError:
            continue

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue

        deadline = time.time() + 2
        while time.time() < deadline and is_pid_alive(pid):
            time.sleep(0.25)

        if is_pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

        print("  Stopped process on port %d (PID %d)" % (SERVER_PORT, pid))


def _stop_by_port_windows():
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError) as e:
        print("  Warning: netstat unavailable (%s), skipping port fallback" % e)
        return

    suffix = ":%d" % SERVER_PORT
    pids = set()
    for line in result.stdout.splitlines():
        cols = line.split()
        # Expected columns: Proto  Local Address  Foreign Address  State  PID
        if len(cols) < 5:
            continue
        local_addr = cols[1]
        state = cols[3]
        if local_addr.endswith(suffix) and state.upper() == "LISTENING":
            try:
                pids.add(int(cols[-1]))
            except ValueError:
                continue

    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T"],
                            capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

        time.sleep(0.25)

        if is_pid_alive(pid):
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass

        print("  Stopped process on port %d (PID %d)" % (SERVER_PORT, pid))


def stop_server():
    print("Stopping server...")
    _stop_by_pid_file()

    if IS_WINDOWS:
        _stop_by_port_windows()
    else:
        _stop_by_port_unix()


def _rotate_log():
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    if not os.path.exists(LOG_FILE):
        return

    log_1 = LOG_FILE + ".1"
    try:
        if os.path.exists(log_1):
            os.remove(log_1)
        os.replace(LOG_FILE, log_1)
    except OSError as e:
        print("  Warning: could not rotate log: %s" % e)


def _print_log_tail(n=20):
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for line in lines[-n:]:
            print(line.rstrip("\n"))
    except OSError as e:
        print("  (could not read %s: %s)" % (LOG_FILE, e))


def start_server(from_restart=False):
    if not from_restart and check_health():
        pid = read_pid_file()
        if pid is not None:
            print("Server already running: %s (pid %d)" % (SERVER_URL, pid))
        else:
            print("Server already running: %s" % SERVER_URL)
        return True

    _rotate_log()

    python_exe = get_python_executable()
    print("Starting server with %s..." % python_exe)

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    log_fh = open(LOG_FILE, "w", encoding="utf-8")
    try:
        if IS_WINDOWS:
            creationflags = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            proc = subprocess.Popen(
                [python_exe, "app.py"],
                cwd=BASE_DIR,
                stdout=log_fh.fileno(),
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=creationflags,
            )
        else:
            proc = subprocess.Popen(
                [python_exe, "app.py"],
                cwd=BASE_DIR,
                stdout=log_fh.fileno(),
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
    finally:
        log_fh.close()

    write_pid_file(proc.pid)

    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            print("Server process exited immediately (code %s)." % proc.returncode)
            break
        if check_health(timeout=2):
            print("Server running: %s (pid %d)" % (SERVER_URL, proc.pid))
            return True
        time.sleep(0.5)

    print("Server failed to start. Last 20 lines of logs/server.log:")
    _print_log_tail(20)
    return False


def status():
    pid = read_pid_file()
    alive = is_pid_alive(pid) if pid is not None else False
    healthy = check_health()

    if pid is not None:
        print("PID file: %d (%s)" % (pid, "alive" if alive else "dead"))
    else:
        print("PID file: none")

    print("Health check (%s): %s" % (HEALTH_URL, "OK" if healthy else "unreachable"))

    return 0 if healthy else 1


def setup():
    if sys.version_info < (3, 9):
        print("Error: Python 3.9+ is required (found %s)." % sys.version.split()[0])
        return 1

    venv_dir = os.path.join(BASE_DIR, "venv")
    if not os.path.isdir(venv_dir):
        print("Creating virtual environment...")
        result = subprocess.run([sys.executable, "-m", "venv", venv_dir])
        if result.returncode != 0:
            print("Error: failed to create venv.")
            return 1
    else:
        print("venv already exists, skipping creation.")

    venv_python = get_python_executable()

    print("Upgrading pip...")
    result = subprocess.run([venv_python, "-m", "pip", "install", "--upgrade", "pip"])
    if result.returncode != 0:
        print("Error: failed to upgrade pip.")
        return 1

    req_file = os.path.join(BASE_DIR, "requirements.txt")
    print("Installing requirements...")
    result = subprocess.run([venv_python, "-m", "pip", "install", "-r", req_file])
    if result.returncode != 0:
        print("Error: failed to install requirements.")
        return 1

    print("Marking data_public/public.db as skip-worktree...")
    try:
        result = subprocess.run(
            ["git", "update-index", "--skip-worktree", "data_public/public.db"],
            cwd=BASE_DIR
        )
        if result.returncode != 0:
            print("  Warning: git update-index failed (non-fatal).")
    except (OSError, subprocess.SubprocessError) as e:
        print("  Warning: git update-index failed: %s" % e)

    print()
    print("Setup complete. Next step:")
    print("  python restart_server.py restart")
    return 0


def main():
    valid_commands = ("restart", "start", "stop", "status", "setup")
    command = sys.argv[1] if len(sys.argv) > 1 else "restart"

    if command not in valid_commands:
        print("Usage: python restart_server.py [%s]" % "|".join(valid_commands))
        return 1

    print("Platform: %s" % platform.system())
    print("Target: %s" % SERVER_URL)
    print()

    if command == "setup":
        return setup()

    if command == "stop":
        stop_server()
        return 0

    if command == "status":
        return status()

    if command == "start":
        return 0 if start_server(from_restart=False) else 1

    # restart (default)
    stop_server()
    time.sleep(0.5)
    return 0 if start_server(from_restart=True) else 1


if __name__ == "__main__":
    sys.exit(main())

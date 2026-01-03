#!/usr/bin/env python3
"""
Cross-platform server restart script.

Works on Windows, macOS, and Linux.
Stops any running Flask server and starts a new instance.

Usage:
    python restart_server.py
"""

import os
import sys
import time
import platform
import subprocess
import signal

# Configuration
SERVER_PORT = 8080
SERVER_SCRIPT = "app.py"
LOG_FILE = os.path.join(os.path.dirname(__file__), "logs", "server.log")


def get_python_executable():
    """Get the path to the Python executable in the venv."""
    base_dir = os.path.dirname(os.path.abspath(__file__))

    if platform.system() == "Windows":
        venv_python = os.path.join(base_dir, "venv", "Scripts", "python.exe")
    else:
        venv_python = os.path.join(base_dir, "venv", "bin", "python3")

    if os.path.exists(venv_python):
        return venv_python

    # Fall back to the current Python
    return sys.executable


def stop_server_windows():
    """Stop the Flask server on Windows."""
    print("Stopping existing server (Windows)...")

    # Find and kill Python processes running app.py
    try:
        # Use WMIC to find Python processes with app.py in command line
        result = subprocess.run(
            ["wmic", "process", "where",
             "name='python.exe' or name='pythonw.exe'",
             "get", "processid,commandline"],
            capture_output=True, text=True, timeout=10
        )

        for line in result.stdout.split('\n'):
            if SERVER_SCRIPT in line:
                # Extract PID from the line
                parts = line.strip().split()
                if parts:
                    try:
                        pid = int(parts[-1])
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                      capture_output=True, timeout=5)
                        print(f"  Killed process {pid}")
                    except (ValueError, subprocess.SubprocessError):
                        pass
    except subprocess.SubprocessError as e:
        print(f"  Warning: Could not query processes: {e}")

    # Also try to kill by port
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.split('\n'):
            if f":{SERVER_PORT}" in line and "LISTENING" in line:
                parts = line.strip().split()
                if parts:
                    try:
                        pid = int(parts[-1])
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                      capture_output=True, timeout=5)
                        print(f"  Killed process on port {SERVER_PORT} (PID {pid})")
                    except (ValueError, subprocess.SubprocessError):
                        pass
    except subprocess.SubprocessError as e:
        print(f"  Warning: Could not check port: {e}")


def stop_server_unix():
    """Stop the Flask server on macOS/Linux."""
    print("Stopping existing server (Unix)...")

    # Kill by process name
    try:
        subprocess.run(
            ["pkill", "-9", "-f", f"python.*{SERVER_SCRIPT}"],
            capture_output=True, timeout=5
        )
    except subprocess.SubprocessError:
        pass

    # Kill by port using lsof
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{SERVER_PORT}"],
            capture_output=True, text=True, timeout=5
        )
        pids = result.stdout.strip().split('\n')
        for pid in pids:
            if pid:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                    print(f"  Killed process on port {SERVER_PORT} (PID {pid})")
                except (ValueError, OSError):
                    pass
    except subprocess.SubprocessError:
        pass


def start_server_windows(python_exe, script_path):
    """Start the Flask server on Windows."""
    print("Starting server (Windows)...")

    # Ensure log directory exists
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    # Start in a new console window
    CREATE_NEW_CONSOLE = 0x00000010
    subprocess.Popen(
        [python_exe, script_path],
        creationflags=CREATE_NEW_CONSOLE,
        cwd=os.path.dirname(script_path)
    )


def start_server_unix(python_exe, script_path):
    """Start the Flask server on macOS/Linux."""
    print("Starting server (Unix)...")

    # Ensure log directory exists
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    # Use nohup to run in background
    with open(LOG_FILE, 'w') as log:
        subprocess.Popen(
            [python_exe, script_path],
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(script_path),
            start_new_session=True
        )


def verify_server():
    """Verify the server started successfully."""
    print(f"Verifying server on http://localhost:{SERVER_PORT}...")

    # Wait a bit for server to start
    time.sleep(3)

    try:
        import urllib.request
        urllib.request.urlopen(f"http://localhost:{SERVER_PORT}", timeout=5)
        print("Server started successfully!")
        return True
    except Exception:
        print(f"Warning: Server may not have started. Check {LOG_FILE}")
        return False


def main():
    """Main entry point."""
    is_windows = platform.system() == "Windows"

    # Get paths
    base_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(base_dir, SERVER_SCRIPT)
    python_exe = get_python_executable()

    print(f"Platform: {platform.system()}")
    print(f"Python: {python_exe}")
    print(f"Script: {script_path}")
    print()

    # Stop existing server
    if is_windows:
        stop_server_windows()
    else:
        stop_server_unix()

    # Wait for processes to die
    time.sleep(2)

    # Start new server
    if is_windows:
        start_server_windows(python_exe, script_path)
    else:
        start_server_unix(python_exe, script_path)

    # Verify
    verify_server()

    print()
    print(f"Server running at http://localhost:{SERVER_PORT}")


if __name__ == "__main__":
    main()

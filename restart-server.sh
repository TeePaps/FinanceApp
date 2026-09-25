#!/bin/sh
exec python3 "$(dirname "$0")/restart_server.py" "$@"

#!/bin/bash
# Restart the Flask development server

echo "Stopping existing server..."
pkill -9 -f "python3.*app.py" 2>/dev/null
pkill -9 -f "python.*app.py" 2>/dev/null
lsof -ti:8080 | xargs kill -9 2>/dev/null
sleep 2

echo "Starting server..."
cd "$(dirname "$0")"
nohup ./venv/bin/python3 app.py > /tmp/flask_server.log 2>&1 &

echo "Server restarting on http://localhost:8080"
sleep 3

# Verify server is running
if curl -s http://localhost:8080 > /dev/null 2>&1; then
    echo "Server started successfully."
else
    echo "Warning: Server may not have started. Check /tmp/flask_server.log"
fi

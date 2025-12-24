"""
Activity Log Manager with SSE Support

Provides a thread-safe activity log for tracking provider operations, data fetches,
and other system events. Supports Server-Sent Events (SSE) for real-time streaming
to web clients.

Usage:
    from services.activity_log import activity_log

    # Add log entries
    activity_log.log('info', 'yfinance', 'Fetched price for AAPL', ticker='AAPL')
    activity_log.log('error', 'fmp', 'API key invalid')

    # Subscribe to SSE stream (in Flask route)
    @app.route('/api/activity/stream')
    def activity_stream():
        return Response(activity_log.subscribe(), mimetype='text/event-stream')
"""

import json
import time
import threading
from collections import deque
from datetime import datetime
from typing import Optional, List, Dict, Generator


class ActivityLogManager:
    """Thread-safe activity log with SSE streaming support."""

    # Log level constants
    DEBUG = 'debug'
    INFO = 'info'
    SUCCESS = 'success'
    WARNING = 'warning'
    ERROR = 'error'

    # Valid log levels for validation
    VALID_LEVELS = {DEBUG, INFO, SUCCESS, WARNING, ERROR}

    # ANSI color codes for console output
    COLORS = {
        DEBUG: '\033[90m',      # Gray
        INFO: '\033[94m',       # Blue
        SUCCESS: '\033[92m',    # Green
        WARNING: '\033[93m',    # Yellow
        ERROR: '\033[91m',      # Red
        'RESET': '\033[0m'
    }

    def __init__(self, max_entries: int = 100):
        """
        Initialize the activity log manager.

        Args:
            max_entries: Maximum number of log entries to retain (ring buffer size)
        """
        self._logs = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._subscribers = []
        self._subscriber_lock = threading.Lock()
        self._next_subscriber_id = 0

    def log(self, level: str, source: str, message: str, ticker: Optional[str] = None) -> None:
        """
        Add a log entry and notify all SSE subscribers.

        Args:
            level: Log level (debug, info, success, warning, error)
            source: Source of the log (e.g., 'yfinance', 'screener', 'database')
            message: Log message
            ticker: Optional ticker symbol associated with this log entry
        """
        # Validate level
        level = level.lower()
        if level not in self.VALID_LEVELS:
            level = self.INFO

        # Create log entry
        entry = {
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'level': level,
            'source': source,
            'message': message,
            'ticker': ticker
        }

        # Add to ring buffer (thread-safe)
        with self._lock:
            self._logs.append(entry)

        # Print to console with color
        self._print_to_console(entry)

        # Notify all subscribers (thread-safe)
        self._notify_subscribers(entry)

    def _print_to_console(self, entry: Dict) -> None:
        """Print log entry to console with color formatting."""
        level = entry['level']
        color = self.COLORS.get(level, '')
        reset = self.COLORS['RESET']

        level_str = f"{color}[{level.upper()}]{reset}"
        source_str = f"[{entry['source']}]"

        # Include ticker if present
        if entry['ticker']:
            ticker_str = f"[{entry['ticker']}]"
            print(f"[ActivityLog] {level_str} {source_str} {ticker_str} {entry['message']}")
        else:
            print(f"[ActivityLog] {level_str} {source_str} {entry['message']}")

    def _notify_subscribers(self, entry: Dict) -> None:
        """Send log entry to all active SSE subscribers."""
        # Format as SSE event
        event_data = json.dumps(entry)
        sse_message = f"event: log\ndata: {event_data}\n\n"

        with self._subscriber_lock:
            # Send to all subscribers, remove any that fail
            dead_subscribers = []

            for subscriber_id, queue in self._subscribers:
                try:
                    queue.append(sse_message)
                except Exception:
                    # Subscriber is dead, mark for removal
                    dead_subscribers.append((subscriber_id, queue))

            # Remove dead subscribers
            for dead_sub in dead_subscribers:
                if dead_sub in self._subscribers:
                    self._subscribers.remove(dead_sub)

    def subscribe(self) -> Generator[str, None, None]:
        """
        Subscribe to SSE stream of log events.

        Yields SSE-formatted events including:
        - Initial batch of recent logs
        - New log entries as they occur
        - Heartbeat events every 15 seconds

        Returns:
            Generator yielding SSE event strings
        """
        # Create a queue for this subscriber
        subscriber_queue = deque()

        # Register subscriber
        with self._subscriber_lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers.append((subscriber_id, subscriber_queue))

        try:
            # Send initial batch of recent logs
            recent_logs = self.get_recent(20)
            for entry in recent_logs:
                event_data = json.dumps(entry)
                yield f"event: log\ndata: {event_data}\n\n"

            # Send initial heartbeat
            yield f"event: heartbeat\ndata: {json.dumps({'timestamp': datetime.now().isoformat()})}\n\n"

            last_heartbeat = time.time()

            # Stream new events as they arrive
            while True:
                # Check for new messages in queue
                if subscriber_queue:
                    # Pop and send all queued messages
                    while subscriber_queue:
                        message = subscriber_queue.popleft()
                        yield message

                # Send heartbeat every 15 seconds
                current_time = time.time()
                if current_time - last_heartbeat >= 15:
                    yield f"event: heartbeat\ndata: {json.dumps({'timestamp': datetime.now().isoformat()})}\n\n"
                    last_heartbeat = current_time

                # Small sleep to prevent CPU spinning
                time.sleep(0.1)

        except GeneratorExit:
            # Client disconnected, clean up
            pass
        except Exception as e:
            # Log error but don't crash
            print(f"[ActivityLog] [ERROR] SSE subscriber error: {e}")
        finally:
            # Unregister subscriber
            with self._subscriber_lock:
                subscriber_entry = (subscriber_id, subscriber_queue)
                if subscriber_entry in self._subscribers:
                    self._subscribers.remove(subscriber_entry)

    def get_recent(self, n: int = 20) -> List[Dict]:
        """
        Get the most recent log entries.

        Args:
            n: Number of recent entries to retrieve

        Returns:
            List of log entry dictionaries (most recent last)
        """
        with self._lock:
            # Return last n entries (or all if fewer than n)
            logs_list = list(self._logs)
            return logs_list[-n:] if len(logs_list) > n else logs_list

    def clear(self) -> None:
        """Clear all log entries."""
        with self._lock:
            self._logs.clear()

        # Notify subscribers about clear
        clear_event = {
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'level': self.INFO,
            'source': 'system',
            'message': 'Activity log cleared',
            'ticker': None
        }
        self._notify_subscribers(clear_event)

        print("[ActivityLog] [INFO] [system] Activity log cleared")

    def get_subscriber_count(self) -> int:
        """Get the number of active SSE subscribers."""
        with self._subscriber_lock:
            return len(self._subscribers)

    def get_log_count(self) -> int:
        """Get the current number of log entries."""
        with self._lock:
            return len(self._logs)


# Module-level singleton instance
activity_log = ActivityLogManager(max_entries=100)

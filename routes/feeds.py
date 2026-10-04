"""
Feed monitoring routes blueprint.

Endpoints:
- GET /api/feed-stats?hours=N - Per-source provider outcomes and latency from the
                                durable feed_events log, plus circuit-breaker state
"""

from flask import Blueprint, jsonify, request

from services import feed_events
from services.providers import get_config
from services.providers.circuit_breaker import get_circuit_breaker

feeds_bp = Blueprint('feeds', __name__, url_prefix='/api')

DEFAULT_HOURS = 24


def _requested_hours() -> int:
    """`hours` query arg, clamped to 1..retention; the default on bad input."""
    hours = request.args.get('hours', DEFAULT_HOURS, type=int)
    if hours is None:
        hours = DEFAULT_HOURS
    return max(1, min(hours, feed_events.retention_days() * 24))


def _circuit_breaker_status() -> dict:
    """Breaker settings and per-breaker state; provider is the bare name, not the key."""
    config = get_config()
    providers = {
        key: {**status, 'provider': key.split(':', 1)[0]}
        for key, status in get_circuit_breaker().get_all_status().items()
    }
    return {
        'enabled': config.circuit_breaker_enabled,
        'failure_threshold': config.failure_threshold,
        'failure_window_seconds': config.failure_window_seconds,
        'cooldown_seconds': config.cooldown_seconds,
        'open': [key for key, status in providers.items() if status['state'] != 'closed'],
        'providers': providers,
    }


@feeds_bp.route('/feed-stats')
def api_feed_stats():
    """Provider outcome/latency summary for the last N hours (default 24)."""
    try:
        feed_events.flush()   # make buffered events visible
        return jsonify({
            'success': True,
            **feed_events.stats(_requested_hours()),
            'circuit_breakers': _circuit_breaker_status(),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

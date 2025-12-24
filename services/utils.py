"""
Shared utility functions for the Finance App.
"""

import math


def sanitize_for_json(obj):
    """Replace NaN and Inf values with None for JSON compatibility."""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj

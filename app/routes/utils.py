import logging
import re
import threading
import time
from collections import defaultdict
from functools import wraps

from quart import Response, jsonify, request

async def respond_with(data: dict) -> Response:
    resp = jsonify(data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp


def log_error(label: str, message: str, hint: str = "", code: int = 0):
    logging.error("%s [%s] %s | %s", label, code, message, hint)


def get_remote_ip() -> str:
    """Extract client IP address, prioritizing Cloudflare verified CF-Connecting-IP over X-Forwarded-For."""
    if cf_connecting_ip := request.headers.get("CF-Connecting-IP"):
        return cf_connecting_ip.strip()
    if x_forwarded_for := request.headers.get("X-Forwarded-For"):
        return x_forwarded_for.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def is_valid_user_id(user_id: str) -> bool:
    """Validate that the user ID follows standard numeric (MAL), AniList (al_digits), Simkl (simkl_digits), Guest (guest_...), or MongoDB Hex UID pattern."""
    if not user_id:
        return False
    return bool(
        re.match(r"^(?:al_|simkl_)?[0-9]+$", user_id)
        or re.match(r"^[0-9a-fA-F]{24}$", user_id)
        or re.match(r"^guest_[a-zA-Z0-9_]+$", user_id)
    )


_rate_limit_lock = threading.Lock()
_rate_limit_buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
_last_rate_limit_cleanup = time.monotonic()


def _cleanup_rate_limits(now: float):
    """Purge stale rate limit buckets to prevent memory accumulation."""
    global _last_rate_limit_cleanup
    if now - _last_rate_limit_cleanup > 60.0:
        _last_rate_limit_cleanup = now
        stale_keys = [k for k, timestamps in _rate_limit_buckets.items() if not timestamps or timestamps[-1] < now - 3600]
        for k in stale_keys:
            del _rate_limit_buckets[k]


def rate_limit(limit: int, period_seconds: int = 60):
    """Fast in-memory sliding window IP rate limiter with automatic pruning."""

    def decorator(f):
        @wraps(f)
        async def wrapped(*args, **kwargs):
            ip = get_remote_ip()
            route = request.path
            key = (ip, route)
            now = time.monotonic()
            cutoff = now - period_seconds

            with _rate_limit_lock:
                _cleanup_rate_limits(now)
                timestamps = _rate_limit_buckets[key]
                valid_timestamps = [t for t in timestamps if t > cutoff]

                if len(valid_timestamps) >= limit:
                    _rate_limit_buckets[key] = valid_timestamps
                    logging.warning("Rate limit exceeded for IP %s on %s: %d/%d", ip, route, len(valid_timestamps), limit)
                    return jsonify(
                        {"error": "Too Many Requests", "message": "Rate limit exceeded. Please try again later."}
                    ), 429

                valid_timestamps.append(now)
                _rate_limit_buckets[key] = valid_timestamps

            return await f(*args, **kwargs)

        return wrapped

    return decorator

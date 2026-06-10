import logging
import re
from functools import wraps
from datetime import datetime, timedelta

from quart import Response, jsonify, request


async def respond_with(data: dict) -> Response:
    resp = jsonify(data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp


def log_error(label: str, message: str, hint: str = "", code: int = 0):
    logging.error("%s [%s] %s | %s", label, code, message, hint)


def get_remote_ip() -> str:
    """Extract client IP address, handling proxy headers robustly."""
    if x_forwarded_for := request.headers.get("X-Forwarded-For"):
        return x_forwarded_for.split(",")[0].strip()
    if cf_connecting_ip := request.headers.get("CF-Connecting-IP"):
        return cf_connecting_ip.strip()
    return request.remote_addr or "127.0.0.1"


def is_valid_user_id(user_id: str) -> bool:
    """Validate that the user ID follows the MAL (digits), AniList (al_digits), or Simkl (simkl_digits) pattern."""
    if not user_id:
        return False
    return bool(re.match(r"^(?:al_|simkl_)?[0-9]+$", user_id))


def rate_limit(limit: int, period_seconds: int = 60):
    """IP-based sliding window rate limiter decorated using MongoDB collection logs."""
    def decorator(f):
        @wraps(f)
        async def wrapped(*args, **kwargs):
            from app.services.db import db
            ip = get_remote_ip()
            route = request.path
            
            from datetime import timezone
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            cutoff = now - timedelta(seconds=period_seconds)
            
            try:
                # Count requests in window
                count = db.rate_limits.count_documents({
                    "ip": ip,
                    "route": route,
                    "timestamp": {"$gt": cutoff}
                })
                
                if count >= limit:
                    logging.warning("Rate limit exceeded for IP %s on %s: %d/%d", ip, route, count, limit)
                    return jsonify({
                        "error": "Too Many Requests",
                        "message": "Rate limit exceeded. Please try again later."
                    }), 429
                
                # Record current request
                db.rate_limits.insert_one({
                    "ip": ip,
                    "route": route,
                    "timestamp": now
                })
            except Exception as e:
                # Fail close on critical database error as per security guidelines
                logging.error("Rate limiter database error: %s", e)
                return jsonify({
                    "error": "Internal Server Error",
                    "message": "Rate limiter error."
                }), 500
                
            return await f(*args, **kwargs)
        return wrapped
    return decorator

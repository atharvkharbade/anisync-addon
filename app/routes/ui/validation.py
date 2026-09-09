import asyncio

from quart import request

from app.routes.utils import rate_limit

from .blueprint import ui_bp


async def check_gemini_api_key_valid(api_key: str) -> tuple[bool, str]:
    import httpx

    if not api_key:
        return False, "Key cannot be empty"

    models = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"]
    payload = {"contents": [{"parts": [{"text": "Hello, respond with OK if you read this."}]}]}
    last_error = "Invalid API key"

    async with httpx.AsyncClient(timeout=5) as client:
        for model in models:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return True, "API key verified"
                else:
                    try:
                        last_error = resp.json().get("error", {}).get("message", "Invalid API key")
                    except Exception:
                        last_error = f"HTTP {resp.status_code}"
            except Exception as e:
                last_error = f"Validation failed: {str(e)}"

    return False, last_error


@ui_bp.route("/gemini/validation", methods=["POST"])
@rate_limit(limit=10, period_seconds=60)
async def validate_gemini_key():
    data = await request.get_json() or {}
    api_key = data.get("api_key", "").strip()
    is_valid, msg = await check_gemini_api_key_valid(api_key)
    if is_valid:
        return {"status": "success", "message": msg}
    return {"status": "error", "message": msg}, 400


@ui_bp.route("/rpdb/validation", methods=["POST"])
@rate_limit(limit=10, period_seconds=60)
async def validate_rpdb_key():
    data = await request.get_json() or {}
    api_key = data.get("api_key", "").strip()
    if not api_key:
        return {"status": "error", "message": "Key cannot be empty"}, 400
    try:
        from app.services.poster import validate_rpdb_api_key

        is_valid = await validate_rpdb_api_key(api_key)
        if is_valid:
            return {"status": "success", "message": "API key verified ✓"}
        else:
            return {"status": "error", "message": "Invalid RPDB API key"}, 400
    except Exception as e:
        return {"status": "error", "message": f"Validation failed: {str(e)}"}, 500


@ui_bp.route("/top_poster/validation", methods=["POST"])
@rate_limit(limit=10, period_seconds=60)
async def validate_top_poster_key():
    data = await request.get_json() or {}
    api_key = data.get("api_key", "").strip()
    if not api_key:
        return {"status": "error", "message": "Key cannot be empty"}, 400
    try:
        from app.services.poster import validate_top_poster_api_key

        is_valid = await validate_top_poster_api_key(api_key)
        if is_valid:
            return {"status": "success", "message": "API key verified ✓"}
        else:
            return {"status": "error", "message": "Invalid TOP Posters API key"}, 400
    except Exception as e:
        return {"status": "error", "message": f"Validation failed: {str(e)}"}, 500


@ui_bp.route("/health")
@rate_limit(limit=60, period_seconds=60)
async def health():
    from app.services.db import db

    try:
        # Verify MongoDB is reachable
        await asyncio.to_thread(db.command, "ping")
        return {"status": "healthy", "database": "connected"}, 200
    except Exception as e:
        return {"status": "unhealthy", "database": str(e)}, 500

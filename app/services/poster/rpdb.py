import asyncio
import logging
from urllib.parse import urlencode

from app.services.http import get_client


async def validate_rpdb_api_key(api_key: str) -> bool:
    """
    Validate the RPDB API key by querying the /isValid endpoint.
    """
    if not api_key:
        return False
    url = f"https://api.ratingposterdb.com/{api_key}/isValid"
    try:
        client = get_client()
        resp = await client.get(url, timeout=8)
        return resp.status_code == 200
    except Exception as e:
        logging.error("Failed to validate RPDB API key: %s", e)
        return False


def check_rpdb_key_validity_background(user_id: str, rpdb_key: str):
    """
    Validate the RPDB key in the background and update the user's validation status.
    """
    async def task():
        from datetime import datetime

        from app.services.db import get_user, store_user

        is_valid = await validate_rpdb_api_key(rpdb_key)

        user = get_user(user_id)
        if user and user.get("rpdb_api_key") == rpdb_key:
            user["rpdb_key_valid"] = is_valid
            user["rpdb_key_last_checked"] = datetime.utcnow()
            store_user(user)

    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            loop.create_task(task())
    except RuntimeError:
        pass


def build_rpdb_poster_url(
    rpdb_key: str,
    media_type: str,
    shape: str = "poster",
    rec_language: str = "en",
    imdb_id: str | None = None,
    tmdb_id: str | None = None,
    tvdb_id: str | None = None,
) -> str | None:
    """
    Construct the RPDB poster or backdrop URL.
    """
    if not rpdb_key:
        return None

    id_type = None
    media_id = None

    if imdb_id:
        id_type = "imdb"
        media_id = imdb_id
    elif tmdb_id:
        id_type = "tmdb"
        prefix = "movie" if media_type == "movie" else "series"
        media_id = f"{prefix}-{tmdb_id}"
    elif tvdb_id:
        id_type = "tvdb"
        prefix = "movie" if media_type == "movie" else "series"
        media_id = f"{prefix}-{tvdb_id}"

    if not id_type or not media_id:
        return None

    endpoint = "backdrop-default" if shape == "landscape" else "poster-default"
    url = f"https://api.ratingposterdb.com/{rpdb_key}/{id_type}/{endpoint}/{media_id}.jpg"
    tier = rpdb_key.split("-")[0].lower() if rpdb_key else "t0"
    lang = (rec_language or "en").split("-")[0].lower()

    params = {"fallback": "true"}
    if tier not in ["t0", "t1"] and lang != "en":
        params["lang"] = lang

    return f"{url}?{urlencode(params)}"

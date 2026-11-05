import asyncio
import logging
from datetime import UTC, datetime, timedelta

from config import Config
from app.services.http import get_client


def pick_fanart_background(data: dict | None) -> str | None:
    """
    Selects the highest quality background from Fanart.tv metadata.
    Prioritizes textless artwork (lang in ['', '00']), ordered by likes descending.
    Checks 4K background followed by standard 1080p backgrounds for TV and Movies.
    """
    if not data or not isinstance(data, dict):
        return None

    candidates = []
    # TV backgrounds
    for key in ("show4kbackground", "showbackground"):
        bgs = data.get(key)
        if isinstance(bgs, list):
            candidates.extend(bgs)

    # Movie backgrounds
    for key in ("movie4kbackground", "moviebackground"):
        bgs = data.get(key)
        if isinstance(bgs, list):
            candidates.extend(bgs)

    if not candidates:
        return None

    def _score(item: dict):
        try:
            likes = int(item.get("likes") or 0)
        except (ValueError, TypeError):
            likes = 0
        lang = (item.get("lang") or "").strip().lower()
        is_textless = 1 if lang in ("", "00") else 0
        return (is_textless, likes)

    sorted_candidates = sorted(candidates, key=_score, reverse=True)
    return sorted_candidates[0].get("url") if sorted_candidates else None


async def fetch_fanart(kind: str, ext_id: str | int | None, client_key: str | None = None) -> dict:
    """
    Fetches Fanart.tv metadata for TV (TheTVDB ID) or Movies (IMDb ID) with multi-tiered caching.
    - 7-day TTL for successful hits
    - 24-hour TTL for misses
    - Stale cache fallback on upstream 429/5xx errors
    - Never leaks personal or server API keys into logs or URLs
    """
    if not ext_id or str(ext_id).strip() == "":
        return {}

    normalized_kind = "movies" if kind in ("movie", "movies") else "tv"
    str_id = str(ext_id).strip()

    from app.services.db import db
    col = db.get_collection("fanart_meta_cache")
    now = datetime.now(UTC).replace(tzinfo=None)

    stale_data = None
    try:
        cached = col.find_one({"kind": normalized_kind, "ext_id": str_id})
        if cached:
            if cached.get("expires_at") and cached.get("expires_at") > now:
                return cached.get("data", {})
            stale_data = cached.get("data", {})
    except Exception as e:
        logging.warning("Error reading fanart_meta_cache for %s/%s: %s", normalized_kind, str_id, e)

    server_key = (Config.FANART_API_KEY or "").strip()
    user_key = (client_key or "").strip()

    if not server_key and not user_key:
        return stale_data or {}

    headers = {}
    if server_key:
        headers["api-key"] = server_key
        if user_key:
            headers["client-key"] = user_key
    else:
        # Fallback if no server key is configured
        headers["api-key"] = user_key

    url = f"https://webservice.fanart.tv/v3/{normalized_kind}/{str_id}"

    try:
        client = get_client()
        resp = await client.get(url, headers=headers, timeout=5.0)

        # Fanart.tv rejects the whole request (401) when the personal client-key is invalid,
        # even if the project key is fine: retry with the project key alone
        if resp.status_code == 401 and server_key and user_key:
            logging.warning("Fanart.tv rejected the personal client key; retrying with the project key only")
            resp = await client.get(url, headers={"api-key": server_key}, timeout=5.0)

        if resp.status_code == 200:
            data = resp.json()
            if not isinstance(data, dict):
                data = {}
            has_art = bool(
                data.get("showbackground")
                or data.get("moviebackground")
                or data.get("show4kbackground")
                or data.get("movie4kbackground")
            )
            ttl = timedelta(days=7) if has_art else timedelta(days=1)
            try:
                col.update_one(
                    {"kind": normalized_kind, "ext_id": str_id},
                    {
                        "$set": {
                            "kind": normalized_kind,
                            "ext_id": str_id,
                            "data": data,
                            "has_art": has_art,
                            "expires_at": now + ttl,
                            "updated_at": now,
                        }
                    },
                    upsert=True,
                )
            except Exception as ex:
                logging.warning("Failed to update fanart_meta_cache for %s/%s: %s", normalized_kind, str_id, ex)
            return data

        elif resp.status_code == 404:
            try:
                col.update_one(
                    {"kind": normalized_kind, "ext_id": str_id},
                    {
                        "$set": {
                            "kind": normalized_kind,
                            "ext_id": str_id,
                            "data": {},
                            "has_art": False,
                            "expires_at": now + timedelta(days=1),
                            "updated_at": now,
                        }
                    },
                    upsert=True,
                )
            except Exception as ex:
                logging.warning("Failed to update 404 fanart_meta_cache for %s/%s: %s", normalized_kind, str_id, ex)
            return {}

        elif resp.status_code in (429, 500, 502, 503, 504):
            logging.warning("Fanart.tv returned %s for %s/%s, falling back to stale cache if present", resp.status_code, normalized_kind, str_id)
            if stale_data is not None:
                return stale_data
            return {}

    except Exception as e:
        logging.warning("Failed to fetch metadata from Fanart.tv (%s/%s): %s", normalized_kind, str_id, e)
        if stale_data is not None:
            return stale_data

    return {}


async def bg_warm_fanart(items: list[tuple[str, str, str | None]]):
    """
    Background non-blocking task to fetch and cache Fanart.tv metadata for items missing from fanart_meta_cache.
    Accepts list of tuples: (kind, ext_id, client_key).
    """
    sem = asyncio.Semaphore(5)

    async def fetch_one(kind: str, ext_id: str, client_key: str | None):
        if not ext_id:
            return
        async with sem:
            try:
                await fetch_fanart(kind, ext_id, client_key)
            except Exception:
                pass

    tasks = [fetch_one(k, eid, ck) for (k, eid, ck) in items]
    await asyncio.gather(*tasks, return_exceptions=True)

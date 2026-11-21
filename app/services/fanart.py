import asyncio
import logging
from datetime import UTC, datetime, timedelta

from config import Config
from app.services.http import get_client

PURGE_RETENTION_DAYS = 30
_INFLIGHT_TIMEOUT_SECONDS = 10.5
_inflight: dict[tuple, asyncio.Task] = {}
_background_tasks: set[asyncio.Task] = set()


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _upsert_fanart_cache(
    col,
    normalized_kind: str,
    str_id: str,
    data: dict,
    has_art: bool,
    ttl: timedelta,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    expires_at = now + ttl
    purge_at = expires_at + timedelta(days=PURGE_RETENTION_DAYS)
    col.update_one(
        {"kind": normalized_kind, "ext_id": str_id},
        {
            "$set": {
                "kind": normalized_kind,
                "ext_id": str_id,
                "data": data,
                "has_art": has_art,
                "expires_at": expires_at,
                "purge_at": purge_at,
                "updated_at": now,
            }
        },
        upsert=True,
    )


async def _fetch_fanart_remote(
    col,
    normalized_kind: str,
    str_id: str,
    server_key: str,
    user_key: str,
) -> dict | None:
    """Fetch Fanart.tv metadata from upstream and update cache. Returns None on transient 429/5xx/network errors."""
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
                _upsert_fanart_cache(col, normalized_kind, str_id, data, has_art, ttl)
            except Exception as ex:
                logging.warning("Failed to update fanart_meta_cache for %s/%s: %s", normalized_kind, str_id, ex)
            return data

        if resp.status_code == 404:
            try:
                _upsert_fanart_cache(col, normalized_kind, str_id, {}, False, timedelta(days=1))
            except Exception as ex:
                logging.warning("Failed to update 404 fanart_meta_cache for %s/%s: %s", normalized_kind, str_id, ex)
            return {}

        if resp.status_code in (429, 500, 502, 503, 504):
            logging.warning(
                "Fanart.tv returned %s for %s/%s, falling back to stale cache if present",
                resp.status_code,
                normalized_kind,
                str_id,
            )
            return None

    except Exception as e:
        logging.warning("Failed to fetch metadata from Fanart.tv (%s/%s): %s", normalized_kind, str_id, e)
        return None

    return {}


def _get_or_start_fanart_task(
    key: tuple,
    col,
    normalized_kind: str,
    str_id: str,
    server_key: str,
    user_key: str,
) -> asyncio.Task:
    loop = asyncio.get_running_loop()
    existing = _inflight.get(key)
    if existing is not None and not existing.done() and existing.get_loop() is loop:
        return existing

    task = loop.create_task(_fetch_fanart_remote(col, normalized_kind, str_id, server_key, user_key))
    _inflight[key] = task
    _background_tasks.add(task)

    def _on_done(done_task: asyncio.Task, k: tuple = key) -> None:
        if _inflight.get(k) is done_task:
            _inflight.pop(k, None)
        _background_tasks.discard(done_task)
        if not done_task.cancelled():
            try:
                done_task.exception()
            except Exception:
                pass

    task.add_done_callback(_on_done)
    return task


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
    - 7-day TTL for successful hits, 24-hour TTL for misses, 30-day physical purge retention
    - Stale-while-revalidate when expires_at <= now < purge_at
    - Stale cache fallback on upstream 429/5xx errors
    - Single-flight coalescing via _inflight with asyncio.shield protection
    - Never leaks personal or server API keys into logs or URLs
    """
    if not ext_id or str(ext_id).strip() == "":
        return {}

    normalized_kind = "movies" if kind in ("movie", "movies") else "tv"
    str_id = str(ext_id).strip()

    from app.services.db import db
    col = db.get_collection("fanart_meta_cache")
    now = datetime.now(UTC).replace(tzinfo=None)

    server_key = (Config.FANART_API_KEY or "").strip()
    user_key = (client_key or "").strip()
    has_keys = bool(server_key or user_key)
    inflight_key = (normalized_kind, str_id, user_key)

    stale_data = None
    try:
        cached = col.find_one({"kind": normalized_kind, "ext_id": str_id})
        if cached:
            exp = _to_naive_utc(cached.get("expires_at"))
            purge_at = _to_naive_utc(cached.get("purge_at"))
            cached_data = cached.get("data") if isinstance(cached.get("data"), dict) else {}

            if exp and exp > now:
                return cached_data

            stale_data = cached_data
            if purge_at and now < purge_at:
                if has_keys:
                    _get_or_start_fanart_task(inflight_key, col, normalized_kind, str_id, server_key, user_key)
                return stale_data
    except Exception as e:
        logging.warning("Error reading fanart_meta_cache for %s/%s: %s", normalized_kind, str_id, e)

    if not has_keys:
        return stale_data if stale_data is not None else {}

    task = _get_or_start_fanart_task(inflight_key, col, normalized_kind, str_id, server_key, user_key)
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=_INFLIGHT_TIMEOUT_SECONDS)
    except TimeoutError:
        logging.warning("Timed out waiting for Fanart.tv (%s/%s), falling back to stale cache if present", normalized_kind, str_id)
        return stale_data if stale_data is not None else {}

    if result is None:
        return stale_data if stale_data is not None else {}
    return result


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

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.services.http import get_client
from config import Config

JIKAN_PRIMARY_URL = getattr(Config, "JIKAN_PRIMARY_URL", "https://jikanfortheweebs.midnightignite.me/v4")
JIKAN_FALLBACK_URL = getattr(Config, "JIKAN_FALLBACK_URL", "https://api.jikan.moe/v4")

_jikan_semaphore: Optional[asyncio.Semaphore] = None


def get_jikan_semaphore() -> asyncio.Semaphore:
    global _jikan_semaphore
    if _jikan_semaphore is None:
        custom_url = getattr(Config, "CUSTOM_JIKAN_URL", "").strip()
        limit = 15 if custom_url else 3
        _jikan_semaphore = asyncio.Semaphore(limit)
    return _jikan_semaphore


def get_jikan_endpoints() -> List[str]:
    """Build an ordered list of Jikan API endpoints.

    If CUSTOM_JIKAN_URL (or JIKAN_API_URL) env var is set, it becomes Primary (Tier 1).
    Followed by MidnightIgnite instance (Tier 2) and official Jikan API (Tier 3).
    """
    endpoints = []
    custom_url = getattr(Config, "CUSTOM_JIKAN_URL", "").strip()
    if custom_url:
        endpoints.append(custom_url)
    endpoints.append(JIKAN_PRIMARY_URL)
    endpoints.append(JIKAN_FALLBACK_URL)

    seen = set()
    unique_endpoints = []
    for ep in endpoints:
        cleaned = ep.rstrip("/")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique_endpoints.append(cleaned)

    return unique_endpoints


async def _jikan_request(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Execute a rate-limited request to Jikan API using ordered fallback endpoints."""
    clean_endpoint = endpoint.lstrip("/")
    endpoints = get_jikan_endpoints()

    sem = get_jikan_semaphore()
    client = get_client()

    async with sem:
        for idx, base_url in enumerate(endpoints):
            full_url = f"{base_url}/{clean_endpoint}"
            try:
                resp = await client.get(full_url, params=params, timeout=3.5)
                if resp.status_code == 200:
                    data_json = resp.json()
                    # If endpoint returns valid data or is not a data-list response, return it
                    if data_json and (data_json.get("data") or "data" not in data_json):
                        items = data_json.get("data")
                        if isinstance(items, list) and len(items) < 15 and idx < len(endpoints) - 1:
                            logging.warning(
                                "Jikan endpoint %s returned sparse data list (%s items) for %s, trying next fallback...",
                                base_url,
                                len(items),
                                full_url,
                            )
                            continue
                        return data_json
                    elif idx < len(endpoints) - 1:
                        logging.warning(
                            "Jikan endpoint %s returned empty data list for %s, trying next fallback...",
                            base_url,
                            full_url,
                        )
                        continue
                    return data_json
                elif resp.status_code == 404:
                    logging.warning("Jikan endpoint %s returned 404 for %s", base_url, full_url)
                    return None
                else:
                    logging.warning(
                        "Jikan endpoint %s returned status %s for %s, trying next fallback...",
                        base_url,
                        resp.status_code,
                        full_url,
                    )
            except Exception as e:
                logging.warning(
                    "Jikan endpoint %s exception for %s: %s, trying next fallback...",
                    base_url,
                    full_url,
                    e,
                )

    return None


async def get_anime_by_id(mal_id: int | str) -> Optional[Dict[str, Any]]:
    """Fetch anime metadata by MAL ID."""
    res = await _jikan_request(f"/anime/{mal_id}")
    return res.get("data") if res else None


async def get_anime_episodes(mal_id: int | str, page: int = 1) -> Optional[List[Dict[str, Any]]]:
    """Fetch episode listing for anime by MAL ID."""
    res = await _jikan_request(f"/anime/{mal_id}/episodes", params={"page": page})
    return res.get("data") if res else None


async def get_anime_recommendations(mal_id: int | str) -> Optional[List[Dict[str, Any]]]:
    """Fetch community recommendations for anime by MAL ID."""
    res = await _jikan_request(f"/anime/{mal_id}/recommendations")
    return res.get("data") if res else None


async def get_seasonal_anime(year: int, season: str, page: int = 1) -> Optional[List[Dict[str, Any]]]:
    """Fetch seasonal anime list (e.g. year=2026, season='winter')."""
    res = await _jikan_request(f"/seasons/{year}/{season.lower()}", params={"page": page})
    return res.get("data") if res else None


async def get_season_now(page: int = 1) -> Optional[List[Dict[str, Any]]]:
    """Fetch current season airing anime list (/seasons/now)."""
    res = await _jikan_request("/seasons/now", params={"page": page})
    return res.get("data") if res else None


async def get_upcoming_anime(page: int = 1) -> Optional[List[Dict[str, Any]]]:
    """Fetch upcoming upcoming seasonal anime."""
    res = await _jikan_request("/seasons/upcoming", params={"page": page})
    return res.get("data") if res else None


async def get_airing_schedule(filter_day: Optional[str] = None, page: int = 1) -> Optional[List[Dict[str, Any]]]:
    """Fetch broadcast schedule for a given day (e.g. filter_day='monday')."""
    params: Dict[str, Any] = {"page": page}
    if filter_day:
        params["filter"] = filter_day.lower()
    res = await _jikan_request("/schedules", params=params)
    return res.get("data") if res else None


async def get_top_anime(
    type_filter: Optional[str] = None, page: int = 1, filter_by: Optional[str] = None
) -> Optional[List[Dict[str, Any]]]:
    """Fetch top anime (e.g. type_filter='movie', 'tv'; filter_by='bypopularity', 'airing', 'favorite')."""
    params: Dict[str, Any] = {"page": page}
    if type_filter:
        params["type"] = type_filter.lower()
    if filter_by:
        params["filter"] = filter_by.lower()
    res = await _jikan_request("/top/anime", params=params)
    return res.get("data") if res else None

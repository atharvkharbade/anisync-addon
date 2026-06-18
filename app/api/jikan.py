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
        _jikan_semaphore = asyncio.Semaphore(3)
    return _jikan_semaphore


async def _jikan_request(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Execute a rate-limited request to Jikan API.

    Tries Primary endpoint first (midnightignite), then falls back to official Jikan API if primary is rate limited (429) or fails.
    """
    clean_endpoint = endpoint.lstrip("/")
    primary_url = f"{JIKAN_PRIMARY_URL.rstrip('/')}/{clean_endpoint}"
    fallback_url = f"{JIKAN_FALLBACK_URL.rstrip('/')}/{clean_endpoint}"

    sem = get_jikan_semaphore()
    client = get_client()

    async with sem:
        # 1. Try Primary Endpoint (Midnight)
        try:
            resp = await client.get(primary_url, params=params, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                logging.warning("Jikan Primary 404 for %s", primary_url)
                return None
            else:
                logging.warning("Jikan Primary returned status %s for %s, falling back to official Jikan...", resp.status_code, primary_url)
        except Exception as e:
            logging.warning("Jikan Primary request exception for %s: %s, falling back to official Jikan...", primary_url, e)

        # 2. Try Fallback Endpoint (Official api.jikan.moe)
        try:
            logging.info("Executing Jikan fallback request to %s", fallback_url)
            resp = await client.get(fallback_url, params=params, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                return None
            elif resp.status_code == 429:
                logging.warning("Jikan Official API 429 rate limit hit on %s", fallback_url)
                await asyncio.sleep(1.0)
            else:
                logging.warning("Jikan Official API returned status %s for %s", resp.status_code, fallback_url)
        except Exception as ex:
            logging.error("Jikan Official API fallback exception for %s: %s", fallback_url, ex)

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


async def get_top_anime(type_filter: Optional[str] = None, page: int = 1) -> Optional[List[Dict[str, Any]]]:
    """Fetch top anime (e.g. type_filter='movie', 'ova', 'tv')."""
    params: Dict[str, Any] = {"page": page}
    if type_filter:
        params["type"] = type_filter.lower()
    res = await _jikan_request("/top/anime", params=params)
    return res.get("data") if res else None

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.services.http import get_client
from config import Config

JIKAN_BASE_URL = os_jikan_url = getattr(Config, "JIKAN_API_URL", "https://jikanfortheweebs.midnightignite.me/v4")

_jikan_semaphore: Optional[asyncio.Semaphore] = None


def get_jikan_semaphore() -> asyncio.Semaphore:
    global _jikan_semaphore
    if _jikan_semaphore is None:
        _jikan_semaphore = asyncio.Semaphore(3)
    return _jikan_semaphore


async def _jikan_request(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Execute a rate-limited request to the Jikan API with exponential backoff retries."""
    url = f"{JIKAN_BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    sem = get_jikan_semaphore()
    client = get_client()

    retries = 3
    backoff = 1.0

    async with sem:
        for attempt in range(retries):
            try:
                resp = await client.get(url, params=params, timeout=12.0)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 404:
                    logging.warning("Jikan 404 for %s", url)
                    return None
                elif resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    sleep_time = float(retry_after) if retry_after else backoff
                    logging.warning("Jikan 429 rate limit hit on %s, sleeping %.1fs...", url, sleep_time)
                    await asyncio.sleep(sleep_time)
                    backoff *= 1.5
                else:
                    logging.warning("Jikan returned status %s for %s", resp.status_code, url)
                    await asyncio.sleep(backoff)
                    backoff *= 1.5
            except Exception as e:
                logging.error("Jikan request exception (attempt %s/%s) for %s: %s", attempt + 1, retries, url, e)
                await asyncio.sleep(backoff)
                backoff *= 1.5

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

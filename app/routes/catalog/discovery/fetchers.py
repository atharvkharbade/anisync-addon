import asyncio
import json as _json
import logging
import urllib.request

from app.api.jikan import get_airing_schedule, get_season_now, get_top_anime


def _fetch_kitsu_sync(query_str: str) -> list:
    try:
        url = f"https://kitsu.io/api/edge/anime?{query_str}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/vnd.api+json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
            if isinstance(data, dict):
                return data.get("data") or []
    except Exception as ex:
        logging.warning("Kitsu discovery fetch error (%s): %s", query_str, ex)
    return []


async def fetch_kitsu_discovery(query_str: str) -> list:
    return await asyncio.to_thread(_fetch_kitsu_sync, query_str)


def _safe_list(val) -> list:
    return val if isinstance(val, list) else []


async def fetch_supplemental_discovery_data() -> dict:
    """Fetches supplemental discovery data from Jikan and Kitsu concurrently."""
    try:
        jikan_pop_task = asyncio.create_task(get_top_anime(filter_by="bypopularity", page=1))
        jikan_airing_task = asyncio.create_task(get_top_anime(filter_by="airing", page=1))
        jikan_top_task = asyncio.create_task(get_top_anime(page=1))
        jikan_movie_task = asyncio.create_task(get_top_anime(type_filter="movie", page=1))
        jikan_season_task = asyncio.create_task(get_season_now(page=1))
        jikan_schedule_task = asyncio.create_task(get_airing_schedule(page=1))
        jikan_fav_task = asyncio.create_task(get_top_anime(filter_by="favorite", page=1))

        kitsu_pop_task = asyncio.create_task(fetch_kitsu_discovery("sort=-userCount&page%5Blimit%5D=25"))
        kitsu_rating_task = asyncio.create_task(fetch_kitsu_discovery("sort=-averageRating&page%5Blimit%5D=25"))
        kitsu_airing_task = asyncio.create_task(
            fetch_kitsu_discovery("filter%5Bstatus%5D=current&sort=-userCount&page%5Blimit%5D=25")
        )
        kitsu_season_task = asyncio.create_task(
            fetch_kitsu_discovery("filter%5Bstatus%5D=current&sort=-createdAt&page%5Blimit%5D=25")
        )
        kitsu_movie_task = asyncio.create_task(
            fetch_kitsu_discovery("filter%5Bsubtype%5D=movie&sort=-averageRating&page%5Blimit%5D=25")
        )

        (
            jikan_pop,
            jikan_airing,
            jikan_top,
            jikan_movies,
            jikan_season_now,
            jikan_schedule,
            jikan_fav,
            kitsu_pop,
            kitsu_rating,
            kitsu_airing,
            kitsu_season,
            kitsu_movie,
        ) = await asyncio.gather(
            jikan_pop_task,
            jikan_airing_task,
            jikan_top_task,
            jikan_movie_task,
            jikan_season_now,
            jikan_schedule_task,
            jikan_fav_task,
            kitsu_pop_task,
            kitsu_rating_task,
            kitsu_airing_task,
            kitsu_season_task,
            kitsu_movie_task,
            return_exceptions=True,
        )
    except Exception as ex:
        logging.warning("Error fetching supplemental discovery data: %s", ex)
        jikan_pop = []
        jikan_airing = []
        jikan_top = []
        jikan_movies = []
        jikan_season_now = []
        jikan_schedule = []
        jikan_fav = []
        kitsu_pop = []
        kitsu_rating = []
        kitsu_airing = []
        kitsu_season = []
        kitsu_movie = []

    return {
        "jikan_pop": _safe_list(jikan_pop),
        "jikan_airing": _safe_list(jikan_airing),
        "jikan_top": _safe_list(jikan_top),
        "jikan_movies": _safe_list(jikan_movies),
        "jikan_season_now": _safe_list(jikan_season_now),
        "jikan_schedule": _safe_list(jikan_schedule),
        "jikan_fav": _safe_list(jikan_fav),
        "kitsu_pop": _safe_list(kitsu_pop),
        "kitsu_rating": _safe_list(kitsu_rating),
        "kitsu_airing": _safe_list(kitsu_airing),
        "kitsu_season": _safe_list(kitsu_season),
        "kitsu_movie": _safe_list(kitsu_movie),
    }

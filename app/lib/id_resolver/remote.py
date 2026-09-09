import logging

import httpx

from app.services.db import cache_ids, db
from app.services.http import get_client

from .constants import ANIZP_API, ARM_API, TIMEOUT
from .fribb import ensure_fribb_mappings


async def _try_arm(client: httpx.AsyncClient, kitsu_id: str) -> tuple[str | None, str | None]:
    resp = await client.get(
        ARM_API,
        params={"source": "kitsu", "id": kitsu_id, "include": "anilist,myanimelist"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None, None
    mal_id = str(data["myanimelist"]) if data.get("myanimelist") else None
    anilist_id = str(data["anilist"]) if data.get("anilist") else None
    if mal_id or anilist_id:
        return mal_id, anilist_id
    return None, None


async def _try_anizp(client: httpx.AsyncClient, kitsu_id: str) -> tuple[str | None, str | None]:
    resp = await client.get(ANIZP_API, params={"kitsu_id": kitsu_id}, timeout=TIMEOUT)
    resp.raise_for_status()
    mappings = resp.json().get("mappings", {})
    mal_id = str(mappings["mal_id"]) if mappings.get("mal_id") else None
    anilist_id = str(mappings["anilist_id"]) if mappings.get("anilist_id") else None
    imdb_id = str(mappings.get("imdb_id")) if mappings.get("imdb_id") else None
    tmdb_id = str(mappings.get("themoviedb_id")) if mappings.get("themoviedb_id") else None
    tvdb_id = str(mappings.get("thetvdb_id")) if mappings.get("thetvdb_id") else None
    if mal_id or anilist_id or imdb_id or tmdb_id or tvdb_id:
        cache_ids(
            kitsu_id=kitsu_id, mal_id=mal_id, anilist_id=anilist_id, imdb_id=imdb_id, tmdb_id=tmdb_id, tvdb_id=tvdb_id
        )
        return mal_id, anilist_id
    return None, None


async def _try_fribb(client: httpx.AsyncClient, kitsu_id: str) -> tuple[str | None, str | None]:
    await ensure_fribb_mappings(client)
    try:
        kid = int(kitsu_id)
        doc = db.fribb_mappings.find_one({"kitsu_id": kid})
        if doc:
            mal_id = doc.get("mal_id")
            anilist_id = doc.get("anilist_id")
            return mal_id, anilist_id
    except Exception as e:
        logging.error("Error looking up kitsu_id in Fribb mappings: %s", e)
    return None, None


async def search_kitsu_by_title(title: str) -> str | None:
    """
    Searches Kitsu by title and returns the best matching kitsu_id.
    """
    if not title:
        return None
    try:
        url = "https://kitsu.io/api/edge/anime"
        params = {"filter[text]": title, "page[limit]": 5}
        headers = {
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
        }
        client = get_client()
        resp = await client.get(url, params=params, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                kitsu_id = str(data[0]["id"])
                logging.info("Found kitsu_id=%s via Kitsu title search for '%s'", kitsu_id, title)
                return kitsu_id
    except Exception as e:
        logging.warning("Kitsu title search failed for '%s': %s", title, e)
    return None


async def fetch_anime_info_by_mal_id(mal_id: str) -> tuple[str | None, str | None]:
    """
    Queries AniList GraphQL using idMal to retrieve the title and AniList ID.
    Also falls back to public MAL API V2 if AniList GraphQL query fails.
    Returns (title, anilist_id).
    """
    # 1. Query AniList GraphQL (Generous rate limits, returns both title and AniList ID)
    query = """
    query ($idMal: Int) {
      Media(idMal: $idMal, type: ANIME) {
        id
        title {
          english
          userPreferred
          romaji
        }
      }
    }
    """
    try:
        from app.api.anilist import _gql

        data = await _gql(None, query, {"idMal": int(mal_id)})
        media = data.get("data", {}).get("Media", {})
        if media:
            anilist_id = str(media["id"])
            titles = media.get("title", {})
            title = titles.get("english") or titles.get("userPreferred") or titles.get("romaji")
            return title, anilist_id
    except Exception as e:
        logging.warning("fetch_anime_info_by_mal_id: AniList query failed for mal_id=%s: %s", mal_id, e)

    # 2. Fallback to MyAnimeList Public API if Client ID is configured
    from config import Config

    if Config.MAL_CLIENT_ID:
        try:
            client = get_client()
            resp = await client.get(
                f"{Config.MAL_API_URL}/anime/{mal_id}",
                headers={"X-MAL-CLIENT-ID": Config.MAL_CLIENT_ID},
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                title = data.get("title")
                return title, None
        except Exception as e:
            logging.warning("fetch_anime_info_by_mal_id: public MAL fetch failed for mal_id=%s: %s", mal_id, e)

    return None, None


async def fetch_anime_info_by_anilist_id(anilist_id: str) -> tuple[str | None, str | None]:
    """
    Queries AniList GraphQL using id to retrieve the title and MAL ID.
    Returns (title, mal_id).
    """
    query = """
    query ($id: Int) {
      Media(id: $id, type: ANIME) {
        idMal
        title {
          english
          userPreferred
          romaji
        }
      }
    }
    """
    try:
        from app.api.anilist import _gql

        data = await _gql(None, query, {"id": int(anilist_id)})
        media = data.get("data", {}).get("Media", {})
        if media:
            mal_id = str(media["idMal"]) if media.get("idMal") else None
            titles = media.get("title", {})
            title = titles.get("english") or titles.get("userPreferred") or titles.get("romaji")
            return title, mal_id
    except Exception as e:
        logging.warning("fetch_anime_info_by_anilist_id: AniList query failed for anilist_id=%s: %s", anilist_id, e)

    return None, None

import logging
from typing import Optional

import httpx

from app.services.db import cache_ids, get_cached_ids, get_cached_ids_by_mal, get_cached_ids_by_anilist

ARM_API    = "https://arm.haglund.dev/api/v2/ids"
ANIZP_API  = "https://api.ani.zip/mappings"
FRIBB_API  = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
TIMEOUT    = 8


async def _try_arm(client: httpx.AsyncClient, kitsu_id: str) -> tuple[Optional[str], Optional[str]]:
    resp = await client.get(
        ARM_API,
        params={"source": "kitsu", "id": kitsu_id, "include": "anilist,myanimelist"},
    )
    resp.raise_for_status()
    data = resp.json()
    mal_id     = str(data["myanimelist"]) if data.get("myanimelist") else None
    anilist_id = str(data["anilist"])     if data.get("anilist")     else None
    if mal_id or anilist_id:
        return mal_id, anilist_id
    return None, None


async def _try_anizp(client: httpx.AsyncClient, kitsu_id: str) -> tuple[Optional[str], Optional[str]]:
    resp = await client.get(ANIZP_API, params={"kitsu_id": kitsu_id})
    resp.raise_for_status()
    mappings   = resp.json().get("mappings", {})
    mal_id     = str(mappings["mal_id"])     if mappings.get("mal_id")     else None
    anilist_id = str(mappings["anilist_id"]) if mappings.get("anilist_id") else None
    if mal_id or anilist_id:
        return mal_id, anilist_id
    return None, None


async def _try_fribb(client: httpx.AsyncClient, kitsu_id: str) -> tuple[Optional[str], Optional[str]]:
    resp = await client.get(FRIBB_API)
    resp.raise_for_status()
    kid = int(kitsu_id)
    for entry in resp.json():
        if entry.get("kitsu_id") == kid:
            mal_id     = str(entry["mal_id"])     if entry.get("mal_id")     else None
            anilist_id = str(entry["anilist_id"]) if entry.get("anilist_id") else None
            if mal_id or anilist_id:
                return mal_id, anilist_id
    return None, None


async def resolve(kitsu_id: str) -> tuple[Optional[str], Optional[str]]:
    """
    Returns (mal_id, anilist_id) for a given kitsu_id.
    Checks MongoDB cache first, then tries APIs in order:
      1. ARM (arm.haglund.dev)   — primary
      2. ani.zip (api.ani.zip)   — fallback 1
      3. Fribb  (GitHub raw)     — fallback 2
    """
    cached = get_cached_ids(kitsu_id)
    if cached:
        return cached.get("mal_id"), cached.get("anilist_id")

    resolvers = [
        ("ARM",    _try_arm),
        ("ani.zip", _try_anizp),
        ("Fribb",  _try_fribb),
    ]

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for name, fn in resolvers:
            try:
                mal_id, anilist_id = await fn(client, kitsu_id)
                if mal_id or anilist_id:
                    logging.info("Resolved via %s: kitsu=%s → mal=%s anilist=%s", name, kitsu_id, mal_id, anilist_id)
                    cache_ids(kitsu_id, mal_id, anilist_id)
                    return mal_id, anilist_id
                else:
                    logging.warning("%s: no data for kitsu_id=%s, trying next", name, kitsu_id)
            except Exception as e:
                logging.warning("%s failed for kitsu_id=%s: %s, trying next", name, kitsu_id, e)

    logging.error("All resolvers failed for kitsu_id=%s", kitsu_id)
    return None, None


async def search_kitsu_by_title(title: str) -> Optional[str]:
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
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    kitsu_id = str(data[0]["id"])
                    logging.info("Found kitsu_id=%s via Kitsu title search for '%s'", kitsu_id, title)
                    return kitsu_id
    except Exception as e:
        logging.warning("Kitsu title search failed for '%s': %s", title, e)
    return None


async def fetch_anime_info_by_mal_id(mal_id: str) -> tuple[Optional[str], Optional[str]]:
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
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                "https://graphql.anilist.co",
                json={"query": query, "variables": {"idMal": int(mal_id)}}
            )
            if resp.status_code == 200:
                data = resp.json()
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
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(
                    f"https://api.myanimelist.net/v2/anime/{mal_id}",
                    headers={"X-MAL-CLIENT-ID": Config.MAL_CLIENT_ID},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    title = data.get("title")
                    return title, None
        except Exception as e:
            logging.warning("fetch_anime_info_by_mal_id: public MAL fetch failed for mal_id=%s: %s", mal_id, e)

    return None, None


async def fetch_anime_info_by_anilist_id(anilist_id: str) -> tuple[Optional[str], Optional[str]]:
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
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                "https://graphql.anilist.co",
                json={"query": query, "variables": {"id": int(anilist_id)}}
            )
            if resp.status_code == 200:
                data = resp.json()
                media = data.get("data", {}).get("Media", {})
                if media:
                    mal_id = str(media["idMal"]) if media.get("idMal") else None
                    titles = media.get("title", {})
                    title = titles.get("english") or titles.get("userPreferred") or titles.get("romaji")
                    return title, mal_id
    except Exception as e:
        logging.warning("fetch_anime_info_by_anilist_id: AniList query failed for anilist_id=%s: %s", anilist_id, e)

    return None, None


async def resolve_mal_to_kitsu(mal_id: str) -> Optional[str]:
    """
    Returns kitsu_id (str) for a given mal_id.
    Checks MongoDB cache first, then tries APIs in order:
      1. ARM (arm.haglund.dev)
      2. Fribb (GitHub raw)
      3. Title-based fallback (AniList title lookup -> Kitsu text search)
    """
    if not mal_id:
        return None
    cached = get_cached_ids_by_mal(mal_id)
    if cached and cached.get("kitsu_id"):
        return str(cached["kitsu_id"])

    # Try ARM API
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                ARM_API,
                params={"source": "myanimelist", "id": mal_id, "include": "kitsu,anilist"},
            )
            if resp.status_code == 200:
                data = resp.json()
                kitsu_id = str(data["kitsu"]) if data.get("kitsu") else None
                anilist_id = str(data["anilist"]) if data.get("anilist") else None
                if kitsu_id:
                    cache_ids(kitsu_id, mal_id, anilist_id)
                    return kitsu_id
    except Exception as e:
        logging.warning("ARM mal->kitsu failed for mal_id=%s: %s", mal_id, e)

    # Try Fribb API
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(FRIBB_API)
            if resp.status_code == 200:
                mid = int(mal_id)
                for entry in resp.json():
                    if entry.get("mal_id") == mid:
                        kitsu_id = str(entry["kitsu_id"]) if entry.get("kitsu_id") else None
                        anilist_id = str(entry["anilist_id"]) if entry.get("anilist_id") else None
                        if kitsu_id:
                            cache_ids(kitsu_id, mal_id, anilist_id)
                            return kitsu_id
    except Exception as e:
        logging.warning("Fribb mal->kitsu failed for mal_id=%s: %s", mal_id, e)

    # Try Title-based fallback
    try:
        title, anilist_id = await fetch_anime_info_by_mal_id(mal_id)
        if title:
            kitsu_id = await search_kitsu_by_title(title)
            if kitsu_id:
                cache_ids(kitsu_id, mal_id, anilist_id)
                return kitsu_id
    except Exception as e:
        logging.warning("Title fallback failed for mal_id=%s: %s", mal_id, e)

    return None


async def resolve_anilist_to_kitsu(anilist_id: str) -> Optional[str]:
    """
    Returns kitsu_id (str) for a given anilist_id.
    Checks MongoDB cache first, then tries APIs in order:
      1. ARM (arm.haglund.dev)
      2. ani.zip (api.ani.zip)
      3. Fribb (GitHub raw)
      4. Title-based fallback (AniList title lookup -> Kitsu text search)
    """
    if not anilist_id:
        return None
    cached = get_cached_ids_by_anilist(anilist_id)
    if cached and cached.get("kitsu_id"):
        return str(cached["kitsu_id"])

    # Try ARM API
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                ARM_API,
                params={"source": "anilist", "id": anilist_id, "include": "kitsu,myanimelist"},
            )
            if resp.status_code == 200:
                data = resp.json()
                kitsu_id = str(data["kitsu"]) if data.get("kitsu") else None
                mal_id = str(data["myanimelist"]) if data.get("myanimelist") else None
                if kitsu_id:
                    cache_ids(kitsu_id, mal_id, anilist_id)
                    return kitsu_id
    except Exception as e:
        logging.warning("ARM anilist->kitsu failed for anilist_id=%s: %s", anilist_id, e)

    # Try ani.zip API
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(ANIZP_API, params={"anilist_id": anilist_id})
            if resp.status_code == 200:
                mappings = resp.json().get("mappings", {})
                kitsu_id = str(mappings["kitsu_id"]) if mappings.get("kitsu_id") else None
                mal_id = str(mappings["mal_id"]) if mappings.get("mal_id") else None
                if kitsu_id:
                    cache_ids(kitsu_id, mal_id, anilist_id)
                    return kitsu_id
    except Exception as e:
        logging.warning("ani.zip anilist->kitsu failed for anilist_id=%s: %s", anilist_id, e)

    # Try Fribb API
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(FRIBB_API)
            if resp.status_code == 200:
                aid = int(anilist_id)
                for entry in resp.json():
                    if entry.get("anilist_id") == aid:
                        kitsu_id = str(entry["kitsu_id"]) if entry.get("kitsu_id") else None
                        mal_id = str(entry["mal_id"]) if entry.get("mal_id") else None
                        if kitsu_id:
                            cache_ids(kitsu_id, mal_id, anilist_id)
                            return kitsu_id
    except Exception as e:
        logging.warning("Fribb anilist->kitsu failed for anilist_id=%s: %s", anilist_id, e)

    # Try Title-based fallback
    try:
        title, mal_id = await fetch_anime_info_by_anilist_id(anilist_id)
        if title:
            kitsu_id = await search_kitsu_by_title(title)
            if kitsu_id:
                cache_ids(kitsu_id, mal_id, anilist_id)
                return kitsu_id
    except Exception as e:
        logging.warning("Title fallback failed for anilist_id=%s: %s", anilist_id, e)

    return None


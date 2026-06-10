import logging
from typing import Optional
from datetime import datetime, timedelta
import asyncio

import httpx

from app.services.http import get_client
from app.services.db import db, cache_ids, get_cached_ids, get_cached_ids_by_mal, get_cached_ids_by_anilist, get_cached_ids_by_simkl

ARM_API    = "https://arm.haglund.dev/api/v2/ids"
ANIZP_API  = "https://api.ani.zip/mappings"
FRIBB_API  = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
TIMEOUT    = 8

_fribb_lock = asyncio.Lock()


async def ensure_fribb_mappings(client: httpx.AsyncClient):
    """
    Ensures that the fribb_mappings collection has been populated and updated
    within the last 24 hours. Does an atomic swap to avoid query disruption.
    """
    now = datetime.utcnow()
    force_rebuild = False
    try:
        # Check if we need to force rebuild to upgrade old schema lacking external IDs
        if db.fribb_mappings.count_documents({}) > 0:
            if db.fribb_mappings.count_documents({"imdb_id": {"$exists": True}}) == 0:
                logging.info("Forcing rebuild of Fribb mappings to upgrade schema (adding imdb_id, tmdb_id, tvdb_id)")
                force_rebuild = True
    except Exception as e:
        logging.error("Failed to check Fribb mappings schema status: %s", e)

    try:
        if not force_rebuild:
            meta = db.fribb_meta.find_one({"key": "last_updated"})
            if meta and (now - meta["timestamp"]) < timedelta(hours=24):
                return
    except Exception as e:
        logging.error("Failed to query Fribb metadata: %s", e)

    async with _fribb_lock:
        # Re-check metadata inside the lock
        try:
            if not force_rebuild:
                meta = db.fribb_meta.find_one({"key": "last_updated"})
                if meta and (now - meta["timestamp"]) < timedelta(hours=24):
                    return
        except Exception:
            pass

        logging.info("Updating local Fribb mappings database cache...")
        try:
            resp = await client.get(FRIBB_API, timeout=15)
            resp.raise_for_status()
            entries = resp.json()

            docs = []
            for entry in entries:
                kitsu_id = entry.get("kitsu_id")
                if kitsu_id is not None:
                    mal_id = entry.get("mal_id")
                    anilist_id = entry.get("anilist_id")
                    simkl_id = entry.get("simkl_id")
                    imdb_id = entry.get("imdb_id")
                    tvdb_id = entry.get("tvdb_id")
                    
                    tmdb_id = None
                    tmdb_obj = entry.get("themoviedb_id")
                    if tmdb_obj:
                        if isinstance(tmdb_obj, dict):
                            tmdb_id = tmdb_obj.get("tv") or tmdb_obj.get("movie")
                        else:
                            tmdb_id = tmdb_obj

                    docs.append({
                        "kitsu_id": int(kitsu_id),
                        "mal_id": str(mal_id) if mal_id is not None else None,
                        "anilist_id": str(anilist_id) if anilist_id is not None else None,
                        "simkl_id": str(simkl_id) if simkl_id is not None else None,
                        "imdb_id": str(imdb_id) if imdb_id else None,
                        "tmdb_id": str(tmdb_id) if tmdb_id is not None else None,
                        "tvdb_id": str(tvdb_id) if tvdb_id is not None else None,
                    })

            if docs:
                temp_coll = db.get_collection("fribb_mappings_temp")
                temp_coll.drop()
                temp_coll.insert_many(docs)
                temp_coll.create_index("kitsu_id")
                temp_coll.create_index("mal_id")
                temp_coll.create_index("anilist_id")
                temp_coll.create_index("simkl_id")
                temp_coll.create_index("imdb_id")
                
                # Swap collections atomically
                temp_coll.rename("fribb_mappings", dropTarget=True)

                db.fribb_meta.update_one(
                    {"key": "last_updated"},
                    {"$set": {"timestamp": datetime.utcnow()}},
                    upsert=True
                )
                logging.info("Successfully updated Fribb mappings with %d entries.", len(docs))
        except Exception as e:
            logging.error("Failed to update Fribb mappings cache: %s", e)


async def _try_arm(client: httpx.AsyncClient, kitsu_id: str) -> tuple[Optional[str], Optional[str]]:
    resp = await client.get(
        ARM_API,
        params={"source": "kitsu", "id": kitsu_id, "include": "anilist,myanimelist"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    mal_id     = str(data["myanimelist"]) if data.get("myanimelist") else None
    anilist_id = str(data["anilist"])     if data.get("anilist")     else None
    if mal_id or anilist_id:
        return mal_id, anilist_id
    return None, None


async def _try_anizp(client: httpx.AsyncClient, kitsu_id: str) -> tuple[Optional[str], Optional[str]]:
    resp = await client.get(ANIZP_API, params={"kitsu_id": kitsu_id}, timeout=TIMEOUT)
    resp.raise_for_status()
    mappings   = resp.json().get("mappings", {})
    mal_id     = str(mappings["mal_id"])     if mappings.get("mal_id")     else None
    anilist_id = str(mappings["anilist_id"]) if mappings.get("anilist_id") else None
    imdb_id    = str(mappings.get("imdb_id")) if mappings.get("imdb_id") else None
    tmdb_id    = str(mappings.get("themoviedb_id")) if mappings.get("themoviedb_id") else None
    tvdb_id    = str(mappings.get("thetvdb_id")) if mappings.get("thetvdb_id") else None
    if mal_id or anilist_id or imdb_id or tmdb_id or tvdb_id:
        cache_ids(
            kitsu_id=kitsu_id,
            mal_id=mal_id,
            anilist_id=anilist_id,
            imdb_id=imdb_id,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id
        )
        return mal_id, anilist_id
    return None, None


async def _try_fribb(client: httpx.AsyncClient, kitsu_id: str) -> tuple[Optional[str], Optional[str]]:
    await ensure_fribb_mappings(client)
    try:
        kid = int(kitsu_id)
        doc = db.fribb_mappings.find_one({"kitsu_id": kid})
        if doc:
            mal_id     = doc.get("mal_id")
            anilist_id = doc.get("anilist_id")
            return mal_id, anilist_id
    except Exception as e:
        logging.error("Error looking up kitsu_id in Fribb mappings: %s", e)
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

    client = get_client()
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
        client = get_client()
        resp = await client.post(
            "https://graphql.anilist.co",
            json={"query": query, "variables": {"idMal": int(mal_id)}},
            timeout=TIMEOUT,
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
            client = get_client()
            resp = await client.get(
                f"https://api.myanimelist.net/v2/anime/{mal_id}",
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
        client = get_client()
        resp = await client.post(
            "https://graphql.anilist.co",
            json={"query": query, "variables": {"id": int(anilist_id)}},
            timeout=TIMEOUT,
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

    client = get_client()

    # Try ARM API
    try:
        resp = await client.get(
            ARM_API,
            params={"source": "myanimelist", "id": mal_id, "include": "kitsu,anilist"},
            timeout=TIMEOUT,
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

    # Try Fribb mappings collection
    try:
        await ensure_fribb_mappings(client)
        doc = db.fribb_mappings.find_one({"mal_id": str(mal_id)})
        if doc:
            kitsu_id = str(doc["kitsu_id"]) if doc.get("kitsu_id") else None
            anilist_id = doc.get("anilist_id")
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

    client = get_client()

    # Try ARM API
    try:
        resp = await client.get(
            ARM_API,
            params={"source": "anilist", "id": anilist_id, "include": "kitsu,myanimelist"},
            timeout=TIMEOUT,
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
        resp = await client.get(ANIZP_API, params={"anilist_id": anilist_id}, timeout=TIMEOUT)
        if resp.status_code == 200:
            mappings = resp.json().get("mappings", {})
            kitsu_id = str(mappings["kitsu_id"]) if mappings.get("kitsu_id") else None
            mal_id = str(mappings["mal_id"]) if mappings.get("mal_id") else None
            imdb_id = str(mappings.get("imdb_id")) if mappings.get("imdb_id") else None
            tmdb_id = str(mappings.get("themoviedb_id")) if mappings.get("themoviedb_id") else None
            tvdb_id = str(mappings.get("thetvdb_id")) if mappings.get("thetvdb_id") else None
            if kitsu_id:
                cache_ids(
                    kitsu_id=kitsu_id,
                    mal_id=mal_id,
                    anilist_id=anilist_id,
                    imdb_id=imdb_id,
                    tmdb_id=tmdb_id,
                    tvdb_id=tvdb_id
                )
                return kitsu_id
    except Exception as e:
        logging.warning("ani.zip anilist->kitsu failed for anilist_id=%s: %s", anilist_id, e)

    # Try Fribb mappings collection
    try:
        await ensure_fribb_mappings(client)
        doc = db.fribb_mappings.find_one({"anilist_id": str(anilist_id)})
        if doc:
            kitsu_id = str(doc["kitsu_id"]) if doc.get("kitsu_id") else None
            mal_id = doc.get("mal_id")
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


async def resolve_simkl_to_kitsu(simkl_id: str) -> Optional[str]:
    """
    Returns kitsu_id (str) for a given simkl_id.
    Checks MongoDB cache first, then queries local watchlist cache, then Simkl API.
    """
    if not simkl_id:
        return None
    cached = get_cached_ids_by_simkl(simkl_id)
    if cached and cached.get("kitsu_id"):
        return str(cached["kitsu_id"])

    # Fallback 1: Local User Watchlist Cache
    try:
        # Search in user_watchlist_cache for any document containing this simkl ID
        simkl_id_int = int(simkl_id) if simkl_id.isdigit() else None
        query_or = [{"data.anime.ids.simkl": simkl_id}, {"data.ids.simkl": simkl_id}]
        if simkl_id_int is not None:
            query_or.extend([
                {"data.anime.ids.simkl": simkl_id_int},
                {"data.ids.simkl": simkl_id_int}
            ])
        
        doc = db.user_watchlist_cache.find_one({
            "tracker": "simkl",
            "$or": query_or
        })
        if doc:
            items = doc.get("data", [])
            for item in items:
                show_obj = item.get("anime") or item.get("show") or item
                ids = show_obj.get("ids") or {}
                if str(ids.get("simkl")) == str(simkl_id):
                    kitsu_id = str(ids.get("kitsu") or "") or None
                    mal_id = str(ids.get("mal") or "") or None
                    anilist_id = str(ids.get("anilist") or "") or None
                    
                    if not kitsu_id:
                        if mal_id:
                            kitsu_id = await resolve_mal_to_kitsu(mal_id)
                        elif anilist_id:
                            kitsu_id = await resolve_anilist_to_kitsu(anilist_id)
                            
                    if kitsu_id:
                        cache_ids(kitsu_id, mal_id, anilist_id, simkl_id)
                        return kitsu_id
    except Exception as e:
        logging.warning("Failed to resolve simkl_id=%s via user watchlist cache: %s", simkl_id, e)

    # Fallback 2: Simkl API lookup
    client = get_client()
    try:
        from config import Config
        url = f"https://api.simkl.com/anime/{simkl_id}"
        params = {"client_id": Config.SIMKL_CLIENT_ID}
        headers = {"User-Agent": "AniSync/1.0"}
        resp = await client.get(url, params=params, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            ids = data.get("ids") or {}
            
            simkl_id_val = str(ids.get("simkl") or simkl_id)
            kitsu_id = str(ids.get("kitsu") or "") or None
            mal_id = str(ids.get("mal") or "") or None
            anilist_id = str(ids.get("anilist") or "") or None
            
            if not kitsu_id:
                if mal_id:
                    kitsu_id = await resolve_mal_to_kitsu(mal_id)
                elif anilist_id:
                    kitsu_id = await resolve_anilist_to_kitsu(anilist_id)
                else:
                    title = data.get("title") or data.get("en_title")
                    if title:
                        kitsu_id = await search_kitsu_by_title(title)
            
            if kitsu_id:
                cache_ids(kitsu_id, mal_id, anilist_id, simkl_id_val)
                return kitsu_id
    except Exception as e:
        logging.warning("Simkl to kitsu resolution failed for simkl_id=%s: %s", simkl_id, e)

    return None


async def bulk_resolve_to_kitsu(mal_ids: list[str] = None, anilist_ids: list[str] = None, simkl_ids: list[str] = None) -> dict:
    """
    Returns a dict mapping (mal_id/anilist_id/simkl_id) -> kitsu_id.
    """
    resolved = {}
    from app.services.db import id_cache_collection, db
    
    # 1. Query id_cache
    query_or = []
    if mal_ids:
        query_or.append({"mal_id": {"$in": [str(x) for x in mal_ids]}})
    if anilist_ids:
        query_or.append({"anilist_id": {"$in": [str(x) for x in anilist_ids]}})
    if simkl_ids:
        query_or.append({"simkl_id": {"$in": [str(x) for x in simkl_ids]}})
        simkl_ints = [int(x) for x in simkl_ids if str(x).isdigit()]
        if simkl_ints:
            query_or.append({"simkl_id": {"$in": simkl_ints}})
            query_or.append({"simkl": {"$in": simkl_ints}})
            
    if not query_or:
        return {}
        
    try:
        docs = list(id_cache_collection.find({"$or": query_or}))
        for doc in docs:
            k_id = str(doc.get("kitsu_id") or "")
            if not k_id:
                continue
            if doc.get("mal_id"):
                resolved[f"mal:{doc['mal_id']}"] = k_id
            if doc.get("anilist_id"):
                resolved[f"anilist:{doc['anilist_id']}"] = k_id
            if doc.get("simkl_id"):
                resolved[f"simkl:{doc['simkl_id']}"] = k_id
            if doc.get("simkl"):
                resolved[f"simkl:{doc['simkl']}"] = k_id
    except Exception as e:
        logging.error("bulk_resolve_to_kitsu: id_cache query failed: %s", e)
        
    # 2. Check fribb_mappings
    unresolved_mal = [x for x in (mal_ids or []) if f"mal:{x}" not in resolved]
    unresolved_al = [x for x in (anilist_ids or []) if f"anilist:{x}" not in resolved]
    
    if unresolved_mal or unresolved_al:
        try:
            fribb_query = []
            if unresolved_mal:
                fribb_query.append({"mal_id": {"$in": [str(x) for x in unresolved_mal]}})
            if unresolved_al:
                fribb_query.append({"anilist_id": {"$in": [str(x) for x in unresolved_al]}})
            if fribb_query:
                fribb_docs = list(db.fribb_mappings.find({"$or": fribb_query}))
                for doc in fribb_docs:
                    k_id = str(doc.get("kitsu_id") or "")
                    if not k_id:
                        continue
                    if doc.get("mal_id"):
                        resolved[f"mal:{doc['mal_id']}"] = k_id
                    if doc.get("anilist_id"):
                        resolved[f"anilist:{doc['anilist_id']}"] = k_id
        except Exception as e:
            logging.error("bulk_resolve_to_kitsu: fribb query failed: %s", e)
            
    # 3. Individual on-the-fly resolution
    remaining_mal = [x for x in (mal_ids or []) if f"mal:{x}" not in resolved]
    remaining_al = [x for x in (anilist_ids or []) if f"anilist:{x}" not in resolved]
    remaining_simkl = [x for x in (simkl_ids or []) if f"simkl:{x}" not in resolved]
    
    if remaining_mal or remaining_al or remaining_simkl:
        tasks = []
        for x in remaining_mal:
            async def resolve_one_mal(val=x):
                try:
                    kid = await resolve_mal_to_kitsu(val)
                    if kid:
                        resolved[f"mal:{val}"] = kid
                except Exception:
                    pass
            tasks.append(resolve_one_mal())
        for x in remaining_al:
            async def resolve_one_al(val=x):
                try:
                    kid = await resolve_anilist_to_kitsu(val)
                    if kid:
                        resolved[f"anilist:{val}"] = kid
                except Exception:
                    pass
            tasks.append(resolve_one_al())
        for x in remaining_simkl:
            async def resolve_one_simkl(val=x):
                try:
                    kid = await resolve_simkl_to_kitsu(val)
                    if kid:
                        resolved[f"simkl:{val}"] = kid
                except Exception:
                    pass
            tasks.append(resolve_one_simkl())
            
        if tasks:
            await asyncio.gather(*tasks)
            
    return resolved

import logging
import asyncio
from typing import Optional
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

async def background_resolve_external_ids(kitsu_id: Optional[str] = None, mal_id: Optional[str] = None, anilist_id: Optional[str] = None):
    """
    Query api.ani.zip in the background and cache external IDs (IMDb, TMDB, TVDB).
    """
    from app.services.db import id_cache_collection, cache_ids
    
    query = {}
    if kitsu_id:
        query["kitsu_id"] = int(kitsu_id)
    elif mal_id:
        query["mal_id"] = str(mal_id)
    elif anilist_id:
        query["anilist_id"] = str(anilist_id)
        
    if not query:
        return
        
    try:
        doc = id_cache_collection.find_one(query)
        if doc and (doc.get("imdb_id") or doc.get("tmdb_id") or doc.get("tvdb_id")):
            return  # Already resolved
    except Exception as e:
        logging.error("Failed to query id_cache in background: %s", e)

    url = "https://api.ani.zip/mappings"
    params = {}
    if anilist_id:
        params["anilist_id"] = anilist_id
    elif mal_id:
        params["mal_id"] = mal_id
    elif kitsu_id:
        params["kitsu_id"] = kitsu_id

    try:
        client = get_client()
        resp = await client.get(url, params=params, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            mappings = data.get("mappings", {})
            k_id = str(mappings.get("kitsu_id") or kitsu_id or "")
            m_id = str(mappings.get("mal_id") or mal_id or "")
            a_id = str(mappings.get("anilist_id") or anilist_id or "")
            imdb_id = str(mappings.get("imdb_id") or "")
            tmdb_id = str(mappings.get("themoviedb_id") or "")
            tvdb_id = str(mappings.get("thetvdb_id") or "")
            
            if k_id:
                cache_ids(
                    kitsu_id=k_id,
                    mal_id=m_id or None,
                    anilist_id=a_id or None,
                    imdb_id=imdb_id or None,
                    tmdb_id=tmdb_id or None,
                    tvdb_id=tvdb_id or None
                )
                logging.info("Cached external IDs for kitsu=%s: imdb=%s tmdb=%s tvdb=%s", k_id, imdb_id, tmdb_id, tvdb_id)
    except Exception as e:
        logging.warning("Failed to background resolve external IDs from ani.zip: %s", e)

def get_rpdb_poster_url(
    user: dict,
    media_type: str,
    kitsu_id: Optional[str] = None,
    mal_id: Optional[str] = None,
    anilist_id: Optional[str] = None,
    simkl_id: Optional[str] = None,
    fallback_poster: Optional[str] = None
) -> Optional[str]:
    """
    Resolve and construct the RPDB poster URL for an item.
    If mappings are missing from the cache, trigger a background task to fetch and cache them.
    """
    if not user:
        return fallback_poster
        
    rpdb_key = user.get("rpdb_api_key")
    if not rpdb_key:
        return fallback_poster
        
    from app.services.db import id_cache_collection
    
    query = []
    if kitsu_id:
        try:
            query.append({"kitsu_id": int(kitsu_id)})
        except (ValueError, TypeError):
            pass
    if mal_id:
        query.append({"mal_id": str(mal_id)})
    if anilist_id:
        query.append({"anilist_id": str(anilist_id)})
    if simkl_id:
        query.append({"simkl_id": str(simkl_id)})
        if str(simkl_id).isdigit():
            query.append({"simkl_id": int(simkl_id)})
            query.append({"simkl": int(simkl_id)})
            
    imdb_id = None
    tmdb_id = None
    tvdb_id = None
    
    if query:
        try:
            doc = id_cache_collection.find_one({"$or": query})
            if doc:
                imdb_id = doc.get("imdb_id")
                tmdb_id = doc.get("tmdb_id")
                tvdb_id = doc.get("tvdb_id")
        except Exception as e:
            logging.error("Failed to query id_cache for RPDB resolution: %s", e)
            
    # Check fribb_mappings next (offline database with 15k+ entries)
    if not (imdb_id or tmdb_id or tvdb_id):
        from app.services.db import db
        fribb_query = []
        if kitsu_id:
            try:
                fribb_query.append({"kitsu_id": int(kitsu_id)})
            except (ValueError, TypeError):
                pass
        if mal_id:
            fribb_query.append({"mal_id": str(mal_id)})
        if anilist_id:
            fribb_query.append({"anilist_id": str(anilist_id)})
        if simkl_id:
            fribb_query.append({"simkl_id": str(simkl_id)})
            if str(simkl_id).isdigit():
                fribb_query.append({"simkl_id": int(simkl_id)})
                
        if fribb_query:
            try:
                doc = db.fribb_mappings.find_one({"$or": fribb_query})
                if doc:
                    imdb_id = doc.get("imdb_id")
                    tmdb_id = doc.get("tmdb_id")
                    tvdb_id = doc.get("tvdb_id")
                    # If we found mappings, write them back to id_cache so they are merged/cached
                    if imdb_id or tmdb_id or tvdb_id:
                        from app.services.db import cache_ids
                        cache_ids(
                            kitsu_id=kitsu_id or doc.get("kitsu_id"),
                            mal_id=mal_id or doc.get("mal_id"),
                            anilist_id=anilist_id or doc.get("anilist_id"),
                            simkl_id=simkl_id or doc.get("simkl_id"),
                            imdb_id=imdb_id,
                            tmdb_id=tmdb_id,
                            tvdb_id=tvdb_id
                        )
            except Exception as e:
                logging.error("Failed to query fribb_mappings for RPDB: %s", e)

    # Trigger background mappings resolution if we still lack external IDs
    if not (imdb_id or tmdb_id or tvdb_id):
        # We need kitsu_id, mal_id, or anilist_id to resolve mappings
        if kitsu_id or mal_id or anilist_id:
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(background_resolve_external_ids(
                        kitsu_id=kitsu_id,
                        mal_id=mal_id,
                        anilist_id=anilist_id
                    ))
            except RuntimeError:
                # No running event loop
                pass
        return fallback_poster

    # Determine media ID format
    id_type = None
    media_id = None
    
    # Priority: IMDb -> TMDB -> TVDB
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
        return fallback_poster
        
    url = f"https://api.ratingposterdb.com/{rpdb_key}/{id_type}/poster-default/{media_id}.jpg"
    
    tier = rpdb_key.split("-")[0].lower()
    lang = user.get("rec_language", "en").split("-")[0].lower()
    
    params = {"fallback": "true"}
    if tier not in ["t0", "t1"] and lang != "en":
        params["lang"] = lang
        
    return f"{url}?{urlencode(params)}"

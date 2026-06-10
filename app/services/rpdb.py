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
    from app.services.db import id_cache_collection, cache_ids, db
    from datetime import datetime, timedelta
    
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
        if doc:
            if doc.get("imdb_id") or doc.get("tmdb_id") or doc.get("tvdb_id"):
                return  # Already resolved
            
            last_attempt = doc.get("last_attempt")
            if last_attempt and (datetime.utcnow() - last_attempt) < timedelta(days=1):
                return  # Throttling repeated requests for unmappable IDs
    except Exception as e:
        logging.error("Failed to query id_cache in background: %s", e)

    k_id = str(kitsu_id) if kitsu_id else ""
    m_id = str(mal_id) if mal_id else ""
    a_id = str(anilist_id) if anilist_id else ""

    # 1. Resolve kitsu_id to mal_id/anilist_id first if we only have kitsu_id
    if k_id and not (m_id or a_id):
        try:
            doc = id_cache_collection.find_one({"kitsu_id": int(k_id)})
            if doc:
                m_id = doc.get("mal_id") or ""
                a_id = doc.get("anilist_id") or ""
        except Exception:
            pass
            
        if not (m_id or a_id):
            from app.lib.id_resolver import resolve
            try:
                m_id_res, a_id_res = await resolve(k_id)
                m_id = str(m_id_res) if m_id_res else ""
                a_id = str(a_id_res) if a_id_res else ""
            except Exception as e:
                logging.warning("Failed to resolve kitsu_id=%s to mal/anilist in background: %s", k_id, e)

    # 2. Conversely, try resolving mal_id/anilist_id to kitsu_id to facilitate relationships tracing if needed
    if m_id and not k_id:
        from app.lib.id_resolver import resolve_mal_to_kitsu
        try:
            k_id_res = await resolve_mal_to_kitsu(m_id)
            k_id = str(k_id_res) if k_id_res else ""
        except Exception:
            pass
    elif a_id and not k_id:
        from app.lib.id_resolver import resolve_anilist_to_kitsu
        try:
            k_id_res = await resolve_anilist_to_kitsu(a_id)
            k_id = str(k_id_res) if k_id_res else ""
        except Exception:
            pass

    imdb_id = ""
    tmdb_id = ""
    tvdb_id = ""

    # 3. Query api.ani.zip mappings endpoint using mal_id or anilist_id (since kitsu_id is not supported)
    url = "https://api.ani.zip/mappings"
    params = {}
    if a_id:
        params["anilist_id"] = a_id
    elif m_id:
        params["mal_id"] = m_id

    if params:
        try:
            client = get_client()
            resp = await client.get(url, params=params, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                mappings = data.get("mappings", {})
                k_id = str(mappings.get("kitsu_id") or k_id or "")
                m_id = str(mappings.get("mal_id") or m_id or "")
                a_id = str(mappings.get("anilist_id") or a_id or "")
                imdb_id = str(mappings.get("imdb_id") or "")
                tmdb_id = str(mappings.get("themoviedb_id") or "")
                tvdb_id = str(mappings.get("thetvdb_id") or "")
        except Exception as e:
            logging.warning("Failed to background resolve external IDs from ani.zip: %s", e)

    # 4. Trace relationships on Kitsu if we still lack external ID mappings
    if not (imdb_id or tmdb_id or tvdb_id) and k_id:
        try:
            client = get_client()
            kitsu_url = f"https://kitsu.io/api/edge/anime/{k_id}/media-relationships?include=destination"
            kitsu_resp = await client.get(kitsu_url, timeout=8)
            if kitsu_resp.status_code == 200:
                rel_data = kitsu_resp.json()
                dest_ids = []
                # 1. Prefer prequel, parent, full_story, etc.
                for rel in rel_data.get("data", []):
                    role = rel.get("attributes", {}).get("role")
                    if role in ["prequel", "parent", "full_story", "alternative_setting", "main_story", "parent_story"]:
                        rel_link = rel.get("relationships", {}).get("destination", {}).get("data", {})
                        if rel_link and rel_link.get("type") == "anime":
                            dest_ids.append(str(rel_link.get("id")))
                # 2. Try alternative roles
                if not dest_ids:
                    for rel in rel_data.get("data", []):
                        rel_link = rel.get("relationships", {}).get("destination", {}).get("data", {})
                        if rel_link and rel_link.get("type") == "anime":
                            dest_ids.append(str(rel_link.get("id")))

                for dest_id in dest_ids:
                    doc = id_cache_collection.find_one({"kitsu_id": int(dest_id)})
                    if not doc:
                        doc = db.fribb_mappings.find_one({"kitsu_id": int(dest_id)})
                    
                    if doc and (doc.get("imdb_id") or doc.get("tmdb_id") or doc.get("tvdb_id")):
                        imdb_id = doc.get("imdb_id") or ""
                        tmdb_id = doc.get("tmdb_id") or ""
                        tvdb_id = doc.get("tvdb_id") or ""
                        logging.info("Resolved external IDs for kitsu=%s via related kitsu=%s: imdb=%s tmdb=%s tvdb=%s", k_id, dest_id, imdb_id, tmdb_id, tvdb_id)
                        break
        except Exception as ex:
            logging.warning("Failed to resolve via Kitsu relationships for kitsu=%s: %s", k_id, ex)

    # 5. Cache the resolved/mapped IDs
    if k_id:
        cache_ids(
            kitsu_id=k_id,
            mal_id=m_id or None,
            anilist_id=a_id or None,
            imdb_id=imdb_id or None,
            tmdb_id=tmdb_id or None,
            tvdb_id=tvdb_id or None
        )
        try:
            id_cache_collection.update_one(
                {"kitsu_id": int(k_id)},
                {"$set": {"last_attempt": datetime.utcnow()}}
            )
        except Exception:
            pass
        logging.info("Cached external IDs for kitsu=%s: imdb=%s tmdb=%s tvdb=%s", k_id, imdb_id, tmdb_id, tvdb_id)

def check_rpdb_key_validity_background(user_id: str, rpdb_key: str):
    """
    Validate the RPDB key in the background and update the user's validation status.
    """
    import asyncio
    async def task():
        from app.services.db import get_user, store_user
        from datetime import datetime
        is_valid = await validate_rpdb_api_key(rpdb_key)
        
        user = get_user(user_id)
        if user and user.get("rpdb_api_key") == rpdb_key:
            user["rpdb_key_valid"] = is_valid
            user["rpdb_key_last_checked"] = datetime.utcnow()
            store_user(user)
            
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            loop.create_task(task())
    except RuntimeError:
        pass


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
        
    # Check if key validation status is cached as invalid
    if user.get("rpdb_key_valid") is False:
        return fallback_poster

    # Periodically re-validate in the background (once every 24 hours)
    from datetime import datetime, timedelta
    last_checked = user.get("rpdb_key_last_checked")
    if not last_checked or (datetime.utcnow() - last_checked) > timedelta(days=1):
        check_rpdb_key_validity_background(user["uid"], rpdb_key)
        
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

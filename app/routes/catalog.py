import urllib.parse
import logging
import asyncio
import time
import datetime
from quart import Blueprint, abort

from app.services.db import get_user
from app.routes.utils import respond_with, is_valid_user_id, rate_limit
from app.api import mal as mal_api
from app.api import anilist as anilist_api
from app.services.http import get_client
from config import Config

catalog_bp = Blueprint("catalog", __name__)


def _parse_stremio_filters(extras: str) -> dict:
    if not extras:
        return {}
    filters = {}
    for part in extras.split("&"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        filters[k] = urllib.parse.unquote(v)
    return filters


async def get_cached_mal_user_anime_list(user_id: str, token: str, status: str) -> list:
    from app.services.db import db
    now = datetime.datetime.utcnow()
    cache_col = db.get_collection("user_watchlist_cache")
    try:
        cached = cache_col.find_one({"uid": user_id, "tracker": "mal", "status": status})
        if cached and cached.get("expires_at") > now:
            return cached["data"]
    except Exception as e:
        logging.error("Failed to query user_watchlist_cache (MAL): %s", e)
        cached = None

    try:
        res = await mal_api.get_user_anime_list(token, status=status, limit=500, offset=0)
        data_items = res.get("data", [])
        try:
            cache_col.update_one(
                {"uid": user_id, "tracker": "mal", "status": status},
                {"$set": {
                    "uid": user_id,
                    "tracker": "mal",
                    "status": status,
                    "data": data_items,
                    "expires_at": now + datetime.timedelta(minutes=5)
                }},
                upsert=True
            )
        except Exception as e:
            logging.error("Failed to write user_watchlist_cache (MAL): %s", e)
        return data_items
    except Exception as e:
        if cached:
            logging.warning("MAL API failed, returning expired cache for user %s: %s", user_id, e)
            return cached["data"]
        raise e


async def get_cached_anilist_user_anime_list(user_id: str, token: str, anilist_uid: int, status: str) -> dict:
    from app.services.db import db
    now = datetime.datetime.utcnow()
    cache_col = db.get_collection("user_watchlist_cache")
    try:
        cached = cache_col.find_one({"uid": user_id, "tracker": "anilist", "status": status})
        if cached and cached.get("expires_at") > now:
            return cached["data"]
    except Exception as e:
        logging.error("Failed to query user_watchlist_cache (AniList): %s", e)
        cached = None

    try:
        collection = await anilist_api.get_user_anime_list(token, user_id=anilist_uid, status=status)
        try:
            cache_col.update_one(
                {"uid": user_id, "tracker": "anilist", "status": status},
                {"$set": {
                    "uid": user_id,
                    "tracker": "anilist",
                    "status": status,
                    "data": collection,
                    "expires_at": now + datetime.timedelta(minutes=5)
                }},
                upsert=True
            )
        except Exception as e:
            logging.error("Failed to write user_watchlist_cache (AniList): %s", e)
        return collection
    except Exception as e:
        if cached:
            logging.warning("AniList API failed, returning expired cache for user %s: %s", user_id, e)
            return cached["data"]
        raise e


async def fetch_anilist_details_in_bulk(mal_ids: list[str]) -> dict:
    if not mal_ids:
        return {}
    from app.services.db import id_cache_collection, db
    try:
        cache_docs = list(id_cache_collection.find({"mal_id": {"$in": mal_ids}}))
        mal_to_anilist = {doc["mal_id"]: str(doc["anilist_id"]) for doc in cache_docs if doc.get("anilist_id")}
    except Exception as e:
        logging.error("Failed to fetch id_cache in bulk: %s", e)
        mal_to_anilist = {}

    # Resolve any uncached MAL IDs on-the-fly concurrently
    uncached_mal_ids = [mid for mid in mal_ids if mid not in mal_to_anilist]
    if uncached_mal_ids:
        logging.info("Resolving %s uncached MAL IDs in bulk: %s", len(uncached_mal_ids), uncached_mal_ids)
        from app.lib.id_resolver import resolve_mal_to_kitsu
        sem = asyncio.Semaphore(15)

        async def resolve_with_sem(mid):
            async with sem:
                try:
                    await resolve_mal_to_kitsu(mid)
                except Exception as ex:
                    logging.warning("Failed to resolve uncached MAL ID %s: %s", mid, ex)

        await asyncio.gather(*[resolve_with_sem(mid) for mid in uncached_mal_ids])

        # Re-fetch cache docs after on-demand resolution
        try:
            cache_docs = list(id_cache_collection.find({"mal_id": {"$in": mal_ids}}))
            mal_to_anilist = {doc["mal_id"]: str(doc["anilist_id"]) for doc in cache_docs if doc.get("anilist_id")}
        except Exception as e:
            logging.error("Failed to re-fetch id_cache in bulk: %s", e)
        
    anilist_ids = list(mal_to_anilist.values())
    if not anilist_ids:
        return {}

    # 1. Query local airing cache
    now = datetime.datetime.utcnow()
    airing_col = db.get_collection("anilist_airing_cache")
    cached_details = {}
    try:
        cached_docs = list(airing_col.find({
            "anilist_id": {"$in": [int(x) for x in anilist_ids]},
            "expires_at": {"$gt": now}
        }))
        for doc in cached_docs:
            cached_details[str(doc["anilist_id"])] = {
                "id": doc["anilist_id"],
                "status": doc.get("status"),
                "nextAiringEpisode": doc.get("nextAiringEpisode")
            }
    except Exception as e:
        logging.error("Failed to read from anilist_airing_cache: %s", e)

    # 2. Determine which IDs need to be fetched
    uncached_anilist_ids = [aid for aid in anilist_ids if aid not in cached_details]

    if uncached_anilist_ids:
        query = """
        query ($ids: [Int]) {
          Page(page: 1, perPage: 50) {
            media(id_in: $ids, type: ANIME) {
              id
              status
              nextAiringEpisode {
                episode
                airingAt
              }
            }
          }
        }
        """
        try:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            chunks = [uncached_anilist_ids[i:i + 50] for i in range(0, len(uncached_anilist_ids), 50)]
            
            async def fetch_chunk(chunk_ids):
                payload = {
                    "query": query,
                    "variables": {"ids": [int(x) for x in chunk_ids]}
                }
                client = get_client()
                resp = await client.post("https://graphql.anilist.co", json=payload, headers=headers, timeout=8)
                if resp.status_code == 200:
                    return resp.json().get("data", {}).get("Page", {}).get("media", [])
                else:
                    logging.error("AniList chunk query failed: %s %s", resp.status_code, resp.text)
                return []
                
            tasks = [fetch_chunk(c) for c in chunks]
            results = await asyncio.gather(*tasks)
            
            media_list = []
            for r in results:
                media_list.extend(r)
                
            for media in media_list:
                aid = media.get("id")
                if not aid:
                    continue
                status = media.get("status", "")
                next_ep = media.get("nextAiringEpisode")
                
                # Expiry calculations:
                if status == "FINISHED":
                    expires_at = now + datetime.timedelta(days=30)
                elif next_ep and next_ep.get("airingAt"):
                    expires_at = datetime.datetime.fromtimestamp(next_ep["airingAt"]) + datetime.timedelta(minutes=15)
                elif status == "NOT_YET_RELEASED":
                    expires_at = now + datetime.timedelta(days=1)
                else:
                    expires_at = now + datetime.timedelta(hours=12)

                try:
                    airing_col.update_one(
                        {"anilist_id": int(aid)},
                        {"$set": {
                            "anilist_id": int(aid),
                            "status": status,
                            "nextAiringEpisode": next_ep,
                            "expires_at": expires_at
                        }},
                        upsert=True
                    )
                except Exception as e:
                    logging.error("Failed to update anilist_airing_cache: %s", e)

                cached_details[str(aid)] = media
        except Exception as e:
            logging.error("Failed bulk AniList query for MAL: %s", e)

    # 3. Map back to MAL IDs
    mal_details = {}
    for mid, aid in mal_to_anilist.items():
        if aid in cached_details:
            mal_details[mid] = cached_details[aid]
    return mal_details


currently_fetching_pairs = set()
currently_fetching_pages = set()
jikan_semaphore = None

def get_jikan_semaphore():
    global jikan_semaphore
    if jikan_semaphore is None:
        jikan_semaphore = asyncio.Semaphore(1)
    return jikan_semaphore

async def background_fetch_and_cache_filler(mal_id: str, episode: int):
    episode = int(episode)
    page = (episode - 1) // 100 + 1
    page_pair = (str(mal_id), page)
    
    if page_pair in currently_fetching_pages:
        currently_fetching_pairs.discard((str(mal_id), episode))
        return
    currently_fetching_pages.add(page_pair)
    
    try:
        async with get_jikan_semaphore():
            await asyncio.sleep(1.0)  # Rate limiting safety sleep
            
            # Check cache again inside the lock
            from app.services.db import get_jikan_filler_cache, set_jikan_filler_cache
            cached = get_jikan_filler_cache(mal_id, episode)
            if cached is not None:
                return
                
            retries = 3
            backoff = 2.0
            success = False
            for attempt in range(retries):
                try:
                    url = f"https://api.jikan.moe/v4/anime/{mal_id}/episodes?page={page}"
                    client = get_client()
                    resp = await client.get(url, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json().get("data", [])
                        for item in data:
                            ep_num = item.get("mal_id")
                            if ep_num:
                                filler = bool(item.get("filler", False))
                                set_jikan_filler_cache(mal_id, ep_num, filler)
                        logging.info("Cached Jikan filler page %s for mal_id=%s (found %s episodes)", page, mal_id, len(data))
                        success = True
                        break
                    elif resp.status_code == 404:
                        logging.warning("Jikan returned 404 for mal_id=%s episodes page %s", mal_id, page)
                        break
                    elif resp.status_code == 429:
                        logging.warning("Jikan 429 rate limit hit on page %s for mal_id=%s, retrying in %s seconds...", page, mal_id, backoff)
                        await asyncio.sleep(backoff)
                        backoff *= 2.0
                    else:
                        logging.error("Jikan returned status %s on page %s for mal_id=%s", resp.status_code, page, mal_id)
                        break
                except Exception as e:
                    logging.error("Jikan background page fetch exception (attempt %s) for mal_id=%s page=%s: %s", attempt + 1, mal_id, page, e)
                    await asyncio.sleep(1.0)
                    
            # If we failed to fetch the page successfully, or if the episode is still not cached,
            # cache it as False to prevent infinite retries.
            if not success or get_jikan_filler_cache(mal_id, episode) is None:
                set_jikan_filler_cache(mal_id, episode, False)
    finally:
        currently_fetching_pages.discard(page_pair)
        currently_fetching_pairs.discard((str(mal_id), episode))


@catalog_bp.route("/<user_id>/catalog/<string:catalog_type>/<string:catalog_id>.json")
@catalog_bp.route("/<user_id>/catalog/<string:catalog_type>/<string:catalog_id>/<path:extras>.json")
@rate_limit(limit=60, period_seconds=60)
async def handle_catalog(user_id: str, catalog_type: str, catalog_id: str, extras: str = ""):
    if not is_valid_user_id(user_id):
        return await respond_with({"metas": []})

    if catalog_id == "anime_tracker_search":
        catalog_id = "anisync_search"

    # We handle 'anime', 'series', and 'movie' catalog types, plus custom tracker types
    allowed_types = ["anime", "series", "movie", "Watching", "Plan to Watch", "Completed", "On Hold", "Dropped", "Planning", "Paused", "Repeating"]
    if catalog_type not in allowed_types:
        return await respond_with({"metas": []})

    user = get_user(user_id)
    if not user:
        logging.warning("Catalog request: Unknown user_id=%s", user_id)
        return await respond_with({"metas": []})

    filters = _parse_stremio_filters(extras)
    offset = int(filters.get("skip", 0))
    search_query = filters.get("search", "")

    metas = []

    # --- Search Catalog ---
    if catalog_id == "anisync_search":
        if not search_query:
            return await respond_with({"metas": []})

        # Check search cache first (expires after 24 hours)
        from app.services.db import db
        now = datetime.datetime.utcnow()
        cache_col = db.get_collection("kitsu_search_cache")
        try:
            cached = cache_col.find_one({"query": search_query, "offset": offset})
            if cached and cached.get("expires_at") > now:
                # Ensure the item type matches the catalog type so Stremio doesn't filter them out
                cached_metas = cached["metas"]
                formatted_metas = []
                for m in cached_metas:
                    m_copy = m.copy()
                    m_copy["type"] = catalog_type
                    formatted_metas.append(m_copy)
                return await respond_with({"metas": formatted_metas})
        except Exception as e:
            logging.error("Failed to query kitsu_search_cache: %s", e)
            cached = None

        # Query Kitsu API directly for fast search results with high rate limits
        try:
            url = "https://kitsu.io/api/edge/anime"
            params = {
                "filter[text]": search_query,
                "page[limit]": 20,
                "page[offset]": offset
            }
            headers = {
                "Accept": "application/vnd.api+json",
                "Content-Type": "application/vnd.api+json",
            }
            client = get_client()
            resp = await client.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for item in data:
                    attrs = item.get("attributes", {})
                    subtype = (attrs.get("subtype") or "tv").lower()
                    item_type = "movie" if subtype == "movie" else "series"

                    titles = attrs.get("titles", {})
                    title = attrs.get("canonicalTitle") or titles.get("en") or titles.get("en_jp") or "Unknown"
                    poster = attrs.get("posterImage", {}).get("large") or attrs.get("posterImage", {}).get("medium") or ""
                    synopsis = attrs.get("synopsis", "")
                    metas.append({
                        "id": f"kitsu:{item['id']}",
                        "type": catalog_type,
                        "name": title,
                        "poster": poster,
                        "description": synopsis[:200] + "..." if len(synopsis) > 200 else synopsis,
                    })

                # Write to search cache
                try:
                    cache_col.update_one(
                        {"query": search_query, "offset": offset},
                        {"$set": {
                            "query": search_query,
                            "offset": offset,
                            "metas": metas,
                            "expires_at": now + datetime.timedelta(hours=24)
                        }},
                        upsert=True
                    )
                except Exception as e:
                    logging.error("Failed to write kitsu_search_cache: %s", e)
        except Exception as e:
            logging.error("Kitsu search query failed: %s", e)
            if cached:
                logging.warning("Kitsu search failed, returning expired cache for query '%s': %s", search_query, e)
                formatted_metas = []
                for m in cached["metas"]:
                    m_copy = m.copy()
                    m_copy["type"] = catalog_type
                    formatted_metas.append(m_copy)
                return await respond_with({"metas": formatted_metas})

        return await respond_with({"metas": metas})

    # --- Recommendations Catalogs ---
    elif catalog_id in ["anisync_rec", "anisync_loved", "anisync_liked"]:
        if not user.get("enable_recommendations", True):
            return await respond_with({"metas": []})
            
        from app.services.recommendations import get_cached_recommendations, trigger_recommendation_update_background
        
        cache = get_cached_recommendations(user_id)
        
        # Trigger background update if cache is missing or stale
        trigger_recommendation_update_background(user_id)
        
        if not cache:
            # Return popular anime as temporary fallback while background generates recommendations
            from app.services.recommendations import get_popular_fallbacks
            fallbacks = get_popular_fallbacks()
            if catalog_id == "anisync_rec":
                metas = fallbacks[:15]
            elif catalog_id == "anisync_loved":
                metas = fallbacks[15:30]
            else:
                metas = fallbacks[30:45]
        else:
            if catalog_id == "anisync_rec":
                metas = cache.get("rec_items", [])
            elif catalog_id == "anisync_loved":
                metas = cache.get("loved_items", [])
            else:
                metas = cache.get("liked_items", [])
                
        # Handle pagination skip
        metas = metas[offset: offset + 40]
        # Ensure that the item type matches the catalog type so Stremio doesn't filter them out
        formatted_metas = []
        for m in metas:
            m_copy = m.copy()
            m_copy["type"] = catalog_type
            formatted_metas.append(m_copy)
        return await respond_with({"metas": formatted_metas})

    # --- Combined Watchlists ---
    elif catalog_id.startswith("comb_"):
        mal_enabled = user.get("mal_access_token") and user.get("mal_enabled", True)
        anilist_enabled = user.get("anilist_token") and user.get("anilist_enabled", True)
        
        if not mal_enabled and not anilist_enabled:
            return await respond_with({"metas": []})

        comb_status = catalog_id.split("comb_")[1]
        
        # Map combined status to individual tracker statuses
        mal_status = None
        al_status = None
        if comb_status == "watching":
            mal_status = "watching"
            al_status = "CURRENT"
        elif comb_status == "plan_to_watch":
            mal_status = "plan_to_watch"
            al_status = "PLANNING"
        elif comb_status == "completed":
            mal_status = "completed"
            al_status = "COMPLETED"
        elif comb_status == "paused_on_hold":
            mal_status = "on_hold"
            al_status = "PAUSED"
        elif comb_status == "dropped":
            mal_status = "dropped"
            al_status = "DROPPED"

        try:
            # Fetch MAL and AniList lists in parallel
            mal_entries = []
            anilist_entries = []

            async def fetch_mal():
                nonlocal mal_entries
                if mal_enabled and mal_status:
                    try:
                        mal_entries = await get_cached_mal_user_anime_list(user_id, user["mal_access_token"], mal_status)
                    except Exception as ex:
                        logging.error("Combined: Failed to fetch MAL list for %s: %s", mal_status, ex)

            async def fetch_al():
                nonlocal anilist_entries
                if anilist_enabled and al_status:
                    try:
                        viewer = await anilist_api.get_viewer(user["anilist_token"])
                        anilist_uid = viewer["id"]
                        
                        # Note: we also fetch REPEATING if status is CURRENT (Watching)
                        statuses = [al_status]
                        if al_status == "CURRENT":
                            statuses.append("REPEATING")
                            
                        for stat in statuses:
                            collection = await get_cached_anilist_user_anime_list(
                                user_id, user["anilist_token"], anilist_uid=anilist_uid, status=stat
                            )
                            lists = collection.get("lists", [])
                            for user_list in lists:
                                anilist_entries.extend(user_list.get("entries", []))
                    except Exception as ex:
                        logging.error("Combined: Failed to fetch AniList list for %s: %s", al_status, ex)

            await asyncio.gather(fetch_mal(), fetch_al())

            # 1. Match AniList entries and MAL entries
            al_by_mal_id = {}
            al_by_al_id = {}
            for entry in anilist_entries:
                al_id = str(entry["media"]["id"])
                al_by_al_id[al_id] = entry
                
                id_mal = entry["media"].get("idMal")
                if id_mal:
                    al_by_mal_id[str(id_mal)] = entry

            # 2. Bulk resolve missing MAL IDs for AniList entries using MongoDB id_cache
            missing_mal_ids_al_ids = [str(entry["media"]["id"]) for entry in anilist_entries if not entry["media"].get("idMal")]
            if missing_mal_ids_al_ids:
                try:
                    from app.services.db import id_cache_collection
                    cache_docs = list(id_cache_collection.find({"anilist_id": {"$in": missing_mal_ids_al_ids}}))
                    for doc in cache_docs:
                        al_id = doc["anilist_id"]
                        mal_id = doc.get("mal_id")
                        if mal_id and al_id in al_by_al_id:
                            al_by_mal_id[str(mal_id)] = al_by_al_id[al_id]
                except Exception as e:
                    logging.error("Failed to query id_cache for missing MAL IDs in combined catalog: %s", e)

            # 3. Bulk resolve missing AniList IDs for unmatched MAL entries
            unmatched_mal_ids = [str(item["node"]["id"]) for item in mal_entries if str(item["node"]["id"]) not in al_by_mal_id]
            if unmatched_mal_ids:
                try:
                    from app.services.db import id_cache_collection
                    cache_docs = list(id_cache_collection.find({"mal_id": {"$in": unmatched_mal_ids}}))
                    mal_to_al_cache = {doc["mal_id"]: doc["anilist_id"] for doc in cache_docs if doc.get("anilist_id")}
                    for mal_id, al_id in mal_to_al_cache.items():
                        if al_id in al_by_al_id:
                            al_by_mal_id[mal_id] = al_by_al_id[al_id]
                except Exception as e:
                    logging.error("Failed to query id_cache for unmatched MAL IDs in combined catalog: %s", e)

            # 4. Group into combined items list
            processed_mal_ids = set()
            processed_al_ids = set()
            combined_items = []

            for mal_item in mal_entries:
                node = mal_item["node"]
                mal_id = str(node["id"])
                processed_mal_ids.add(mal_id)
                
                al_entry = al_by_mal_id.get(mal_id)
                if al_entry:
                    al_id = str(al_entry["media"]["id"])
                    processed_al_ids.add(al_id)
                    combined_items.append({
                        "tracker": "both",
                        "mal_item": mal_item,
                        "anilist_item": al_entry,
                        "mal_id": mal_id,
                        "anilist_id": al_id,
                    })
                else:
                    combined_items.append({
                        "tracker": "mal",
                        "mal_item": mal_item,
                        "anilist_item": None,
                        "mal_id": mal_id,
                        "anilist_id": None,
                    })

            for al_entry in anilist_entries:
                al_id = str(al_entry["media"]["id"])
                if al_id in processed_al_ids:
                    continue
                id_mal = al_entry["media"].get("idMal")
                if id_mal and str(id_mal) in processed_mal_ids:
                    continue
                
                processed_al_ids.add(al_id)
                if id_mal:
                    processed_mal_ids.add(str(id_mal))

                combined_items.append({
                    "tracker": "anilist",
                    "mal_item": None,
                    "anilist_item": al_entry,
                    "mal_id": str(id_mal) if id_mal else None,
                    "anilist_id": al_id,
                })

            current_time = int(time.time())

            # Bulk fetch AniList next airing details for combined items that are airing
            bulk_details = {}
            if user.get("sort_by_new_episodes") and comb_status in ["watching", "plan_to_watch"]:
                mal_ids_to_query = [item["mal_id"] for item in combined_items if item["mal_id"]]
                if mal_ids_to_query:
                    bulk_details = await fetch_anilist_details_in_bulk(mal_ids_to_query)

            # Helper to compute flags for combined items
            def compute_comb_flags(item):
                tracker = item["tracker"]
                is_new_ep = False
                latest_aired_at = 0
                next_airing_at = 2**31 - 1
                
                # Check status/airing state first
                is_airing = False
                if tracker in ["both", "anilist"]:
                    al_media = item["anilist_item"].get("media", {})
                    al_status_str = al_media.get("status", "")
                    is_airing = (al_status_str in ["RELEASING", "NOT_YET_RELEASED"])
                elif tracker == "mal":
                    mal_status_str = item["mal_item"]["node"].get("status", "")
                    is_airing = (mal_status_str in ["currently_airing", "not_yet_aired"])
                
                # Extract progress
                progress = 0
                if tracker == "both":
                    mal_progress = item["mal_item"].get("my_list_status", {}).get("num_episodes_watched", 0)
                    al_progress = item["anilist_item"].get("progress", 0)
                    progress = max(mal_progress, al_progress)
                elif tracker == "mal":
                    progress = item["mal_item"].get("my_list_status", {}).get("num_episodes_watched", 0)
                elif tracker == "anilist":
                    progress = item["anilist_item"].get("progress", 0)

                # Airing calculations using AniList data
                next_ep_num = None
                next_ep_airing_at = None
                
                if tracker in ["both", "anilist"]:
                    next_ep = item["anilist_item"].get("media", {}).get("nextAiringEpisode")
                    if next_ep:
                        next_ep_num = next_ep.get("episode")
                        next_ep_airing_at = next_ep.get("airingAt")
                elif tracker == "mal" and item["mal_id"]:
                    al_media = bulk_details.get(item["mal_id"]) or {}
                    next_ep = al_media.get("nextAiringEpisode")
                    if next_ep:
                        next_ep_num = next_ep.get("episode")
                        next_ep_airing_at = next_ep.get("airingAt")

                latest_aired_num = 0
                if next_ep_num and next_ep_airing_at:
                    latest_aired_num = next_ep_num - 1
                    latest_aired_at = next_ep_airing_at - 604800
                    next_airing_at = next_ep_airing_at

                if is_airing and user.get("sort_by_new_episodes") and latest_aired_num > 0 and progress < latest_aired_num:
                    time_since_air = current_time - latest_aired_at
                    # Global 7-day window
                    if time_since_air <= 604800:
                        is_new_ep = True

                return is_new_ep, latest_aired_at, next_airing_at

            # ── Sorting ───────────────────────────────────────────────────────
            if user.get("sort_by_new_episodes") and comb_status in ["watching", "plan_to_watch"]:
                def parse_mal_updated_at(updated_str):
                    if not updated_str:
                        return 0
                    try:
                        s = updated_str.replace("Z", "").replace("T", " ")
                        s = s.split(".")[0]
                        dt = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
                        return int(dt.timestamp())
                    except Exception:
                        return 0

                def get_comb_priority(item):
                    is_new_ep, _, next_airing_at = compute_comb_flags(item)
                    
                    # Determine combined updatedAt
                    mal_updated_ts = 0
                    al_updated_ts = 0
                    if item["mal_item"]:
                        mal_updated_ts = parse_mal_updated_at(item["mal_item"].get("my_list_status", {}).get("updated_at", ""))
                    if item["anilist_item"]:
                        al_updated_ts = item["anilist_item"].get("updatedAt") or 0
                    updated_ts = max(mal_updated_ts, al_updated_ts)

                    # Determine airing state
                    is_airing = False
                    if item["tracker"] in ["both", "anilist"]:
                        al_status_str = item["anilist_item"].get("media", {}).get("status", "")
                        is_airing = (al_status_str in ["RELEASING", "NOT_YET_RELEASED"])
                    elif item["tracker"] == "mal":
                        mal_status_str = item["mal_item"]["node"].get("status", "")
                        is_airing = (mal_status_str in ["currently_airing", "not_yet_aired"])

                    if is_airing and is_new_ep:
                        # Group 0: Airing with new episodes (first)
                        group_idx = 0
                        secondary_sort = (next_airing_at, -updated_ts)
                    elif not is_airing:
                        # Group 1: Completed airing anime
                        group_idx = 1
                        secondary_sort = (-updated_ts, 0)
                    else:
                        # Group 2: Airing with no new episodes (sorted ascending by next airing time)
                        group_idx = 2
                        secondary_sort = (next_airing_at, -updated_ts)
                        
                    return (group_idx, *secondary_sort)

                sorted_items = sorted(combined_items, key=get_comb_priority)
                paged_items = sorted_items[offset: offset + 40]
            else:
                def parse_mal_updated_at(updated_str):
                    if not updated_str:
                        return 0
                    try:
                        s = updated_str.replace("Z", "").replace("T", " ")
                        s = s.split(".")[0]
                        dt = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
                        return int(dt.timestamp())
                    except Exception:
                        return 0

                def get_default_updated_ts(item):
                    mal_updated_ts = 0
                    al_updated_ts = 0
                    if item["mal_item"]:
                        mal_updated_ts = parse_mal_updated_at(item["mal_item"].get("my_list_status", {}).get("updated_at", ""))
                    if item["anilist_item"]:
                        al_updated_ts = item["anilist_item"].get("updatedAt") or 0
                    return -max(mal_updated_ts, al_updated_ts)

                sorted_items = sorted(combined_items, key=get_default_updated_ts)
                paged_items = sorted_items[offset: offset + 40]

            # ── Build meta items ──────────────────────────────────────────────
            for item in paged_items:
                tracker = item["tracker"]
                mal_id = item["mal_id"]
                anilist_id = item["anilist_id"]

                # Extract progress and title/poster
                progress = 0
                total_eps = "?"
                name = ""
                poster = ""

                if tracker == "both":
                    mal_progress = item["mal_item"].get("my_list_status", {}).get("num_episodes_watched", 0)
                    al_progress = item["anilist_item"].get("progress", 0)
                    progress = max(mal_progress, al_progress)
                    total_eps = item["anilist_item"].get("media", {}).get("episodes") or item["mal_item"]["node"].get("num_episodes") or "?"
                    
                    media = item["anilist_item"]["media"]
                    name = media["title"]["userPreferred"] or media["title"]["english"] or item["mal_item"]["node"].get("title", "")
                    poster = (
                        (media["coverImage"] or {}).get("large")
                        or (media["coverImage"] or {}).get("medium")
                        or item["mal_item"]["node"].get("main_picture", {}).get("large")
                        or item["mal_item"]["node"].get("main_picture", {}).get("medium")
                        or ""
                    )
                elif tracker == "mal":
                    node = item["mal_item"]["node"]
                    progress = item["mal_item"].get("my_list_status", {}).get("num_episodes_watched", 0)
                    total_eps = node.get("num_episodes") or "?"
                    name = node.get("title", "")
                    poster = (
                        node.get("main_picture", {}).get("large")
                        or node.get("main_picture", {}).get("medium")
                        or ""
                    )
                elif tracker == "anilist":
                    entry = item["anilist_item"]
                    media = entry["media"]
                    progress = entry.get("progress", 0)
                    total_eps = media.get("episodes") or "?"
                    name = media["title"]["userPreferred"] or media["title"]["english"] or ""
                    poster = (
                        (media["coverImage"] or {}).get("large")
                        or (media["coverImage"] or {}).get("medium")
                        or ""
                    )

                is_new_ep = False
                if comb_status in ["watching", "plan_to_watch"]:
                    is_new_ep, _, _ = compute_comb_flags(item)

                if is_new_ep and poster:
                    encoded_url = urllib.parse.quote_plus(poster)
                    m_id_for_url = mal_id if mal_id else anilist_id
                    poster = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/{user_id}/poster/{m_id_for_url}.jpg?url={encoded_url}&badge=new&tracker={tracker}&v=newep_graphical_v11"

                stremio_id = f"mal:{mal_id}" if mal_id else f"anilist:{anilist_id}"

                metas.append({
                    "id": stremio_id,
                    "type": catalog_type,
                    "name": name,
                    "poster": poster,
                    "description": (
                        f"Watchlist - {comb_status.replace('_', ' ').title()} (Combined).\n"
                        f"Progress: {progress} / {total_eps}."
                    ),
                })
        except Exception as e:
            logging.error("Combined watchlist catalog load failed for status %s: %s", comb_status, e)

    # --- MAL Watchlists ---
    elif catalog_id.startswith("mal_"):
        if not user.get("mal_access_token") or not user.get("mal_enabled"):
            return await respond_with({"metas": []})

        mal_status = catalog_id.split("mal_")[1]
        try:
            data_items = await get_cached_mal_user_anime_list(user_id, user["mal_access_token"], mal_status)

            current_time = int(time.time())

            # Fetch AniList next-airing-episode data in bulk for watching/planning lists
            bulk_details = {}
            if mal_status in ["watching", "plan_to_watch"] and data_items:
                bulk_details = await fetch_anilist_details_in_bulk(
                    [str(item["node"]["id"]) for item in data_items]
                )

            # ── Compute per-item: is_new_ep (with time-gating) ────────────────
            def compute_mal_flags(node, mal_id):
                """Return (is_new_ep, has_unwatched, latest_aired_at, latest_aired_num)."""
                progress = node.get("my_list_status", {}).get("num_episodes_watched", 0)
                total = node.get("num_episodes", 0)
                al_media = bulk_details.get(mal_id) or {}
                next_ep = al_media.get("nextAiringEpisode")
                next_ep_num = next_ep.get("episode") if next_ep else None
                next_ep_airing_at = next_ep.get("airingAt") if next_ep else None

                latest_aired_at = 0
                latest_aired_num = 0

                if next_ep_num and next_ep_airing_at:
                    latest_aired_num = next_ep_num - 1
                    latest_aired_at = next_ep_airing_at - 604800

                has_unwatched = False
                if latest_aired_num > 0:
                    has_unwatched = progress < latest_aired_num
                elif total > 0:
                    has_unwatched = progress < total
                else:
                    has_unwatched = True

                # Banned completed airing anime from showing the [New] tag
                status = node.get("status", "")
                is_airing = (status == "currently_airing" or status == "not_yet_aired")

                is_new_ep = False
                if is_airing and user.get("sort_by_new_episodes") and latest_aired_num > 0 and progress < latest_aired_num:
                    time_since_air = current_time - latest_aired_at
                    # Global 7-day window (604800 seconds)
                    if time_since_air <= 604800:
                        is_new_ep = True

                return is_new_ep, has_unwatched, latest_aired_at, latest_aired_num

            # ── Sorting ───────────────────────────────────────────────────────
            if user.get("sort_by_new_episodes") and mal_status in ["watching", "plan_to_watch"]:
                def parse_mal_updated_at(updated_str):
                    if not updated_str:
                        return 0
                    try:
                        s = updated_str.replace("Z", "").replace("T", " ")
                        s = s.split(".")[0]
                        dt = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
                        return int(dt.timestamp())
                    except Exception:
                        return 0

                def get_mal_priority(item):
                    node = item.get("node", {})
                    mal_id = str(node["id"])
                    is_new_ep, has_unwatched, latest_aired_at, _ = compute_mal_flags(node, mal_id)
                    
                    status = node.get("status", "")
                    is_airing = (status == "currently_airing" or status == "not_yet_aired")
                    updated_ts = parse_mal_updated_at(node.get("my_list_status", {}).get("updated_at", ""))
                    
                    # Fetch airing time for Group 2 sorting
                    al_media = bulk_details.get(mal_id) or {}
                    next_ep = al_media.get("nextAiringEpisode")
                    airing_at = next_ep.get("airingAt") if next_ep else None
                    if not airing_at:
                        airing_at = 2**31 - 1 # Fallback for no next airing details
                    
                    if is_airing and is_new_ep:
                        # Group 0: Airing anime with new episodes (first)
                        group_idx = 0
                        secondary_sort = (airing_at, -updated_ts)
                    elif not is_airing:
                        # Group 1: Completed airing anime (always placed before Group 2)
                        group_idx = 1
                        secondary_sort = (-updated_ts, 0)
                    else:
                        # Group 2: Airing anime with no new episodes (sorted ascending by next airing time)
                        group_idx = 2
                        secondary_sort = (airing_at, -updated_ts)
                        
                    return (group_idx, *secondary_sort)

                sorted_data_items = sorted(data_items, key=get_mal_priority)
                paged_data_items = sorted_data_items[offset: offset + 40]
            else:
                paged_data_items = data_items[offset: offset + 40]

            # ── Build meta items ──────────────────────────────────────────────
            for item in paged_data_items:
                node = item["node"]
                mal_id = str(node["id"])
                progress = node.get("my_list_status", {}).get("num_episodes_watched", 0)

                is_new_ep, _, _, _ = compute_mal_flags(node, mal_id) if mal_status in ["watching", "plan_to_watch"] else (False, False, 0, 0)

                name = node.get("title", "")

                poster = (
                    node.get("main_picture", {}).get("large")
                    or node.get("main_picture", {}).get("medium")
                    or ""
                )

                if is_new_ep and poster:
                    encoded_url = urllib.parse.quote_plus(poster)
                    poster = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/{user_id}/poster/{mal_id}.jpg?url={encoded_url}&badge=new&tracker=mal&v=newep_graphical_v11"

                metas.append({
                    "id": f"mal:{node['id']}",
                    "type": catalog_type,
                    "name": name,
                    "poster": poster,
                    "description": (
                        f"MAL Watchlist - {mal_status.replace('_', ' ').title()}.\n"
                        f"Progress: {progress} / {node.get('num_episodes') or '?'}."
                    ),
                })
        except Exception as e:
            logging.error("MAL catalog load failed for status %s: %s", mal_status, e)

    # --- AniList Watchlists ---
    elif catalog_id.startswith("anilist_"):
        if not user.get("anilist_token") or not user.get("anilist_enabled"):
            return await respond_with({"metas": []})

        # Retrieve user's AniList numerical ID first
        try:
            viewer = await anilist_api.get_viewer(user["anilist_token"])
            anilist_uid = viewer["id"]
        except Exception as e:
            logging.error("Failed to retrieve AniList viewer ID: %s", e)
            return await respond_with({"metas": []})

        anilist_status = catalog_id.split("anilist_")[1].upper()
        if anilist_status == "WATCHING":
            anilist_status = "CURRENT"
        try:
            collection = await get_cached_anilist_user_anime_list(
                user_id,
                user["anilist_token"],
                anilist_uid=anilist_uid,
                status=anilist_status
            )
            lists = collection.get("lists", [])
            entries = []
            for user_list in lists:
                entries.extend(user_list.get("entries", []))

            current_time = int(time.time())

            # ── Compute per-entry flags (with time-gating) ────────────────────
            def compute_al_flags(entry):
                media = entry.get("media", {})
                progress = entry.get("progress", 0)
                total = media.get("episodes") or 0
                next_ep = media.get("nextAiringEpisode")
                next_ep_num = next_ep.get("episode") if next_ep else None
                next_ep_airing_at = next_ep.get("airingAt") if next_ep else None

                latest_aired_at = 0
                latest_aired_num = 0

                if next_ep_num and next_ep_airing_at:
                    latest_aired_num = next_ep_num - 1
                    latest_aired_at = next_ep_airing_at - 604800

                has_unwatched = False
                if latest_aired_num > 0:
                    has_unwatched = progress < latest_aired_num
                elif total > 0:
                    has_unwatched = progress < total
                else:
                    has_unwatched = True

                # Banned completed airing anime from showing the [New] tag
                status = media.get("status", "")
                is_airing = (status in ["RELEASING", "NOT_YET_RELEASED"])

                is_new_ep = False
                if is_airing and user.get("sort_by_new_episodes") and latest_aired_num > 0 and progress < latest_aired_num:
                    time_since_air = current_time - latest_aired_at
                    # Global 7-day window (604800 seconds)
                    if time_since_air <= 604800:
                        is_new_ep = True

                return is_new_ep, has_unwatched, latest_aired_at

            # ── Sorting ───────────────────────────────────────────────────────
            if user.get("sort_by_new_episodes") and anilist_status in ["CURRENT", "PLANNING"]:
                def get_al_priority(entry):
                    is_new_ep, has_unwatched, latest_aired_at = compute_al_flags(entry)
                    media = entry.get("media", {})
                    status = media.get("status", "")
                    is_airing = (status in ["RELEASING", "NOT_YET_RELEASED"])
                    updated_ts = entry.get("updatedAt") or 0
                    
                    next_ep = media.get("nextAiringEpisode")
                    airing_at = next_ep.get("airingAt") if next_ep else None
                    if not airing_at:
                        airing_at = 2**31 - 1 # Fallback for no next airing details
                    
                    if is_airing and is_new_ep:
                        # Group 0: Airing anime with new episodes (first)
                        group_idx = 0
                        secondary_sort = (airing_at, -updated_ts)
                    elif not is_airing:
                        # Group 1: Completed airing anime (always placed before Group 2)
                        group_idx = 1
                        secondary_sort = (-updated_ts, 0)
                    else:
                        # Group 2: Airing anime with no new episodes (sorted ascending by next airing time)
                        group_idx = 2
                        secondary_sort = (airing_at, -updated_ts)
                        
                    return (group_idx, *secondary_sort)

                entries = sorted(entries, key=get_al_priority)

            # Paginate the full sorted list
            paged_entries = entries[offset: offset + 40]

            # ── Build meta items ──────────────────────────────────────────────
            for entry in paged_entries:
                media = entry["media"]
                al_id = str(media["id"])
                progress = entry.get("progress", 0)

                is_new_ep = False
                if anilist_status in ["CURRENT", "PLANNING"]:
                    is_new_ep, _, _ = compute_al_flags(entry)

                name = media["title"]["userPreferred"] or media["title"]["english"] or ""

                poster = (
                    (media["coverImage"] or {}).get("large")
                    or (media["coverImage"] or {}).get("medium")
                    or ""
                )

                if is_new_ep and poster:
                    encoded_url = urllib.parse.quote_plus(poster)
                    poster = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/{user_id}/poster/{al_id}.jpg?url={encoded_url}&badge=new&tracker=anilist&v=newep_graphical_v11"

                metas.append({
                    "id": f"anilist:{al_id}",
                    "type": catalog_type,
                    "name": name,
                    "poster": poster,
                    "description": (
                        f"AniList Watchlist - {anilist_status.title()}.\n"
                        f"Progress: {progress} / {media.get('episodes') or '?'}."
                    ),
                })
        except Exception as e:
            logging.error("AniList catalog load failed for status %s: %s", anilist_status, e)


    # Map custom types back to standard types for media items so streams/meta function correctly in Stremio
    custom_types_map = {
        "Watching": "anime",
        "Plan to Watch": "anime",
        "Completed": "anime",
        "On Hold": "anime",
        "Dropped": "anime",
        "Planning": "anime",
        "Paused": "anime",
        "Repeating": "anime"
    }
    formatted_metas = []
    for m in metas:
        m_copy = m.copy()
        if m_copy.get("type") in custom_types_map:
            m_copy["type"] = custom_types_map[m_copy["type"]]
        elif catalog_type in custom_types_map:
            m_copy["type"] = custom_types_map[catalog_type]
        formatted_metas.append(m_copy)

    return await respond_with({"metas": formatted_metas})

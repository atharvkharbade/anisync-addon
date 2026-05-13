import urllib.parse
import logging
import asyncio
from quart import Blueprint, abort

from app.services.db import get_user
from app.routes.utils import respond_with
from app.api import mal as mal_api
from app.api import anilist as anilist_api
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


async def fetch_anilist_details_in_bulk(mal_ids: list[str]) -> dict:
    if not mal_ids:
        return {}
    from app.services.db import id_cache_collection
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
        import httpx
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        # Chunk anilist_ids in groups of 50
        chunks = [anilist_ids[i:i + 50] for i in range(0, len(anilist_ids), 50)]
        
        async def fetch_chunk(chunk_ids):
            payload = {
                "query": query,
                "variables": {"ids": [int(x) for x in chunk_ids]}
            }
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post("https://graphql.anilist.co", json=payload, headers=headers)
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
            
        al_details = {str(m["id"]): m for m in media_list if m.get("id")}
        mal_details = {}
        for mid, aid in mal_to_anilist.items():
            if aid in al_details:
                mal_details[mid] = al_details[aid]
        return mal_details
    except Exception as e:
        logging.error("Failed bulk AniList query for MAL: %s", e)
    return {}


currently_fetching_pairs = set()
jikan_semaphore = None

def get_jikan_semaphore():
    global jikan_semaphore
    if jikan_semaphore is None:
        jikan_semaphore = asyncio.Semaphore(1)
    return jikan_semaphore

async def background_fetch_and_cache_filler(mal_id: str, episode: int):
    pair = (str(mal_id), episode)
    async with get_jikan_semaphore():
        await asyncio.sleep(1.0)  # Rate limiting safety sleep
        
        # Check cache again inside the lock
        from app.services.db import get_jikan_filler_cache, set_jikan_filler_cache
        cached = get_jikan_filler_cache(mal_id, episode)
        if cached is not None:
            currently_fetching_pairs.discard(pair)
            return
            
        retries = 3
        backoff = 2.0
        for attempt in range(retries):
            try:
                import httpx
                url = f"https://api.jikan.moe/v4/anime/{mal_id}/episodes/{episode}"
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                        filler = bool(data.get("filler", False))
                        set_jikan_filler_cache(mal_id, episode, filler)
                        logging.info("Cached filler status for mal_id=%s ep=%s: %s", mal_id, episode, filler)
                        break
                    elif resp.status_code == 404:
                        set_jikan_filler_cache(mal_id, episode, False)
                        logging.info("Cached filler status for mal_id=%s ep=%s: False (404 Not Found)", mal_id, episode)
                        break
                    elif resp.status_code == 429:
                        logging.warning("Jikan 429 rate limit hit for mal_id=%s ep=%s, retrying in %s seconds...", mal_id, episode, backoff)
                        await asyncio.sleep(backoff)
                        backoff *= 2.0
                    else:
                        logging.error("Jikan returned status %s for mal_id=%s ep=%s", resp.status_code, mal_id, episode)
                        break
            except Exception as e:
                logging.error("Jikan background fetch exception (attempt %s) for mal_id=%s ep=%s: %s", attempt + 1, mal_id, episode, e)
                await asyncio.sleep(1.0)
                
        currently_fetching_pairs.discard(pair)


async def fetch_jikan_filler_status(mal_id: str, episode: int) -> bool:
    """Return True if the given episode is a filler, using MongoDB cache + Jikan background fetch."""
    from app.services.db import get_jikan_filler_cache
    
    # Check cache first
    cached = get_jikan_filler_cache(mal_id, episode)
    if cached is not None:
        return cached
        
    # Cache miss: queue background fetch and return False immediately
    pair = (str(mal_id), episode)
    if pair not in currently_fetching_pairs:
        currently_fetching_pairs.add(pair)
        asyncio.create_task(background_fetch_and_cache_filler(mal_id, episode))
        
    return False


async def bulk_jikan_filler(items: list[tuple[str, int]]) -> dict[tuple, bool]:
    """Fetch filler status for multiple (mal_id, episode) pairs concurrently."""
    tasks = {pair: fetch_jikan_filler_status(pair[0], pair[1]) for pair in items}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    return {
        pair: (result if isinstance(result, bool) else False)
        for pair, result in zip(tasks.keys(), results)
    }


@catalog_bp.route("/<user_id>/catalog/<string:catalog_type>/<string:catalog_id>.json")
@catalog_bp.route("/<user_id>/catalog/<string:catalog_type>/<string:catalog_id>/<path:extras>.json")
async def handle_catalog(user_id: str, catalog_type: str, catalog_id: str, extras: str = ""):
    if catalog_id == "anime_tracker_search":
        catalog_id = "anisync_search"

    # We handle 'anime', 'series', and 'movie' catalog types
    if catalog_type not in ["anime", "series", "movie"]:
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

        # Query Kitsu API directly for fast search results with high rate limits
        import httpx
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
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params, headers=headers)
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
                            "type": item_type,
                            "name": title,
                            "poster": poster,
                            "description": synopsis[:200] + "..." if len(synopsis) > 200 else synopsis,
                        })
        except Exception as e:
            logging.error("Kitsu search query failed: %s", e)

        return await respond_with({"metas": metas})

    # --- MAL Watchlists ---
    if catalog_id.startswith("mal_"):
        if not user.get("mal_access_token") or not user.get("mal_enabled"):
            return await respond_with({"metas": []})

        mal_status = catalog_id.split("mal_")[1]
        # Map Stremio catalog names to MAL API statuses
        # MAL statuses: watching, completed, on_hold, dropped, plan_to_watch
        try:
            # When sorting by new episodes, fetch wider (100) to sort correctly then paginate locally
            fetch_limit = 100 if (user.get("sort_by_new_episodes") and mal_status == "watching") else 40
            fetch_offset = 0 if (user.get("sort_by_new_episodes") and mal_status == "watching") else offset

            res = await mal_api.get_user_anime_list(
                user["mal_access_token"],
                status=mal_status,
                limit=fetch_limit,
                offset=fetch_offset
            )
            data_items = res.get("data", [])

            import time
            current_time = int(time.time())

            # Fetch AniList next-airing-episode data in bulk for watching list
            bulk_details = {}
            if mal_status == "watching" and data_items:
                bulk_details = await fetch_anilist_details_in_bulk(
                    [str(item["node"]["id"]) for item in data_items]
                )

            # ── Compute per-item: is_new_ep (no time-gating) ──────────────────
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
                if is_airing and user.get("sort_by_new_episodes") and latest_aired_num > 0:
                    is_new_ep = progress < latest_aired_num

                return is_new_ep, has_unwatched, latest_aired_at, latest_aired_num

            # ── Sorting ───────────────────────────────────────────────────────
            if user.get("sort_by_new_episodes") and mal_status == "watching":
                import datetime
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

                data_items = sorted(data_items, key=get_mal_priority)
                data_items = data_items[offset: offset + 40]

            # ── Build meta items ──────────────────────────────────────────────
            for item in data_items:
                node = item["node"]
                mal_id = str(node["id"])
                progress = node.get("my_list_status", {}).get("num_episodes_watched", 0)

                is_new_ep, _, _, _ = compute_mal_flags(node, mal_id) if mal_status == "watching" else (False, False, 0, 0)

                name = node.get("title", "")
                if is_new_ep:
                    name = f"[New] {name}"

                poster = (
                    node.get("main_picture", {}).get("large")
                    or node.get("main_picture", {}).get("medium")
                    or ""
                )

                metas.append({
                    "id": f"mal:{node['id']}",
                    "type": "anime",
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
        # AniList statuses: CURRENT, PLANNING, COMPLETED, PAUSED, DROPPED, REPEATING
        try:
            collection = await anilist_api.get_user_anime_list(
                user["anilist_token"],
                user_id=anilist_uid,
                status=anilist_status
            )
            lists = collection.get("lists", [])
            entries = []
            for user_list in lists:
                entries.extend(user_list.get("entries", []))

            import time
            current_time = int(time.time())

            # ── Compute per-entry flags (no time-gating) ──────────────────────
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
                if is_airing and user.get("sort_by_new_episodes") and latest_aired_num > 0:
                    is_new_ep = progress < latest_aired_num

                return is_new_ep, has_unwatched, latest_aired_at

            # ── Sorting ───────────────────────────────────────────────────────
            if user.get("sort_by_new_episodes") and anilist_status == "CURRENT":
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
                if anilist_status == "CURRENT":
                    is_new_ep, _, _ = compute_al_flags(entry)

                name = media["title"]["userPreferred"] or media["title"]["english"] or ""
                if is_new_ep:
                    name = f"[New] {name}"

                poster = (
                    (media["coverImage"] or {}).get("large")
                    or (media["coverImage"] or {}).get("medium")
                    or ""
                )

                metas.append({
                    "id": f"anilist:{al_id}",
                    "type": "anime",
                    "name": name,
                    "poster": poster,
                    "description": (
                        f"AniList Watchlist - {anilist_status.title()}.\n"
                        f"Progress: {progress} / {media.get('episodes') or '?'}."
                    ),
                })
            # Note: pagination already applied above via paged_entries
        except Exception as e:
            logging.error("AniList catalog load failed for status %s: %s", anilist_status, e)


    return await respond_with({"metas": metas})

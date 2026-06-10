import logging
import httpx
import asyncio
from quart import Blueprint

from app.services.db import get_user
from app.routes.utils import respond_with, is_valid_user_id, rate_limit
from app.services.http import get_client
from app.lib.id_resolver import resolve, resolve_mal_to_kitsu, resolve_anilist_to_kitsu, resolve_simkl_to_kitsu

meta_bp = Blueprint("meta", __name__)

async def fetch_anizp_metadata(anilist_id: str = None, mal_id: str = None) -> dict:
    url = "https://api.ani.zip/mappings"
    params = {}
    if anilist_id:
        params["anilist_id"] = anilist_id
    elif mal_id:
        params["mal_id"] = mal_id
    else:
        return {}
    try:
        client = get_client()
        resp = await client.get(url, params=params, timeout=8)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logging.warning("Failed to fetch rich metadata from ani.zip: %s", e)
    return {}


async def fetch_cinemeta_metadata(imdb_id: str, media_type: str) -> dict:
    url = f"https://v3-cinemeta.strem.io/meta/{media_type}/{imdb_id}.json"
    try:
        client = get_client()
        resp = await client.get(url, timeout=8)
        if resp.status_code == 200:
            return resp.json().get("meta", {})
    except Exception as e:
        logging.warning("Failed to fetch metadata from Cinemeta: %s", e)
    return {}

KITSU_API_BASE = "https://kitsu.io/api/edge"
TIMEOUT = 10


async def fetch_kitsu_meta(kitsu_id: str) -> dict:
    url = f"{KITSU_API_BASE}/anime/{kitsu_id}"
    params = {"include": "episodes"}
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }
    client = get_client()
    resp = await client.get(url, params=params, headers=headers, timeout=TIMEOUT)
    if resp.status_code != 200:
        logging.error("Kitsu API returned status %s for id %s", resp.status_code, kitsu_id)
        return {}
    return resp.json()


def map_kitsu_to_stremio(kitsu_data: dict, meta_id: str, anizp_data: dict = None, mal_id: str = None, show_filler_tags: bool = True, loop = None, cinemeta_data: dict = None) -> dict:
    data = kitsu_data.get("data", {})
    if not data:
        return {}

    attributes = data.get("attributes", {})
    titles = attributes.get("titles", {})
    title = attributes.get("canonicalTitle") or titles.get("en") or titles.get("en_jp") or "Unknown Title"
    synopsis = attributes.get("synopsis", "")
    anizp_images = anizp_data.get("images", []) if anizp_data else []
    anizp_fanart = None
    anizp_poster = None
    anizp_logo = None
    for img in anizp_images:
        if img.get("coverType") == "Fanart" and not anizp_fanart:
            anizp_fanart = img.get("url")
        elif img.get("coverType") == "Poster" and not anizp_poster:
            anizp_poster = img.get("url")
        elif img.get("coverType") in ["Clearlogo", "Logo"] and not anizp_logo:
            anizp_logo = img.get("url")

    poster_data = attributes.get("posterImage") or {}
    poster = anizp_poster or poster_data.get("original") or poster_data.get("large") or poster_data.get("medium") or ""
    cover_data = attributes.get("coverImage") or {}
    background = anizp_fanart or cover_data.get("original") or cover_data.get("large") or cover_data.get("medium") or poster

    imdb_id = anizp_data.get("mappings", {}).get("imdb_id") if anizp_data else None
    
    logo = anizp_logo
    if not logo and cinemeta_data:
        logo = cinemeta_data.get("logo")
    if not logo and imdb_id:
        logo = f"https://images.metahub.space/logo/medium/{imdb_id}/img"

    if cinemeta_data and cinemeta_data.get("background"):
        background = cinemeta_data.get("background")

    average_rating = attributes.get("averageRating")
    rating = str(round(float(average_rating) / 10.0, 1)) if average_rating else None

    # Release info (Year)
    start_date = attributes.get("startDate")
    end_date = attributes.get("endDate")
    release_info = ""
    if start_date:
        release_info = start_date[:4]
        if end_date:
            release_info += f"-{end_date[:4]}"
        else:
            release_info += "-"

    # Media Type
    subtype = (attributes.get("subtype") or "tv").lower()
    media_type = "movie" if subtype == "movie" else "series"

    # Videos / Episodes List
    videos = []
    included = kitsu_data.get("included", [])

    # Filter and sort episodes by number
    episodes_data = []
    for item in included:
        if item.get("type") == "episodes":
            episodes_data.append(item)

    anizp_episodes = anizp_data.get("episodes", {}) if anizp_data else {}

    if subtype == "movie":
        videos.append({
            "id": f"kitsu:{data['id']}",
            "title": title,
            "episode": 1,
            "season": 1,
            "released": start_date + "T00:00:00Z" if start_date else None,
            "overview": synopsis,
            "thumbnail": background or poster,
        })
    else:
        if episodes_data:
            # Sort by episode number
            episodes_data.sort(key=lambda x: x.get("attributes", {}).get("number") or 9999)
            for ep in episodes_data:
                attrs = ep.get("attributes", {})
                ep_num = attrs.get("number") or 1
                
                # Fetch details from ani.zip if available
                anizp_ep = anizp_episodes.get(str(ep_num)) or {}
                
                ep_title = (
                    attrs.get("canonicalTitle")
                    or anizp_ep.get("title", {}).get("en")
                    or anizp_ep.get("title", {}).get("x-jat")
                    or f"Episode {ep_num}"
                )
                released = attrs.get("airdate") or anizp_ep.get("airdate")
                overview = attrs.get("synopsis") or anizp_ep.get("overview") or anizp_ep.get("summary") or ""
                thumbnail = (
                    anizp_ep.get("image")
                    or (attrs.get("thumbnail") or {}).get("original")
                    or (attrs.get("thumbnail") or {}).get("large")
                    or background
                )
                
                # Check filler status
                is_filler = False
                if mal_id and show_filler_tags:
                    from app.services.db import get_jikan_filler_cache
                    cached = get_jikan_filler_cache(mal_id, ep_num)
                    if cached is not None:
                        is_filler = cached
                    else:
                        from app.routes.catalog import currently_fetching_pairs, background_fetch_and_cache_filler
                        pair = (str(mal_id), ep_num)
                        if pair not in currently_fetching_pairs:
                            currently_fetching_pairs.add(pair)
                            if loop and loop.is_running():
                                try:
                                    asyncio.run_coroutine_threadsafe(
                                        background_fetch_and_cache_filler(mal_id, ep_num),
                                        loop
                                    )
                                except Exception:
                                    pass
                                
                if is_filler:
                    ep_title = f"[Filler] {ep_title}"

                videos.append({
                    "id": f"kitsu:{data['id']}:{ep_num}",
                    "title": ep_title,
                    "episode": ep_num,
                    "season": 1,
                    "released": released + "T00:00:00Z" if released else None,
                    "overview": overview,
                    "thumbnail": thumbnail,
                })
        else:
            # Fallback if no episodes returned (generate placeholder episodes from episodeCount)
            ep_count = attributes.get("episodeCount") or 12
            for i in range(1, ep_count + 1):
                anizp_ep = anizp_episodes.get(str(i)) or {}
                ep_title = (
                    anizp_ep.get("title", {}).get("en")
                    or anizp_ep.get("title", {}).get("x-jat")
                    or f"Episode {i}"
                )
                overview = anizp_ep.get("overview") or anizp_ep.get("summary") or f"Episode {i} of {title}"
                thumbnail = anizp_ep.get("image") or background
                
                # Check filler status for fallback
                is_filler = False
                if mal_id and show_filler_tags:
                    from app.services.db import get_jikan_filler_cache
                    cached = get_jikan_filler_cache(mal_id, i)
                    if cached is not None:
                        is_filler = cached
                    else:
                        from app.routes.catalog import currently_fetching_pairs, background_fetch_and_cache_filler
                        pair = (str(mal_id), i)
                        if pair not in currently_fetching_pairs:
                            currently_fetching_pairs.add(pair)
                            if loop and loop.is_running():
                                try:
                                    asyncio.run_coroutine_threadsafe(
                                        background_fetch_and_cache_filler(mal_id, i),
                                        loop
                                    )
                                except Exception:
                                    pass
                                
                if is_filler:
                    ep_title = f"[Filler] {ep_title}"

                videos.append({
                    "id": f"kitsu:{data['id']}:{i}",
                    "title": ep_title,
                    "episode": i,
                    "season": 1,
                    "overview": overview,
                    "thumbnail": thumbnail,
                })

    genres = ["Anime"]
    if cinemeta_data and cinemeta_data.get("genres"):
        for g in cinemeta_data["genres"]:
            if g not in genres:
                genres.append(g)

    links = []
    if cinemeta_data and "links" in cinemeta_data:
        for link in cinemeta_data["links"]:
            if link.get("category") == "Cast":
                links.append(link)

    meta_obj = {
        "id": meta_id,
        "name": title,
        "type": media_type,
        "poster": poster,
        "background": background,
        "imdbRating": rating,
        "releaseInfo": release_info,
        "description": synopsis,
        "videos": videos,
        "genres": genres,
        "links": links,
    }
    if logo:
        meta_obj["logo"] = logo

    return meta_obj


@meta_bp.route("/<user_id>/meta/<string:meta_type>/<string:meta_id>.json")
@rate_limit(limit=60, period_seconds=60)
async def handle_meta(user_id: str, meta_type: str, meta_id: str):
    if meta_type not in ["anime", "series", "movie"]:
        return await respond_with({"meta": {}})

    if not is_valid_user_id(user_id):
        return await respond_with({"meta": {}})

    user = get_user(user_id)
    if not user:
        logging.warning("Meta request: Unknown user_id=%s", user_id)
        return await respond_with({"meta": {}})

    # Strip prefixes and get kitsu id
    kitsu_id = None
    anilist_id = None
    mal_id = None
    
    if meta_id.startswith("mal:"):
        mal_id = meta_id.split(":")[1]
        kitsu_id = await resolve_mal_to_kitsu(mal_id)
    elif meta_id.startswith("anilist:"):
        anilist_id = meta_id.split(":")[1]
        kitsu_id = await resolve_anilist_to_kitsu(anilist_id)
    elif meta_id.startswith("simkl:"):
        simkl_id = meta_id.split(":")[1]
        kitsu_id = await resolve_simkl_to_kitsu(simkl_id)
    elif meta_id.startswith("kitsu:"):
        kitsu_id = meta_id.split(":")[1]

    if not kitsu_id:
        logging.warning("Could not map meta_id=%s to Kitsu ID", meta_id)
        return await respond_with({"meta": {}})

    # Resolve mapped IDs using db cache or resolvers robustly
    resolved_mal, resolved_anilist = await resolve(kitsu_id)
    if not mal_id:
        mal_id = resolved_mal
    if not anilist_id:
        anilist_id = resolved_anilist

    try:
        tasks = [fetch_kitsu_meta(kitsu_id)]
        if anilist_id or mal_id:
            tasks.append(fetch_anizp_metadata(anilist_id=anilist_id, mal_id=mal_id))
        else:
            tasks.append(asyncio.sleep(0, {}))
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        kitsu_data = results[0] if not isinstance(results[0], Exception) else {}
        anizp_data = results[1] if (len(results) > 1 and not isinstance(results[1], Exception)) else {}

        if not kitsu_data:
            return await respond_with({"meta": {}})

        imdb_id = anizp_data.get("mappings", {}).get("imdb_id") if anizp_data else None
        cinemeta_data = {}
        if imdb_id:
            subtype = (kitsu_data.get("data", {}).get("attributes", {}).get("subtype") or "tv").lower()
            media_type = "movie" if subtype == "movie" else "series"
            cinemeta_data = await fetch_cinemeta_metadata(imdb_id, media_type)

        show_filler = user.get("show_filler_tags", True) if user else True
        
        # Offload CPU-bound mapping to worker threads
        run_loop = asyncio.get_running_loop()
        meta = await asyncio.to_thread(
            map_kitsu_to_stremio,
            kitsu_data,
            meta_id,
            anizp_data,
            mal_id,
            show_filler,
            run_loop,
            cinemeta_data
        )

        # Look up description in recommendations cache to retain the trace prefix
        from app.services.recommendations import get_cached_recommendations
        cache = get_cached_recommendations(user_id)
        if cache:
            found_desc = None
            for key in ["rec_items", "loved_items", "liked_items", "item_items", "genre_1_items", "genre_2_items"]:
                items = cache.get(key) or []
                for item in items:
                    cache_id = item.get("id")
                    if not cache_id:
                        continue
                    # 1. Direct match
                    if cache_id == meta_id:
                        found_desc = item.get("description")
                        break
                    # 2. Mapped IDs match
                    c_parts = cache_id.split(":")
                    if len(c_parts) >= 2:
                        c_prefix, c_val = c_parts[0], c_parts[1]
                        if c_prefix == "mal" and mal_id and c_val == str(mal_id):
                            found_desc = item.get("description")
                            break
                        elif c_prefix == "anilist" and anilist_id and c_val == str(anilist_id):
                            found_desc = item.get("description")
                            break
                        elif c_prefix == "kitsu" and kitsu_id and c_val == str(kitsu_id):
                            found_desc = item.get("description")
                            break
                if found_desc:
                    break
            if found_desc:
                meta["description"] = found_desc

        return await respond_with({"meta": meta})
    except Exception as e:
        logging.error("Failed to handle meta for %s: %s", meta_id, e)
        return await respond_with({"meta": {}})

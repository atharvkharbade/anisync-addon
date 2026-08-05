import asyncio
import logging

from quart import Blueprint

from app.lib.id_resolver import resolve, resolve_anilist_to_kitsu, resolve_mal_to_kitsu, resolve_simkl_to_kitsu
from app.routes.utils import is_valid_user_id, rate_limit, respond_with
from app.services.db import get_user
from app.services.http import get_client

meta_bp = Blueprint("meta", __name__)


def clean_imdb_id(val) -> str | None:
    if not val:
        return None
    if isinstance(val, list):
        val = val[0] if val else None
    if not val:
        return None
    val = str(val).strip()
    if val.startswith("[") and val.endswith("]"):
        import ast
        try:
            lst = ast.literal_eval(val)
            if isinstance(lst, list) and len(lst) > 0:
                val = str(lst[0]).strip()
        except Exception:
            val = val.strip("[]'\" ")
    return val.strip("'\" ")


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


def map_kitsu_to_stremio(
    kitsu_data: dict,
    meta_id: str,
    anizp_data: dict = None,
    mal_id: str = None,
    show_filler_tags: bool = True,
    loop=None,
    cinemeta_data: dict = None,
    show_watched_tags: bool = False,
    watched_progress: int = 0,
    title_language: str = "english",
) -> dict:
    data = kitsu_data.get("data", {})
    if not data:
        return {}

    video_base = f"kitsu:{data.get('id')}"

    attributes = data.get("attributes", {})
    titles = attributes.get("titles", {})
    
    if title_language == "english":
        title = titles.get("en") or titles.get("en_us") or attributes.get("canonicalTitle") or titles.get("en_jp") or "Unknown Title"
    elif title_language == "romaji":
        title = titles.get("en_jp") or attributes.get("canonicalTitle") or titles.get("en") or "Unknown Title"
    elif title_language == "japanese":
        title = titles.get("ja_jp") or titles.get("en_jp") or attributes.get("canonicalTitle") or "Unknown Title"
    else:  # default / canonical
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
    background = (
        anizp_fanart or cover_data.get("original") or cover_data.get("large") or cover_data.get("medium") or poster
    )

    imdb_id = clean_imdb_id(anizp_data.get("mappings", {}).get("imdb_id") if anizp_data else None)

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
    included = kitsu_data.get("included") or []

    # Filter and sort episodes by number
    episodes_data = []
    for item in included:
        if item.get("type") == "episodes":
            episodes_data.append(item)

    anizp_episodes = anizp_data.get("episodes", {}) if anizp_data else {}

    if subtype == "movie":
        videos.append(
            {
                "id": video_base,
                "title": title,
                "episode": 1,
                "season": 1,
                "released": start_date + "T00:00:00Z" if start_date else None,
                "overview": synopsis,
                "thumbnail": background or poster,
            }
        )
    else:
        # Determine the maximum episode number we should display to prevent truncation
        kitsu_ep_count = attributes.get("episodeCount") or 0

        max_kitsu_ep = 0
        if episodes_data:
            max_kitsu_ep = max([x.get("attributes", {}).get("number") or 0 for x in episodes_data])

        max_anizp_ep = 0
        if anizp_episodes:
            try:
                max_anizp_ep = max([int(k) for k in anizp_episodes.keys() if k.isdigit()])
            except ValueError:
                pass

        total_ep = max(kitsu_ep_count, max_kitsu_ep, max_anizp_ep)
        if total_ep == 0:
            total_ep = 12  # Default fallback

        # Create a mapping of episode number to Kitsu episode data for O(1) lookup
        kitsu_ep_map = {}
        for ep in episodes_data:
            num = ep.get("attributes", {}).get("number")
            if num:
                try:
                    kitsu_ep_map[int(num)] = ep
                except (ValueError, TypeError):
                    pass

        for i in range(1, total_ep + 1):
            ep_num = i
            kitsu_ep = kitsu_ep_map.get(i)
            anizp_ep = anizp_episodes.get(str(i)) or {}

            # Extract attributes from Kitsu episode if available
            attrs = kitsu_ep.get("attributes", {}) if kitsu_ep else {}

            if title_language == "english":
                ep_title = (
                    anizp_ep.get("title", {}).get("en")
                    or attrs.get("canonicalTitle")
                    or anizp_ep.get("title", {}).get("x-jat")
                    or f"Episode {ep_num}"
                )
            elif title_language == "romaji":
                ep_title = (
                    anizp_ep.get("title", {}).get("x-jat")
                    or attrs.get("canonicalTitle")
                    or anizp_ep.get("title", {}).get("en")
                    or f"Episode {ep_num}"
                )
            elif title_language == "japanese":
                ep_title = (
                    anizp_ep.get("title", {}).get("ja")
                    or anizp_ep.get("title", {}).get("x-jat")
                    or attrs.get("canonicalTitle")
                    or f"Episode {ep_num}"
                )
            else:  # default / canonical
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
                    from app.routes.catalog import background_fetch_and_cache_filler, currently_fetching_pairs

                    pair = (str(mal_id), ep_num)
                    if pair not in currently_fetching_pairs:
                        currently_fetching_pairs.add(pair)
                        if loop and loop.is_running():
                            try:
                                asyncio.run_coroutine_threadsafe(
                                    background_fetch_and_cache_filler(mal_id, ep_num), loop
                                )
                            except Exception:
                                pass

            if is_filler:
                ep_title = f"[Filler] {ep_title}"
            if show_watched_tags and ep_num <= watched_progress:
                ep_title = f"[Watched] {ep_title}"

            released_str = None
            if released and isinstance(released, str) and released.strip():
                released = released.strip()
                if "T" not in released:
                    released_str = released + "T00:00:00Z"
                else:
                    released_str = released

            videos.append(
                {
                    "id": f"{video_base}:{ep_num}",
                    "title": ep_title,
                    "episode": ep_num,
                    "season": 1,
                    "released": released_str,
                    "overview": overview,
                    "thumbnail": thumbnail,
                }
            )

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
    simkl_id = None

    if meta_id.startswith(("mal:", "mal-", "mal_")):
        mal_id = meta_id[4:]
        kitsu_id = await resolve_mal_to_kitsu(mal_id)
    elif meta_id.startswith(("anilist:", "anilist-", "anilist_")):
        anilist_id = meta_id[8:]
        kitsu_id = await resolve_anilist_to_kitsu(anilist_id)
    elif meta_id.startswith(("simkl:", "simkl-", "simkl_")):
        simkl_id = meta_id[6:]
        kitsu_id = await resolve_simkl_to_kitsu(simkl_id)
    elif meta_id.startswith(("kitsu:", "kitsu-", "kitsu_")):
        kitsu_id = meta_id[6:]

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

        imdb_id = clean_imdb_id(anizp_data.get("mappings", {}).get("imdb_id") if anizp_data else None)
        if not imdb_id and kitsu_id:
            from app.services.db import get_cached_ids, db
            cached_ids = get_cached_ids(kitsu_id)
            if cached_ids:
                imdb_id = clean_imdb_id(cached_ids.get("imdb_id"))
            if not imdb_id:
                try:
                    fribb_doc = db.fribb_mappings.find_one({"kitsu_id": int(kitsu_id)})
                    if fribb_doc:
                        imdb_id = clean_imdb_id(fribb_doc.get("imdb_id"))
                except Exception as e:
                    logging.warning("Failed to query fribb_mappings for imdb_id: %s", e)
            if imdb_id:
                if not isinstance(anizp_data, dict):
                    anizp_data = {}
                if "mappings" not in anizp_data:
                    anizp_data["mappings"] = {}
                anizp_data["mappings"]["imdb_id"] = imdb_id

        cinemeta_data = {}
        if imdb_id:
            subtype = (kitsu_data.get("data", {}).get("attributes", {}).get("subtype") or "tv").lower()
            media_type = "movie" if subtype == "movie" else "series"
            cinemeta_data = await fetch_cinemeta_metadata(imdb_id, media_type)

        # Resolve simkl_id if not present but we have kitsu_id
        if not simkl_id and kitsu_id:
            from app.services.db import get_cached_ids

            cached_ids = get_cached_ids(kitsu_id)
            if cached_ids:
                simkl_id = cached_ids.get("simkl_id")

        show_filler = user.get("show_filler_tags", True) if user else True
        show_watched = user.get("show_watched_tags", False) if user else False
        watched_progress = 0
        if show_watched:
            from app.services.db import get_user_watch_progress

            watched_progress = get_user_watch_progress(user_id, mal_id=mal_id, anilist_id=anilist_id, simkl_id=simkl_id)

        title_lang = user.get("title_language", "english") if user else "english"

        # Offload CPU-bound mapping to worker threads
        run_loop = asyncio.get_running_loop()
        meta = await asyncio.to_thread(
            map_kitsu_to_stremio,
            kitsu_data,
            meta_id,
            anizp_data=anizp_data,
            mal_id=mal_id,
            show_filler_tags=show_filler,
            loop=run_loop,
            cinemeta_data=cinemeta_data,
            show_watched_tags=show_watched,
            watched_progress=watched_progress,
            title_language=title_lang,
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

        # Apply user preferred Metadata Provider override (MAL / AniList with Kitsu fallback)
        await apply_metadata_provider_override(meta, user, mal_id, anilist_id)

        # Apply custom poster provider if configured
        if (user.get("poster_provider") and user.get("poster_provider") != "none") or user.get("rpdb_api_key"):
            from app.services.poster_service import get_rpdb_poster_url

            meta["poster"] = get_rpdb_poster_url(
                user=user,
                media_type=meta.get("type", "series"),
                kitsu_id=kitsu_id,
                mal_id=mal_id,
                anilist_id=anilist_id,
                fallback_poster=meta.get("poster"),
            )

        notice = build_expired_trackers_notice(user)
        if notice:
            curr_desc = meta.get("description", "")
            meta["description"] = f"{notice}\n\n{curr_desc}" if curr_desc else notice

        return await respond_with({"meta": meta})
    except Exception as e:
        logging.error("Failed to handle meta for %s: %s", meta_id, e)
        return await respond_with({"meta": {}})


def build_expired_trackers_notice(user: dict | None) -> str | None:
    if not user:
        return None

    import time
    now = time.time()
    grace_period = 7 * 86400  # 7 days in seconds
    expired_names = []

    trackers = [
        ("AniList", "anilist_token_expired", "anilist_expired_at"),
        ("MyAnimeList", "mal_token_expired", "mal_expired_at"),
        ("Simkl", "simkl_token_expired", "simkl_expired_at"),
    ]

    for name, flag_key, timestamp_key in trackers:
        if user.get(flag_key):
            expired_at = user.get(timestamp_key)
            if expired_at is None or (now - float(expired_at)) <= grace_period:
                expired_names.append(name)

    if not expired_names:
        return None

    if len(expired_names) == 1:
        trackers_str = expired_names[0]
    elif len(expired_names) == 2:
        trackers_str = f"{expired_names[0]} & {expired_names[1]}"
    else:
        trackers_str = f"{', '.join(expired_names[:-1])} & {expired_names[-1]}"

    return f"⚠️ Your {trackers_str} session has expired. You can re-login via the website."


async def apply_metadata_provider_override(meta: dict, user: dict, mal_id: str | None, anilist_id: str | None):
    provider = (user.get("metadata_provider", "kitsu") or "kitsu").lower() if user else "kitsu"
    if provider == "mal" and mal_id:
        try:
            from app.api.jikan import get_anime_by_id
            mal_data = await get_anime_by_id(mal_id)
            if mal_data:
                images = mal_data.get("images", {})
                jpg_img = images.get("jpg", {}) or images.get("webp", {})
                poster_url = jpg_img.get("large_image_url") or jpg_img.get("image_url")
                if poster_url:
                    meta["poster"] = poster_url
                synopsis = mal_data.get("synopsis")
                if synopsis:
                    meta["description"] = synopsis
                score = mal_data.get("score")
                if score:
                    meta["imdbRating"] = str(score)
        except Exception as e:
            logging.warning("Failed to apply MAL metadata override for mal_id=%s: %s", mal_id, e)
    elif provider == "anilist" and anilist_id:
        try:
            from app.api.anilist import get_media_status
            token = user.get("anilist_token", "") if user else ""
            al_data = await get_media_status(token, int(anilist_id))
            if al_data:
                cover = al_data.get("coverImage", {})
                poster_url = cover.get("extraLarge") or cover.get("large")
                if poster_url:
                    meta["poster"] = poster_url
                banner_url = al_data.get("bannerImage")
                if banner_url:
                    meta["background"] = banner_url
                desc = al_data.get("description")
                if desc:
                    import re
                    clean_desc = re.sub(r'<[^>]+>', '', desc)
                    meta["description"] = clean_desc
                avg_score = al_data.get("averageScore")
                if avg_score:
                    meta["imdbRating"] = f"{avg_score / 10:.1f}"
        except Exception as e:
            logging.warning("Failed to apply AniList metadata override for anilist_id=%s: %s", anilist_id, e)

import asyncio
import logging
import urllib.parse

from quart import Blueprint, request

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
    if not anilist_id and not mal_id:
        return {}

    from app.services.db import db
    import datetime

    cache_key = f"al_{anilist_id}" if anilist_id else f"mal_{mal_id}"
    col = db.get_collection("anizp_meta_cache")
    now = datetime.datetime.utcnow()
    try:
        cached = col.find_one({"key": cache_key})
        if cached and cached.get("expires_at") > now:
            return cached.get("data", {})
    except Exception as e:
        logging.error("Failed to read anizp_meta_cache for %s: %s", cache_key, e)

    url = "https://api.ani.zip/mappings"
    params = {}
    if anilist_id:
        params["anilist_id"] = anilist_id
    elif mal_id:
        params["mal_id"] = mal_id

    try:
        client = get_client()
        resp = await client.get(url, params=params, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            ttl = datetime.timedelta(hours=2)
            try:
                col.update_one(
                    {"key": cache_key},
                    {
                        "$set": {
                            "key": cache_key,
                            "data": data,
                            "expires_at": now + ttl,
                            "updated_at": now,
                        }
                    },
                    upsert=True,
                )
            except Exception as ex:
                logging.error("Failed to write anizp_meta_cache for %s: %s", cache_key, ex)
            return data
    except Exception as e:
        logging.warning("Failed to fetch rich metadata from ani.zip: %s", e)
    return {}


async def fetch_cinemeta_metadata(imdb_id: str, media_type: str, is_releasing: bool = False) -> dict:
    from app.services.db import db
    import datetime

    col = db.get_collection("cinemeta_meta_cache")
    now = datetime.datetime.utcnow()
    try:
        cached = col.find_one({"imdb_id": str(imdb_id), "media_type": str(media_type)})
        if cached and cached.get("expires_at") > now:
            return cached.get("data", {})
    except Exception as e:
        logging.error("Failed to read cinemeta_meta_cache for %s: %s", imdb_id, e)

    url = f"https://v3-cinemeta.strem.io/meta/{media_type}/{imdb_id}.json"
    try:
        client = get_client()
        resp = await client.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json().get("meta", {})
            ttl = datetime.timedelta(hours=4) if is_releasing else datetime.timedelta(days=7)
            try:
                col.update_one(
                    {"imdb_id": str(imdb_id), "media_type": str(media_type)},
                    {
                        "$set": {
                            "imdb_id": str(imdb_id),
                            "media_type": str(media_type),
                            "data": data,
                            "is_releasing": is_releasing,
                            "expires_at": now + ttl,
                            "updated_at": now,
                        }
                    },
                    upsert=True,
                )
            except Exception as ex:
                logging.error("Failed to write cinemeta_meta_cache for %s: %s", imdb_id, ex)
            return data
    except Exception as e:
        logging.warning("Failed to fetch metadata from Cinemeta: %s", e)
    return {}


KITSU_API_BASE = "https://kitsu.io/api/edge"
TIMEOUT = 10


async def fetch_kitsu_meta(kitsu_id: str) -> dict:
    from app.services.db import db
    import datetime

    col = db.get_collection("kitsu_meta_cache")
    now = datetime.datetime.utcnow()
    try:
        cached = col.find_one({"kitsu_id": str(kitsu_id)})
        if cached and cached.get("expires_at") > now:
            return cached.get("data", {})
    except Exception as e:
        logging.error("Failed to read kitsu_meta_cache for %s: %s", kitsu_id, e)

    url = f"{KITSU_API_BASE}/anime/{kitsu_id}"
    params = {"include": "episodes"}
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }
    client = get_client()
    try:
        resp = await client.get(url, params=params, headers=headers, timeout=TIMEOUT)
        if resp.status_code != 200:
            logging.error("Kitsu API returned status %s for id %s", resp.status_code, kitsu_id)
            return {}
        data = resp.json()

        status = (data.get("data", {}).get("attributes", {}).get("status") or "").lower()
        if status in ["current", "releasing", "unreleased", "not_yet_released"]:
            ttl = datetime.timedelta(hours=2)
        else:
            ttl = datetime.timedelta(days=7)

        try:
            col.update_one(
                {"kitsu_id": str(kitsu_id)},
                {
                    "$set": {
                        "kitsu_id": str(kitsu_id),
                        "data": data,
                        "status": status,
                        "expires_at": now + ttl,
                        "updated_at": now,
                    }
                },
                upsert=True,
            )
        except Exception as ex:
            logging.error("Failed to write kitsu_meta_cache for %s: %s", kitsu_id, ex)
        return data
    except Exception as e:
        logging.error("Failed to fetch Kitsu meta for %s: %s", kitsu_id, e)
        return {}


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
    episodes_provider: str = "anizp",
    backdrop_provider: str = "fanart",
    poster_provider: str = "anilist",
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
    kitsu_poster = poster_data.get("original") or poster_data.get("large") or poster_data.get("medium") or ""
    if poster_provider == "kitsu":
        poster = kitsu_poster or anizp_poster or ""
    else:
        poster = anizp_poster or kitsu_poster or ""
    cover_data = attributes.get("coverImage") or {}
    kitsu_cover = cover_data.get("original") or cover_data.get("large") or cover_data.get("medium")

    if backdrop_provider == "kitsu":
        background = kitsu_cover or anizp_fanart or (cinemeta_data.get("background") if cinemeta_data else "") or poster
    else:  # fanart (default)
        background = (
            (cinemeta_data.get("background") if cinemeta_data else None)
            or anizp_fanart
            or kitsu_cover
            or poster
        )

    imdb_id = clean_imdb_id(anizp_data.get("mappings", {}).get("imdb_id") if anizp_data else None)

    logo = anizp_logo
    if not logo and cinemeta_data:
        logo = cinemeta_data.get("logo")
    if not logo and imdb_id:
        logo = f"https://images.metahub.space/logo/medium/{imdb_id}/img"

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
            if episodes_provider == "kitsu":
                overview = attrs.get("synopsis") or anizp_ep.get("overview") or anizp_ep.get("summary") or ""
                thumbnail = (
                    (attrs.get("thumbnail") or {}).get("original")
                    or (attrs.get("thumbnail") or {}).get("large")
                    or anizp_ep.get("image")
                    or background
                )
            else:  # anizp, mal, or default
                overview = anizp_ep.get("overview") or anizp_ep.get("summary") or attrs.get("synopsis") or ""
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
    meta_id = urllib.parse.unquote(meta_id)
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
            k_status = (kitsu_data.get("data", {}).get("attributes", {}).get("status") or "").lower()
            is_releasing = k_status in ["current", "releasing", "unreleased", "not_yet_released"]
            subtype = (kitsu_data.get("data", {}).get("attributes", {}).get("subtype") or "tv").lower()
            media_type = "movie" if subtype == "movie" else "series"
            cinemeta_data = await fetch_cinemeta_metadata(imdb_id, media_type, is_releasing=is_releasing)

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
        effective_provs = get_effective_meta_providers(user)

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
            episodes_provider=effective_provs.get("episodes", "anizp"),
            backdrop_provider=effective_provs.get("backdrop", "fanart"),
            poster_provider=effective_provs.get("poster", "anilist"),
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
                rec_reason = found_desc.split("\n\n")[0].strip()
            else:
                rec_reason = None

        # Apply user preferred Metadata Provider override (MAL / AniList with Kitsu fallback)
        mal_data_override = await apply_metadata_provider_override(meta, user, mal_id, anilist_id, rec_prefix=rec_reason if "rec_reason" in locals() else None)

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
            meta["description"] = f"{notice}\n\n\n{curr_desc}" if curr_desc else notice

        # Collect dynamic metadata headers
        dynamic_headers = []

        user_status_hdr = build_user_status_header(user_id, mal_id=mal_id, anilist_id=anilist_id, simkl_id=simkl_id)
        if user_status_hdr:
            dynamic_headers.append(user_status_hdr)

        airing_prov = effective_provs.get("airing", "anilist")
        al_data = None
        if airing_prov == "anilist" and anilist_id:
            try:
                from app.api.anilist import get_media_status

                token = user.get("anilist_token", "") if user else ""
                al_data = await get_media_status(token, int(anilist_id))
            except Exception as e:
                logging.debug("Could not fetch AniList media status for airing countdown: %s", e)

        mal_data_airing = mal_data_override if (airing_prov == "mal" and mal_data_override) else None
        if airing_prov == "mal" and not mal_data_airing and mal_id:
            try:
                from app.api.jikan import get_anime_by_id

                mal_data_airing = await get_anime_by_id(mal_id)
            except Exception:
                pass

        if airing_prov != "kitsu":
            next_airing_hdr = build_next_airing_header(al_data, mal_data=mal_data_airing)
            if next_airing_hdr:
                dynamic_headers.append(next_airing_hdr)

        filler_arc_hdr = build_filler_arc_header(anizp_data, watched_progress=watched_progress)
        if filler_arc_hdr:
            dynamic_headers.append(filler_arc_hdr)

        if dynamic_headers:
            header_text = "\n".join(dynamic_headers)
            curr_desc = meta.get("description", "")
            meta["description"] = f"{header_text}\n\n{curr_desc}" if curr_desc else header_text

        return await respond_with({"meta": meta})
    except Exception as e:
        logging.error("Failed to handle meta for %s: %s", meta_id, e)
        return await respond_with({"meta": {}})


def extract_rec_prefix(desc: str | None) -> tuple[str | None, str | None]:
    """
    If description contains a recommendation reason prefix (e.g. 'Inspired by...',
    'Community Recommendation', 'Popular ... collection', or Gemini reasoning),
    extract the prefix and the underlying synopsis.
    """
    if not desc:
        return None, None

    known_markers = [
        "inspired by",
        "community recommendation",
        "recommended",
        "popular",
        "trending",
        "franchise",
        "because you",
        "collection",
        "based on your",
    ]

    parts = desc.split("\n\n", 1)
    first_part = parts[0].strip()

    for marker in known_markers:
        if marker in first_part.lower():
            synopsis = parts[1].strip() if len(parts) > 1 else ""
            return first_part, synopsis

    # Handle custom Gemini 1-sentence personalized descriptions (short first paragraph followed by synopsis)
    if len(parts) > 1 and len(first_part) <= 300:
        return first_part, parts[1].strip()

    return None, desc


def build_user_status_header(user_id: str, mal_id: str | None, anilist_id: str | None, simkl_id: str | None) -> str | None:
    from app.services.db import get_user_anime_meta_status

    status_info = get_user_anime_meta_status(user_id, mal_id=mal_id, anilist_id=anilist_id, simkl_id=simkl_id)
    if not status_info:
        return None

    status = (status_info.get("status") or "").lower()
    progress = status_info.get("progress") or 0
    total_eps = status_info.get("total_episodes") or 0
    score = status_info.get("score") or 0

    status_display_map = {
        "watching": "Watching",
        "current": "Watching",
        "completed": "Completed",
        "planning": "Plan to Watch",
        "plan_to_watch": "Plan to Watch",
        "on_hold": "On Hold",
        "paused": "On Hold",
        "dropped": "Dropped",
    }
    status_title = status_display_map.get(status, status.capitalize() if status else "Tracked")

    parts = [f"Status: {status_title}"]
    if status in ["watching", "current", "on_hold", "paused", "dropped", "completed"] and progress > 0:
        if total_eps > 0:
            parts.append(f"Progress: {progress}/{total_eps} Ep")
        else:
            parts.append(f"Progress: {progress} Ep")

    if score > 0:
        parts.append(f"Your Rating: {score}/10")

    return f"[{' • '.join(parts)}]"


def build_next_airing_header(anilist_data: dict | None = None, mal_data: dict | None = None) -> str | None:
    if anilist_data:
        al_status = anilist_data.get("status")
        # Only check nextAiringEpisode if show is actively releasing or unreleased
        if al_status in ["RELEASING", "NOT_YET_RELEASED"] or not al_status:
            next_ep = anilist_data.get("nextAiringEpisode")
            if next_ep:
                ep_num = next_ep.get("episode")
                time_until = next_ep.get("timeUntilAiring")
                if time_until is not None and ep_num is not None and time_until > 0:
                    if time_until < 3600:
                        mins = max(1, time_until // 60)
                        time_str = f"in {mins}m"
                    elif time_until < 86400:
                        hours = max(1, time_until // 3600)
                        time_str = f"in {hours}h"
                    else:
                        days = max(1, time_until // 86400)
                        time_str = f"in {days}d"
                    return f"[Next Airing: Episode {ep_num} releases {time_str}]"

    if mal_data:
        mal_status = (mal_data.get("status") or "").lower().replace(" ", "_")
        # Strictly ignore historical broadcast slots for finished anime
        if mal_status in ["currently_airing", "not_yet_aired"]:
            broadcast = mal_data.get("broadcast") or {}
            day = broadcast.get("day") or broadcast.get("day_of_the_week")
            time_str = broadcast.get("time") or broadcast.get("start_time")
            if day and time_str:
                return f"[Next Airing: Broadcasts {day} at {time_str} JST]"

    return None


def build_filler_arc_header(anizp_data: dict | None, watched_progress: int = 0) -> str | None:
    if not anizp_data:
        return None

    episodes = anizp_data.get("episodes") or {}
    if not episodes:
        return None

    filler_eps = []
    for ep_key, ep_info in episodes.items():
        if isinstance(ep_info, dict) and ep_info.get("isFiller"):
            try:
                ep_num = int(ep_key)
                filler_eps.append(ep_num)
            except ValueError:
                pass

    if not filler_eps:
        return None

    filler_eps.sort()

    ranges = []
    start = filler_eps[0]
    end = filler_eps[0]

    for ep in filler_eps[1:]:
        if ep == end + 1:
            end = ep
        else:
            ranges.append((start, end))
            start = ep
            end = ep
    ranges.append((start, end))

    range_strs = []
    for r_start, r_end in ranges:
        if r_start == r_end:
            range_strs.append(f"Ep {r_start}")
        else:
            range_strs.append(f"Episodes {r_start}–{r_end}")

    if not range_strs:
        return None

    for r_start, r_end in ranges:
        if r_start <= watched_progress <= r_end:
            r_label = f"Ep {r_start}" if r_start == r_end else f"Episodes {r_start}–{r_end}"
            return f"[Current Filler Arc: {r_label}]"

    for r_start, r_end in ranges:
        if watched_progress < r_start and (r_start - watched_progress) <= 10:
            r_label = f"Ep {r_start}" if r_start == r_end else f"Episodes {r_start}–{r_end}"
            return f"[Upcoming Filler Arc: {r_label}]"

    if watched_progress == 0 and len(range_strs) <= 3:
        return f"[Filler Guide: {', '.join(range_strs)} are non-canon fillers]"

    return None


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
        session_word = "session has"
    elif len(expired_names) == 2:
        trackers_str = f"{expired_names[0]} & {expired_names[1]}"
        session_word = "sessions have"
    else:
        trackers_str = f"{', '.join(expired_names[:-1])} & {expired_names[-1]}"
        session_word = "sessions have"

    return f"Your {trackers_str} {session_word} expired. You can re-login via the website."



async def get_banner_aspect_ratio(banner_url: str) -> float:
    """Check banner aspect ratio with MongoDB caching. Returns 2.5 on error to trigger safe fallback."""
    if not banner_url:
        return 2.5
    try:
        from app.services.db import db

        col = db.get_collection("banner_ratios")
        doc = col.find_one({"url": banner_url})
        if doc and "ratio" in doc:
            return float(doc["ratio"])

        import httpx
        from PIL import Image
        import io

        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(banner_url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200 and len(resp.content) <= 10 * 1024 * 1024:
                img = Image.open(io.BytesIO(resp.content))
                bw, bh = img.size
                ratio = round(bw / float(bh), 2) if bh > 0 else 2.5
                try:
                    col.update_one(
                        {"url": banner_url},
                        {"$set": {"url": banner_url, "ratio": ratio}},
                        upsert=True,
                    )
                except Exception as ex:
                    logging.warning("Failed to cache banner ratio for %s: %s", banner_url, ex)
                return ratio
    except Exception as e:
        logging.warning("Failed to resolve banner aspect ratio for %s: %s", banner_url, e)
    return 2.5


def get_effective_meta_providers(user: dict | None) -> dict:
    pref = (user.get("metadata_provider", "kitsu") or "kitsu").lower() if user else "kitsu"
    if pref == "custom":
        legacy_artwork = (user.get("meta_artwork_provider") or "anilist").lower()
        return {
            "synopsis": (user.get("meta_synopsis_provider") or "kitsu").lower(),
            "episodes": (user.get("meta_episodes_provider") or "anizp").lower(),
            "poster": (user.get("meta_poster_provider") or legacy_artwork).lower(),
            "backdrop": (user.get("meta_backdrop_provider") or "fanart").lower(),
            "airing": (user.get("meta_airing_provider") or "anilist").lower(),
        }
    elif pref == "mal":
        return {
            "synopsis": "mal",
            "episodes": "anizp",
            "poster": "mal",
            "backdrop": "fanart",
            "airing": "mal",
        }
    elif pref == "anilist":
        return {
            "synopsis": "anilist",
            "episodes": "anizp",
            "poster": "anilist",
            "backdrop": "fanart",
            "airing": "anilist",
        }
    else:  # kitsu
        return {
            "synopsis": "kitsu",
            "episodes": "anizp",
            "poster": "kitsu",
            "backdrop": "fanart",
            "airing": "anilist",
        }


async def apply_metadata_provider_override(
    meta: dict,
    user: dict,
    mal_id: str | None,
    anilist_id: str | None,
    rec_prefix: str | None = None,
) -> dict | None:
    effective = get_effective_meta_providers(user)
    synopsis_prov = effective["synopsis"]
    poster_prov = effective["poster"]
    backdrop_prov = effective["backdrop"]

    mal_data_ret = None
    if not rec_prefix:
        rec_prefix, _ = extract_rec_prefix(meta.get("description"))

    # 1. Fetch MAL data if needed for synopsis or poster
    mal_data = None
    if (synopsis_prov == "mal" or poster_prov == "mal") and mal_id:
        try:
            from app.api.jikan import get_anime_by_id

            mal_data = await get_anime_by_id(mal_id)
            if mal_data:
                mal_data_ret = mal_data
        except Exception as e:
            logging.warning("Failed to fetch MAL metadata for mal_id=%s: %s", mal_id, e)

    # 2. Fetch AniList data if needed for synopsis, poster, or backdrop
    al_data = None
    if (synopsis_prov == "anilist" or poster_prov == "anilist" or backdrop_prov == "anilist") and anilist_id:
        try:
            from app.api.anilist import get_media_status

            token = user.get("anilist_token", "") if user else ""
            al_data = await get_media_status(token, int(anilist_id))
        except Exception as e:
            logging.warning("Failed to fetch AniList metadata for anilist_id=%s: %s", anilist_id, e)

    # 3. Apply Synopsis & Rating
    if synopsis_prov == "mal" and mal_data:
        synopsis = mal_data.get("synopsis")
        if synopsis:
            meta["description"] = f"{rec_prefix}\n\n{synopsis}" if rec_prefix else synopsis
        score = mal_data.get("score")
        if score:
            meta["imdbRating"] = str(score)
    elif synopsis_prov == "anilist" and al_data:
        desc = al_data.get("description")
        if desc:
            import re

            clean_desc = re.sub(r"<[^>]+>", "", desc)
            meta["description"] = f"{rec_prefix}\n\n{clean_desc}" if rec_prefix else clean_desc
        avg_score = al_data.get("averageScore")
        if avg_score:
            meta["imdbRating"] = f"{avg_score / 10:.1f}"

    # 4. Apply Poster Override
    if poster_prov == "mal" and mal_data:
        images = mal_data.get("images", {})
        jpg_img = images.get("jpg", {}) or images.get("webp", {})
        poster_url = jpg_img.get("large_image_url") or jpg_img.get("image_url")
        if poster_url:
            meta["poster"] = poster_url
    elif poster_prov == "anilist" and al_data:
        cover = al_data.get("coverImage", {})
        poster_url = cover.get("extraLarge") or cover.get("large")
        if poster_url:
            meta["poster"] = poster_url

    # 5. Apply Backdrop Override
    if backdrop_prov == "anilist" and al_data:
        banner_url = al_data.get("bannerImage")
        if banner_url:
            ratio = await get_banner_aspect_ratio(banner_url)
            if ratio <= 2.0:
                meta["background"] = banner_url
            # Else (ratio > 2.0 ultra-wide banner): skip and retain Fanart/Kitsu/Cinemeta fallback

    return mal_data_ret

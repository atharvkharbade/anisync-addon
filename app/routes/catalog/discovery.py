import asyncio
import datetime
import logging
import random

from quart import request

from app.api import anilist as anilist_api
from app.routes.catalog.formatting import format_catalog_metas
from app.routes.catalog.sorting import (
    apply_catalog_dub_filter,
    get_catalog_sorting,
    is_catalog_shuffle_enabled,
    sort_watchlist_items,
)
from app.routes.utils import respond_with

DISCOVERY_CAT_IDS = [
    "anisync_trending",
    "anisync_highest_rated",
    "anisync_most_popular",
    "anisync_top_airing",
    "anisync_seasonal",
    "anisync_schedule",
    "anisync_spotlight",
]


async def update_discovery_catalogs_cache() -> dict:
    import re
    from app.services.db import db
    from app.lib.id_resolver import bulk_resolve_to_kitsu

    now = datetime.datetime.utcnow()
    month = now.month
    year = now.year
    if month in [1, 2, 3]:
        cur_season = "WINTER"
        next_season = "SPRING"
        next_year = year
    elif month in [4, 5, 6]:
        cur_season = "SPRING"
        next_season = "SUMMER"
        next_year = year
    elif month in [7, 8, 9]:
        cur_season = "SUMMER"
        next_season = "FALL"
        next_year = year
    else:
        cur_season = "FALL"
        next_season = "WINTER"
        next_year = year + 1

    query = f"""
    fragment MediaFields on Media {{
      id
      idMal
      genres
      format
      duration
      averageScore
      popularity
      episodes
      startDate {{ year }}
      seasonYear
      title {{
        english
        userPreferred
        romaji
      }}
      coverImage {{
        large
      }}
      bannerImage
      description
      nextAiringEpisode {{
        airingAt
        episode
        timeUntilAiring
      }}
    }}
    query {{
      trending: Page(page: 1, perPage: 50) {{
        media(type: ANIME, sort: TRENDING_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      highestRated: Page(page: 1, perPage: 50) {{
        media(type: ANIME, sort: SCORE_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      mostPopular: Page(page: 1, perPage: 50) {{
        media(type: ANIME, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      topAiring: Page(page: 1, perPage: 50) {{
        media(type: ANIME, status: RELEASING, sort: SCORE_DESC, popularity_greater: 2000, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonal: Page(page: 1, perPage: 50) {{
        media(type: ANIME, season: {cur_season}, seasonYear: {year}, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonalNext: Page(page: 1, perPage: 50) {{
        media(type: ANIME, season: {next_season}, seasonYear: {next_year}, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonalUpcoming: Page(page: 1, perPage: 50) {{
        media(type: ANIME, status: NOT_YET_RELEASED, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonalWinter: Page(page: 1, perPage: 50) {{
        media(type: ANIME, season: WINTER, seasonYear: {year}, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonalSpring: Page(page: 1, perPage: 50) {{
        media(type: ANIME, season: SPRING, seasonYear: {year}, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonalSummer: Page(page: 1, perPage: 50) {{
        media(type: ANIME, season: SUMMER, seasonYear: {year}, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonalFall: Page(page: 1, perPage: 50) {{
        media(type: ANIME, season: FALL, seasonYear: {year}, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      schedule: Page(page: 1, perPage: 50) {{
        media(type: ANIME, status: RELEASING, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      spotlightMovies: Page(page: 1, perPage: 50) {{
        media(type: ANIME, format: MOVIE, sort: SCORE_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      spotlightNewMovies: Page(page: 1, perPage: 50) {{
        media(type: ANIME, format: MOVIE, sort: START_DATE_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      spotlightOva: Page(page: 1, perPage: 50) {{
        media(type: ANIME, format_in: [OVA, SPECIAL], sort: SCORE_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      spotlightClassics: Page(page: 1, perPage: 50) {{
        media(type: ANIME, startDate_lesser: 20120101, sort: SCORE_DESC, popularity_greater: 10000, isAdult: false) {{
          ...MediaFields
        }}
      }}
    }}
    """

    data = {}
    try:
        res = await anilist_api._gql(None, query)
        data = res.get("data") or {}
    except Exception as e:
        logging.warning("AniList GraphQL discovery fetch failed (%s), relying on Jikan data...", e)

    # Fetch Jikan and Kitsu discovery lists in parallel
    from app.api.jikan import get_airing_schedule, get_season_now, get_top_anime

    def _fetch_kitsu_sync(query_str: str) -> list:
        import json as _json
        import urllib.request
        try:
            url = f"https://kitsu.io/api/edge/anime?{query_str}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "application/vnd.api+json",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read())
                if isinstance(data, dict):
                    return data.get("data") or []
        except Exception as ex:
            logging.warning("Kitsu discovery fetch error (%s): %s", query_str, ex)
        return []

    async def fetch_kitsu_discovery(query_str: str) -> list:
        return await asyncio.to_thread(_fetch_kitsu_sync, query_str)

    try:
        jikan_pop_task = asyncio.create_task(get_top_anime(filter_by="bypopularity", page=1))
        jikan_airing_task = asyncio.create_task(get_top_anime(filter_by="airing", page=1))
        jikan_top_task = asyncio.create_task(get_top_anime(page=1))
        jikan_movie_task = asyncio.create_task(get_top_anime(type_filter="movie", page=1))
        jikan_season_task = asyncio.create_task(get_season_now(page=1))
        jikan_schedule_task = asyncio.create_task(get_airing_schedule(page=1))
        jikan_fav_task = asyncio.create_task(get_top_anime(filter_by="favorite", page=1))

        kitsu_pop_task = asyncio.create_task(fetch_kitsu_discovery("sort=-userCount&page%5Blimit%5D=25"))
        kitsu_rating_task = asyncio.create_task(fetch_kitsu_discovery("sort=-averageRating&page%5Blimit%5D=25"))
        kitsu_airing_task = asyncio.create_task(fetch_kitsu_discovery("filter%5Bstatus%5D=current&sort=-userCount&page%5Blimit%5D=25"))
        kitsu_season_task = asyncio.create_task(fetch_kitsu_discovery("filter%5Bstatus%5D=current&sort=-createdAt&page%5Blimit%5D=25"))
        kitsu_movie_task = asyncio.create_task(fetch_kitsu_discovery("filter%5Bsubtype%5D=movie&sort=-averageRating&page%5Blimit%5D=25"))

        (
            jikan_pop,
            jikan_airing,
            jikan_top,
            jikan_movies,
            jikan_season_now,
            jikan_schedule,
            jikan_fav,
            kitsu_pop,
            kitsu_rating,
            kitsu_airing,
            kitsu_season,
            kitsu_movie,
        ) = await asyncio.gather(
            jikan_pop_task,
            jikan_airing_task,
            jikan_top_task,
            jikan_movie_task,
            jikan_season_task,
            jikan_schedule_task,
            jikan_fav_task,
            kitsu_pop_task,
            kitsu_rating_task,
            kitsu_airing_task,
            kitsu_season_task,
            kitsu_movie_task,
            return_exceptions=True,
        )
    except Exception as ex:
        logging.warning("Error fetching supplemental discovery data: %s", ex)
        jikan_pop = []
        jikan_airing = []
        jikan_top = []
        jikan_movies = []
        jikan_season_now = []
        jikan_schedule = []
        jikan_fav = []
        kitsu_pop = []
        kitsu_rating = []
        kitsu_airing = []
        kitsu_season = []
        kitsu_movie = []

    # Safe unwrapping of exceptions from return_exceptions=True
    def _safe_list(val):
        return val if isinstance(val, list) else []

    jikan_pop = _safe_list(jikan_pop)
    jikan_airing = _safe_list(jikan_airing)
    jikan_top = _safe_list(jikan_top)
    jikan_movies = _safe_list(jikan_movies)
    jikan_season_now = _safe_list(jikan_season_now)
    jikan_schedule = _safe_list(jikan_schedule)
    jikan_fav = _safe_list(jikan_fav)
    kitsu_pop = _safe_list(kitsu_pop)
    kitsu_rating = _safe_list(kitsu_rating)
    kitsu_airing = _safe_list(kitsu_airing)
    kitsu_season = _safe_list(kitsu_season)
    kitsu_movie = _safe_list(kitsu_movie)

    discovery_col = db.get_collection("discovery_catalogs_cache")
    expires_at = now + datetime.timedelta(hours=12)

    cat_map = {
        "anisync_trending": ("trending", jikan_airing, kitsu_airing),
        "anisync_highest_rated": ("highestRated", jikan_top, kitsu_rating),
        "anisync_most_popular": ("mostPopular", jikan_pop, kitsu_pop),
        "anisync_top_airing": ("topAiring", jikan_airing, kitsu_airing),
        "anisync_seasonal": ("seasonal", jikan_season_now, kitsu_season),
        "anisync_schedule": ("schedule", jikan_schedule, []),
        "anisync_spotlight": ("spotlightMovies", jikan_movies, kitsu_movie),
        "anisync_seasonal:Next Season": ("seasonalNext", [], []),
        "anisync_seasonal:Upcoming": ("seasonalUpcoming", [], []),
        "anisync_seasonal:Winter": ("seasonalWinter", [], []),
        "anisync_seasonal:Spring": ("seasonalSpring", [], []),
        "anisync_seasonal:Summer": ("seasonalSummer", [], []),
        "anisync_seasonal:Fall": ("seasonalFall", [], []),
        "anisync_spotlight:New Movies": ("spotlightNewMovies", [], []),
        "anisync_spotlight:Classic Masterpieces": ("spotlightClassics", jikan_fav, []),
        "anisync_spotlight:OVAs & Specials": ("spotlightOva", [], []),
    }

    # Collect all unique MAL and AniList IDs across all fetched discovery lists to resolve to Kitsu IDs
    all_mal_ids = set()
    all_al_ids = set()
    for cat_key, (page_name, jikan_list, _) in cat_map.items():
        media_list = (data.get(page_name) or {}).get("media") or []
        for m in media_list:
            if m.get("id"):
                all_al_ids.add(str(m["id"]))
            if m.get("idMal"):
                all_mal_ids.add(str(m["idMal"]))
        for j_item in jikan_list:
            j_id = j_item.get("mal_id")
            if j_id:
                all_mal_ids.add(str(j_id))

    kitsu_mappings = await bulk_resolve_to_kitsu(mal_ids=list(all_mal_ids), anilist_ids=list(all_al_ids), skip_external=True)

    result_metas = {}

    for cat_key, (page_name, jikan_list, kitsu_list) in cat_map.items():
        media_list = (data.get(page_name) or {}).get("media") or []
        cat_metas = []
        seen_stremio_ids = set()

        for m in media_list:
            al_id = str(m["id"])
            mal_id = str(m.get("idMal") or "")
            kitsu_id = kitsu_mappings.get(f"mal:{mal_id}") or kitsu_mappings.get(f"anilist:{al_id}")
            stremio_id = f"kitsu:{kitsu_id}" if kitsu_id else (f"mal:{mal_id}" if mal_id else f"anilist:{al_id}")

            if stremio_id in seen_stremio_ids:
                continue
            seen_stremio_ids.add(stremio_id)

            t_obj = m.get("title") or {}
            canonical = t_obj.get("userPreferred") or t_obj.get("english") or t_obj.get("romaji") or "Unknown"

            # Parse next airing details
            next_ep_data = m.get("nextAiringEpisode")
            airing_day = None
            is_today = False
            if next_ep_data and next_ep_data.get("airingAt"):
                airing_ts = next_ep_data["airingAt"]
                airing_dt = datetime.datetime.fromtimestamp(airing_ts, datetime.timezone.utc)
                airing_day = airing_dt.strftime("%A")
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                is_today = (airing_dt.date() == now_utc.date())

            raw_desc = m.get("description") or ""
            clean_desc = re.sub(r"<[^>]+>", "", raw_desc)
            clean_desc = clean_desc[:200] + "..." if len(clean_desc) > 200 else clean_desc

            m_format = (m.get("format") or "tv").lower()
            stremio_type = "movie" if m_format == "movie" else "series"

            # Detect season string for seasonal catalog
            season_str = None
            if "season" in cat_key.lower() or "seasonal" in cat_key.lower():
                if "winter" in cat_key.lower():
                    season_str = "WINTER"
                elif "spring" in cat_key.lower():
                    season_str = "SPRING"
                elif "summer" in cat_key.lower():
                    season_str = "SUMMER"
                elif "fall" in cat_key.lower():
                    season_str = "FALL"
                else:
                    season_str = cur_season

            cat_metas.append(
                {
                    "id": stremio_id,
                    "type": stremio_type,
                    "name": canonical,
                    "title_obj": t_obj,
                    "poster": (m.get("coverImage") or {}).get("large") or "",
                    "background": m.get("bannerImage"),
                    "kitsu_id": kitsu_id,
                    "mal_id": mal_id,
                    "anilist_id": al_id,
                    "description": clean_desc,
                    "genres": m.get("genres") or [],
                    "score": round((m.get("averageScore") or 0) / 10, 1),
                    "episodes": m.get("episodes") or 0,
                    "year": (m.get("startDate") or {}).get("year") or m.get("seasonYear") or 0,
                    "airing_at": next_ep_data.get("airingAt") if next_ep_data else None,
                    "airing_day": airing_day,
                    "is_today": is_today,
                    "season": season_str,
                }
            )

        # Merge Jikan entries
        for j_item in jikan_list:
            mal_id = str(j_item.get("mal_id") or "")
            if not mal_id:
                continue
            kitsu_id = kitsu_mappings.get(f"mal:{mal_id}")
            stremio_id = f"kitsu:{kitsu_id}" if kitsu_id else f"mal:{mal_id}"

            if stremio_id in seen_stremio_ids:
                continue
            seen_stremio_ids.add(stremio_id)

            titles = j_item.get("titles") or []
            title_dict = {}
            for t in titles:
                t_type = t.get("type", "").lower()
                if t_type == "english":
                    title_dict["english"] = t.get("title")
                elif t_type == "default":
                    title_dict["userPreferred"] = t.get("title")
                elif t_type == "japanese":
                    title_dict["native"] = t.get("title")
            canonical = title_dict.get("english") or title_dict.get("userPreferred") or j_item.get("title") or "Unknown"

            images = j_item.get("images") or {}
            jpg_imgs = images.get("jpg") or images.get("webp") or {}
            poster = jpg_imgs.get("large_image_url") or jpg_imgs.get("image_url") or ""

            j_type = (j_item.get("type") or "tv").lower()
            stremio_type = "movie" if j_type == "movie" else "series"

            raw_desc = j_item.get("synopsis") or ""
            clean_desc = re.sub(r"<[^>]+>", "", raw_desc)
            clean_desc = clean_desc[:200] + "..." if len(clean_desc) > 200 else clean_desc

            cat_metas.append(
                {
                    "id": stremio_id,
                    "type": stremio_type,
                    "name": canonical,
                    "title_obj": title_dict,
                    "poster": poster,
                    "background": None,
                    "kitsu_id": kitsu_id,
                    "mal_id": mal_id,
                    "anilist_id": None,
                    "description": clean_desc,
                    "genres": [g.get("name") for g in (j_item.get("genres") or []) if g.get("name")],
                    "score": round(float(j_item.get("score") or 0), 1),
                    "episodes": j_item.get("episodes") or 0,
                    "year": int(j_item.get("year") or 0),
                    "airing_at": None,
                    "airing_day": None,
                    "is_today": False,
                    "season": cur_season if "seasonal" in cat_key.lower() else None,
                }
            )

        # Merge Kitsu entries
        for k_item in kitsu_list:
            k_id = str(k_item.get("id") or "")
            if not k_id:
                continue
            stremio_id = f"kitsu:{k_id}"
            if stremio_id in seen_stremio_ids:
                continue
            seen_stremio_ids.add(stremio_id)

            attrs = k_item.get("attributes") or {}
            titles = attrs.get("titles") or {}
            canonical = titles.get("en") or titles.get("en_jp") or attrs.get("canonicalTitle") or "Unknown"

            poster_obj = attrs.get("posterImage") or {}
            poster = poster_obj.get("large") or poster_obj.get("medium") or poster_obj.get("original") or ""

            cover_obj = attrs.get("coverImage") or {}
            cover = cover_obj.get("large") or cover_obj.get("original") or cover_obj.get("small") or None

            subtype = (attrs.get("subtype") or "tv").lower()
            stremio_type = "movie" if subtype == "movie" else "series"

            raw_desc = attrs.get("synopsis") or ""
            clean_desc = raw_desc[:200] + "..." if len(raw_desc) > 200 else raw_desc

            cat_metas.append(
                {
                    "id": stremio_id,
                    "type": stremio_type,
                    "name": canonical,
                    "title_obj": {"canonicalTitle": canonical, "titles": titles},
                    "poster": poster,
                    "background": cover,
                    "kitsu_id": k_id,
                    "mal_id": None,
                    "anilist_id": None,
                    "description": clean_desc,
                    "genres": [],
                    "score": round(float(attrs.get("averageRating") or 0) / 10, 1),
                    "episodes": attrs.get("episodeCount") or 0,
                    "year": int((attrs.get("startDate") or "0000")[:4]) if attrs.get("startDate") else 0,
                    "airing_at": None,
                    "airing_day": None,
                    "is_today": False,
                    "season": cur_season if "seasonal" in cat_key.lower() else None,
                }
            )

        result_metas[cat_key] = cat_metas

    # Fallback protection: If seasonal subgenres returned empty from AniList, reuse current season list
    base_seasonal = result_metas.get("anisync_seasonal", [])
    if not result_metas.get("anisync_seasonal:Next Season"):
        result_metas["anisync_seasonal:Next Season"] = list(base_seasonal)
    if not result_metas.get("anisync_seasonal:Upcoming"):
        result_metas["anisync_seasonal:Upcoming"] = list(base_seasonal)

    base_spotlight = result_metas.get("anisync_spotlight", [])
    for sp_key in [
        "anisync_spotlight:New Movies",
        "anisync_spotlight:Classic Masterpieces",
        "anisync_spotlight:OVAs & Specials",
    ]:
        if not result_metas.get(sp_key):
            result_metas[sp_key] = list(base_spotlight)

    # Save all discovery catalogs and sub-genre lists to database cache
    for cat_key, cat_metas in result_metas.items():
        try:
            if not cat_metas:
                # If newly fetched metas is empty (e.g. upstream AniList 403), preserve existing healthy cached documents!
                # Only bump expires_at so we don't spam upstream APIs while continuing to serve cached data.
                res = discovery_col.update_one(
                    {"catalog_id": cat_key, "metas.0": {"$exists": True}},
                    {"$set": {"expires_at": expires_at}},
                )
                if res.matched_count > 0:
                    logging.info("Preserved existing healthy discovery cache for %s while extending expiry", cat_key)
                    continue

            discovery_col.update_one(
                {"catalog_id": cat_key},
                {
                    "$set": {
                        "catalog_id": cat_key,
                        "metas": cat_metas,
                        "expires_at": expires_at,
                    }
                },
                upsert=True,
            )
        except Exception as ex:
            logging.error("Failed to write to discovery_catalogs_cache for %s: %s", cat_key, ex)

    # Pre-warm AniZip clearlogo and fanart cache for top discovery items
    try:
        from app.lib.meta_providers import bg_warm_anizip
        unique_discovery = {}
        for m in (
            result_metas.get("anisync_trending", [])
            + result_metas.get("anisync_top_airing", [])
            + result_metas.get("anisync_most_popular", [])
            + result_metas.get("anisync_spotlight", [])
        ):
            aid = m.get("anilist_id")
            mid = m.get("mal_id")
            if (aid or mid) and m.get("id") and m["id"] not in unique_discovery:
                unique_discovery[m["id"]] = {"anilist_id": aid, "mal_id": mid}
        if unique_discovery:
            asyncio.create_task(bg_warm_anizip(list(unique_discovery.values())[:60]))
    except Exception as e:
        logging.warning("Failed to dispatch AniZip prewarm for discovery catalogs: %s", e)

    return result_metas


async def discovery_catalogs_loop():
    """Background loop to periodically pre-fetch and update discovery catalogs cache."""
    # Wait a short bit after startup to avoid overloading AniList API during other startup tasks
    await asyncio.sleep(5)
    while True:
        try:
            logging.info("Pre-fetching discovery catalogs cache...")
            await update_discovery_catalogs_cache()
            logging.info("Discovery catalogs cache successfully updated.")
        except Exception as e:
            logging.error("Error in discovery catalogs pre-fetch loop: %s", e)
        # Sleep for 12 hours (matching 12h cache TTL) minus a 5-minute buffer
        await asyncio.sleep(12 * 3600 - 300)


def trigger_discovery_catalogs_prefetch():
    """Start the background discovery catalogs prefetch loop."""
    asyncio.create_task(discovery_catalogs_loop())


async def handle_discovery_catalog(user, user_id, catalog_type, catalog_id, filters, extras=""):
    if not user.get("enable_discovery_catalogs", True):
        return await respond_with({"metas": []})

    from app.services.db import db
    discovery_col = db.get_collection("discovery_catalogs_cache")
    now = datetime.datetime.utcnow()

    genre = filters.get("genre")
    cache_key = f"{catalog_id}:{genre}" if genre else catalog_id

    cached = None
    base_cached = None
    try:
        base_cached = discovery_col.find_one({"catalog_id": catalog_id})
        if genre:
            cached = discovery_col.find_one({"catalog_id": cache_key})
            if (not cached or not cached.get("metas")) and base_cached:
                base_metas = base_cached.get("metas", [])
                if catalog_id == "anisync_schedule":
                    if genre == "Airing Today":
                        sub_metas = [m for m in base_metas if m.get("is_today")]
                    else:
                        sub_metas = [m for m in base_metas if m.get("airing_day") == genre]
                    cached = {"metas": sub_metas, "expires_at": base_cached.get("expires_at", now)}
                elif catalog_id == "anisync_seasonal":
                    if genre in ["Winter", "Spring", "Summer", "Fall"]:
                        sub_metas = [m for m in base_metas if m.get("season") == genre.upper()]
                        cached = {"metas": sub_metas or base_metas, "expires_at": base_cached.get("expires_at", now)}
                    else:
                        cached = base_cached
                else:
                    cached = base_cached
        else:
            cached = base_cached
    except Exception as e:
        logging.error("Failed to query discovery_catalogs_cache: %s", e)

    metas = []
    if cached and cached.get("metas") and cached.get("expires_at", now) > now:
        metas = cached["metas"]
    elif base_cached and base_cached.get("metas"):
        # Serve parent base catalog immediately — zero latency stall for invalid/missing genres!
        metas = base_cached["metas"]
        if base_cached.get("expires_at", now) <= now:
            trigger_discovery_catalogs_prefetch()
    else:
        try:
            all_metas = await update_discovery_catalogs_cache()
            metas = all_metas.get(cache_key) or all_metas.get(catalog_id, [])
        except Exception as e:
            logging.error("Failed to update discovery catalogs from AniList: %s", e)
            metas = []

    # Filter to only dubbed anime if user has enabled dubbed for this catalog or globally
    metas = await apply_catalog_dub_filter(metas, user, catalog_id)

    # Apply Custom Sorting for Discovery Catalogs if enabled
    is_custom_sort, sort_by, sort_order = get_catalog_sorting(user, catalog_id, "watching", url_filters=filters)
    if is_custom_sort:
        metas = sort_watchlist_items(metas, sort_by, sort_order, tracker_type="stremio")

    # Shuffle if enabled and not explicitly custom sorted
    if is_catalog_shuffle_enabled(user, catalog_id) and (not is_custom_sort or sort_by == "default"):
        metas = list(metas)
        random.shuffle(metas)

    # Handle pagination skip
    try:
        offset = max(0, int(filters.get("skip", 0)))
    except (ValueError, TypeError):
        offset = 0
    try:
        limit_val = request.args.get("limit") or filters.get("limit")
        page_limit = max(1, min(100, int(limit_val))) if limit_val else 40
    except (ValueError, TypeError):
        page_limit = 40

    metas = metas[offset : offset + page_limit]
    return await respond_with(
        {"metas": format_catalog_metas(metas, user, catalog_type, catalog_id)},
        max_age=3600,
        stale_while_revalidate=7200,
    )

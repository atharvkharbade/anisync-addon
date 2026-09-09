import asyncio
import datetime
import logging
import re

from app.api import anilist as anilist_api
from app.lib.id_resolver import bulk_resolve_to_kitsu
from app.services.db import db

from .constants import build_anilist_discovery_query
from .fetchers import fetch_supplemental_discovery_data


async def update_discovery_catalogs_cache() -> dict:
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
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

    query = build_anilist_discovery_query(cur_season, next_season, year, next_year)

    data = {}
    try:
        res = await anilist_api._gql(None, query)
        data = res.get("data") or {}
    except Exception as e:
        logging.warning("AniList GraphQL discovery fetch failed (%s), relying on supplemental data...", e)

    # Fetch Jikan and Kitsu discovery lists in parallel
    supp = await fetch_supplemental_discovery_data()
    jikan_pop = supp["jikan_pop"]
    jikan_airing = supp["jikan_airing"]
    jikan_top = supp["jikan_top"]
    jikan_movies = supp["jikan_movies"]
    jikan_season_now = supp["jikan_season_now"]
    jikan_schedule = supp["jikan_schedule"]
    jikan_fav = supp["jikan_fav"]
    kitsu_pop = supp["kitsu_pop"]
    kitsu_rating = supp["kitsu_rating"]
    kitsu_airing = supp["kitsu_airing"]
    kitsu_season = supp["kitsu_season"]
    kitsu_movie = supp["kitsu_movie"]

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

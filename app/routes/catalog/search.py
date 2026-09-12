import datetime
import logging
import re

import random

from app.routes.catalog.formatting import format_catalog_metas, get_anilist_title, get_kitsu_title
from app.routes.catalog.sorting.dubs import apply_catalog_dub_filter
from app.routes.catalog.sorting.preferences import get_catalog_sorting, is_catalog_shuffle_enabled
from app.routes.catalog.sorting.sorter import sort_watchlist_items
from app.routes.utils import respond_with
from app.services.http import get_client


async def _process_search_metas(raw_metas, user, catalog_id, filters):
    if not raw_metas:
        return []
    metas = list(raw_metas)
    metas = await apply_catalog_dub_filter(metas, user, catalog_id)
    is_custom_sort, sort_by, sort_order = get_catalog_sorting(user, catalog_id, "watching", url_filters=filters)
    if is_custom_sort and sort_by != "default":
        title_lang = user.get("title_language", "english") if isinstance(user, dict) else "english"
        metas = sort_watchlist_items(metas, sort_by, sort_order, tracker_type="stremio", title_lang=title_lang)
    if is_catalog_shuffle_enabled(user, catalog_id) and (not is_custom_sort or sort_by == "default"):
        metas = list(metas)
        random.shuffle(metas)
    return metas


async def handle_search_catalog(user, user_id, catalog_type, catalog_id, filters, extras=""):
    search_query = filters.get("search", "")
    if not search_query:
        return await respond_with({"metas": []})

    try:
        offset = max(0, int(filters.get("skip", 0)))
    except (ValueError, TypeError):
        offset = 0

    norm_search_query = search_query.strip().lower()

    # Check search cache first (expires after 24 hours)
    from app.services.db import db

    now = datetime.datetime.utcnow()
    cache_col = db.get_collection("kitsu_search_cache")
    try:
        cached = cache_col.find_one({"query": norm_search_query, "offset": offset})
        if cached and cached.get("expires_at") > now:
            c_metas = cached.get("metas") or []
            if c_metas and all(m.get("date_val") is not None and m.get("score") is not None and m.get("year") is not None for m in c_metas):
                processed_metas = await _process_search_metas(c_metas, user, catalog_id, filters)
                return await respond_with(
                    {"metas": format_catalog_metas(processed_metas, user, catalog_type, catalog_id)},
                    max_age=60,
                    stale_while_revalidate=120,
                )
            else:
                try:
                    cache_col.delete_one({"_id": cached["_id"]})
                except Exception:
                    pass
                cached = None
    except Exception as e:
        logging.error("Failed to query kitsu_search_cache: %s", e)
        cached = None

    metas = []

    # 1. Primary Pass: Query Kitsu API directly for fast search results
    try:
        url = "https://kitsu.io/api/edge/anime"
        params = {"filter[text]": search_query, "page[limit]": 20, "page[offset]": offset}
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
                if subtype == "music":
                    continue
                item_type = "movie" if subtype == "movie" else "series"

                titles = attrs.get("titles", {})
                title_lang = user.get("title_language", "english")
                title = get_kitsu_title(attrs, title_lang)
                title_obj = {
                    "canonicalTitle": attrs.get("canonicalTitle"),
                    "titles": titles
                }
                poster_img = attrs.get("posterImage") or {}
                poster = (
                    poster_img.get("large")
                    or poster_img.get("medium")
                    or poster_img.get("original")
                    or ""
                )
                if poster:
                    poster = poster.split("?")[0]
                cover_img = attrs.get("coverImage") or {}
                cover = (
                    cover_img.get("large")
                    or cover_img.get("original")
                    or cover_img.get("small")
                    or None
                )
                synopsis = attrs.get("synopsis") or ""
                avg_rating = attrs.get("averageRating")
                score_val = 0.0
                if avg_rating:
                    try:
                        score_val = round(float(avg_rating) / 10.0, 1)
                    except (ValueError, TypeError):
                        score_val = 0.0

                start_date = str(attrs.get("startDate") or "").strip()
                year_val = 0
                date_val = 0
                if start_date:
                    parts = start_date.split("-")
                    if len(parts) >= 1 and parts[0].isdigit():
                        year_val = int(parts[0])
                        m_num = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
                        d_num = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
                        date_val = year_val * 10000 + m_num * 100 + d_num

                ep_count = int(attrs.get("episodeCount") or 0)

                metas.append(
                    {
                        "id": f"kitsu:{item['id']}",
                        "type": item_type,
                        "name": title,
                        "title_obj": title_obj,
                        "poster": poster,
                        "background": cover,
                        "kitsu_id": str(item["id"]),
                        "description": synopsis[:200] + "..." if len(synopsis) > 200 else synopsis,
                        "ageRating": attrs.get("ageRating"),
                        "nsfw": attrs.get("nsfw"),
                        "score": score_val,
                        "year": year_val,
                        "date_val": date_val,
                        "release_date": start_date,
                        "releaseInfo": str(year_val) if year_val else None,
                        "episodes": ep_count,
                    }
                )
    except Exception as e:
        logging.error("Kitsu search query failed: %s", e)

    # 2. Smart Fallback / Enrichment: If Kitsu returned few results (< 5) and search query is specific (>= 3 chars)
    if len(metas) < 5 and len(search_query.strip()) >= 3:
        try:
            page_num = (offset // 20) + 1
            anilist_query = """
            query ($search: String, $page: Int, $perPage: Int) {
              Page(page: $page, perPage: $perPage) {
                media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
                  id
                  idMal
                  format
                  status
                  description
                  isAdult
                  averageScore
                  seasonYear
                  startDate {
                    year
                  }
                  episodes
                  title {
                    romaji
                    english
                    native
                    userPreferred
                  }
                  coverImage {
                    extraLarge
                    large
                    medium
                  }
                  bannerImage
                }
              }
            }
            """
            client = get_client()

            # Generate search candidates: exact query + stripped base query if suffix keywords present
            search_candidates = [search_query.strip()]
            SUFFIX_WORDS = {
                "ova", "ovas", "oad", "oads", "special", "specials", "movie", "movies", "film", "films",
                "moment", "moments", "episode", "episodes", "side", "story"
            }
            words = search_query.strip().split()
            if len(words) > 1:
                cleaned = [w for w in words if w.lower() not in SUFFIX_WORDS]
                if cleaned and len(cleaned) < len(words):
                    search_candidates.append(" ".join(cleaned))

            al_data = []
            for candidate_query in search_candidates:
                al_resp = await client.post(
                    "https://graphql.anilist.co",
                    json={"query": anilist_query, "variables": {"search": candidate_query, "page": page_num, "perPage": 20}},
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Referer": "https://anilist.co/",
                    },
                    timeout=5,
                )
                if al_resp.status_code == 200:
                    al_data = al_resp.json().get("data", {}).get("Page", {}).get("media", [])
                    if al_data:
                        break

            if al_data:
                # If the query contained a format-specific keyword (e.g. OVA, Movie, Special), boost matching formats to the top
                FORMAT_BOOST_MAP = {
                    "ova": ["OVA"],
                    "ovas": ["OVA"],
                    "oad": ["OVA"],
                    "oads": ["OVA"],
                    "special": ["SPECIAL", "TV_SHORT"],
                    "specials": ["SPECIAL", "TV_SHORT"],
                    "movie": ["MOVIE"],
                    "movies": ["MOVIE"],
                    "film": ["MOVIE"],
                    "films": ["MOVIE"],
                }
                detected_formats = []
                for w in words:
                    if w.lower() in FORMAT_BOOST_MAP:
                        detected_formats.extend(FORMAT_BOOST_MAP[w.lower()])

                if detected_formats and len(al_data) > 1:
                    al_data.sort(key=lambda m: (m.get("format") not in detected_formats))

                # Batch resolve all MAL and AniList IDs to Kitsu IDs in one MongoDB query
                mal_ids_list = [str(m["idMal"]) for m in al_data if m.get("idMal")]
                al_ids_list = [str(m["id"]) for m in al_data if m.get("id")]

                fribb_col = db.get_collection("fribb_mappings")
                id_cache_col = db.get_collection("id_cache")

                mal_query_vals = mal_ids_list + [int(mid) for mid in mal_ids_list if mid.isdigit()]
                al_query_vals = al_ids_list + [int(aid) for aid in al_ids_list if aid.isdigit()]

                or_clauses = []
                if mal_query_vals:
                    or_clauses.append({"mal_id": {"$in": mal_query_vals}})
                if al_query_vals:
                    or_clauses.append({"anilist_id": {"$in": al_query_vals}})

                kitsu_map = {}
                if or_clauses:
                    try:
                        fribb_docs = list(fribb_col.find({"$or": or_clauses}))
                        for doc in fribb_docs:
                            k_id = doc.get("kitsu_id")
                            if k_id:
                                if doc.get("mal_id"):
                                    kitsu_map[f"mal:{doc['mal_id']}"] = str(k_id)
                                if doc.get("anilist_id"):
                                    kitsu_map[f"al:{doc['anilist_id']}"] = str(k_id)
                    except Exception as ex:
                        logging.error("Failed batch fribb lookup for search fallback: %s", ex)

                    # Fallback to id_cache for any unresolved
                    try:
                        id_cache_docs = list(id_cache_col.find({"$or": or_clauses}))
                        for doc in id_cache_docs:
                            k_id = doc.get("kitsu_id")
                            if k_id:
                                if doc.get("mal_id"):
                                    kitsu_map.setdefault(f"mal:{doc['mal_id']}", str(k_id))
                                if doc.get("anilist_id"):
                                    kitsu_map.setdefault(f"al:{doc['anilist_id']}", str(k_id))
                    except Exception as ex:
                        logging.error("Failed batch id_cache lookup for search fallback: %s", ex)

                existing_ids = {m["id"] for m in metas}
                fallback_metas = []
                for m in al_data:
                    if (m.get("format") or "").upper() == "MUSIC":
                        continue
                    al_id = str(m.get("id"))
                    mal_id = str(m["idMal"]) if m.get("idMal") else None

                    # Resolve Kitsu ID
                    kitsu_id = (
                        kitsu_map.get(f"mal:{mal_id}")
                        or (kitsu_map.get(f"mal:{int(mal_id)}") if mal_id and mal_id.isdigit() else None)
                        or kitsu_map.get(f"al:{al_id}")
                        or (kitsu_map.get(f"al:{int(al_id)}") if al_id and al_id.isdigit() else None)
                    )
                    stremio_id = f"kitsu:{kitsu_id}" if kitsu_id else (f"mal:{mal_id}" if mal_id else f"anilist:{al_id}")
                    if stremio_id in existing_ids:
                        continue

                    al_title = m.get("title", {}) or {}
                    title_lang = user.get("title_language", "english")
                    title_name = get_anilist_title(al_title, title_lang)
                    title_obj = {
                        "canonicalTitle": al_title.get("userPreferred") or al_title.get("romaji") or al_title.get("english"),
                        "titles": {
                            "en": al_title.get("english"),
                            "en_jp": al_title.get("romaji"),
                            "ja_jp": al_title.get("native"),
                        }
                    }

                    fmt = (m.get("format") or "TV").upper()
                    item_type = "movie" if fmt in ["MOVIE"] else "series"

                    cover = m.get("coverImage", {}) or {}
                    poster = cover.get("large") or cover.get("extraLarge") or cover.get("medium") or ""

                    raw_desc = m.get("description") or ""
                    clean_desc = re.sub(r"<[^>]+>", "", raw_desc)
                    clean_desc = clean_desc[:200] + "..." if len(clean_desc) > 200 else clean_desc

                    fallback_metas.append(
                        {
                            "id": stremio_id,
                            "type": item_type,
                            "name": title_name,
                            "title_obj": title_obj,
                            "poster": poster,
                            "background": m.get("bannerImage"),
                            "anilist_id": al_id,
                            "mal_id": mal_id,
                            "kitsu_id": kitsu_id,
                            "description": clean_desc,
                            "ageRating": "R18" if m.get("isAdult") else None,
                            "nsfw": bool(m.get("isAdult")),
                            "score": round((m.get("averageScore") or 0) / 10.0, 1),
                            "year": (m.get("seasonYear") or (m.get("startDate") or {}).get("year") or 0),
                            "date_val": ((m.get("startDate") or {}).get("year", 0) * 10000 + (m.get("startDate") or {}).get("month", 0) * 100 + (m.get("startDate") or {}).get("day", 0)) if (m.get("startDate") or {}).get("year") else ((m.get("seasonYear") or 0) * 10000),
                            "release_date": f"{(m.get('startDate') or {}).get('year', 0):04d}-{(m.get('startDate') or {}).get('month', 0):02d}-{(m.get('startDate') or {}).get('day', 0):02d}" if (m.get("startDate") or {}).get("year") else (str(m.get("seasonYear")) if m.get("seasonYear") else ""),
                            "releaseInfo": str(m.get("seasonYear") or (m.get("startDate") or {}).get("year")) if (m.get("seasonYear") or (m.get("startDate") or {}).get("year")) else None,
                            "episodes": m.get("episodes") or 0,
                        }
                    )
                    existing_ids.add(stremio_id)

                # Prepend exact search matches from AniList if Kitsu missed them
                metas = fallback_metas + metas
        except Exception as e:
            logging.error("AniList search fallback failed: %s", e)

    # 3. Write non-empty results to search cache
    if metas:
        try:
            cache_col.update_one(
                {"query": norm_search_query, "offset": offset},
                {
                    "$set": {
                        "query": norm_search_query,
                        "offset": offset,
                        "metas": metas,
                        "expires_at": now + datetime.timedelta(hours=24),
                    }
                },
                upsert=True,
            )
        except Exception as e:
            logging.error("Failed to write kitsu_search_cache: %s", e)
    elif cached:
        logging.warning("Search returned 0 results, returning expired cache for query '%s'", search_query)
        processed_metas = await _process_search_metas(cached["metas"], user, catalog_id, filters)
        return await respond_with(
            {"metas": format_catalog_metas(processed_metas, user, catalog_type, catalog_id)},
            max_age=60,
            stale_while_revalidate=120,
        )

    processed_metas = await _process_search_metas(metas, user, catalog_id, filters)
    return await respond_with(
        {"metas": format_catalog_metas(processed_metas, user, catalog_type, catalog_id)},
        max_age=60,
        stale_while_revalidate=120,
    )

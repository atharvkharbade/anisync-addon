import asyncio
import datetime
import logging
import random

from app.services.db import get_cached_ids_by_mal_bulk, get_user
from app.services.recommendations.ai_enhancer import enhance_recommendations_with_gemini
from app.services.recommendations.cache import get_popular_fallbacks, recommendations_cache_collection
from app.services.recommendations.fetchers import (
    get_anilist_recommendations_bulk,
    get_mal_recommendations_for_id,
)
from app.services.recommendations.genres import generate_genre_recommendations
from app.services.recommendations.history import fetch_user_watchlist_history
from app.services.recommendations.seeding import get_recommendations_for_seeds, select_weighted_seeds
from app.services.recommendations.utils import clean_html, is_proper_anime

logger = logging.getLogger(__name__)


async def _update_recommendations_cache_impl(user_id: str, force: bool = False):
    user = get_user(user_id)
    if not user or not user.get("enable_recommendations", True):
        return
    fallbacks = get_popular_fallbacks()

    # Check if cache is fresh enough
    existing = recommendations_cache_collection.find_one({"uid": user_id})
    if existing and not force:
        last_updated = existing.get("last_updated")
        if last_updated:
            # Handle naive or aware datetimes
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=datetime.timezone.utc)
            if (datetime.datetime.now(datetime.timezone.utc) - last_updated) < datetime.timedelta(hours=24):
                return

    rec_language = user.get("rec_language", "en")
    rec_popularity = user.get("rec_popularity", "balanced")
    rec_sorting_order = user.get("rec_sorting_order", "default")
    rec_year_min = user.get("rec_year_min", 1980)
    rec_year_max = user.get("rec_year_max", datetime.datetime.now(datetime.timezone.utc).year + 1)
    rec_excluded_movie_genres = user.get("rec_excluded_movie_genres", [])
    rec_excluded_series_genres = user.get("rec_excluded_series_genres", [])

    logger.info(
        "Recalculating recommendations for user %s (Lang: %s, Pop: %s, Years: %s-%s, Excl Movies: %s, Excl Series: %s)...",
        user_id,
        rec_language,
        rec_popularity,
        rec_year_min,
        rec_year_max,
        rec_excluded_movie_genres,
        rec_excluded_series_genres,
    )

    # 1. Fetch watched history from track managers & resolve cross-tracker IDs
    (
        merged_shows,
        watched_mal_ids,
        watched_anilist_ids,
        watched_kitsu_ids,
        watched_titles,
    ) = await fetch_user_watchlist_history(user, user_id)

    title_lang = (user.get("title_language", "english") or "english").lower() if user else "english"
    filter_watched = user.get("recommendations_filter_watched", True)

    # Sort history to select seed shows
    sorted_user_history = sorted(
        merged_shows.values(),
        key=lambda x: (1 if x["status"] in ["completed", "watching"] else 0, x["rating"] or 0),
        reverse=True,
    )

    # 2. Generate "Top Picks" (Community Recs)
    top_picks = []
    rec_candidates = {}

    seed_pool = [s for s in merged_shows.values() if s["status"] in ["completed", "watching", "on_hold"]]
    if not seed_pool:
        seed_pool = [s for s in merged_shows.values() if s["status"] == "planning"]
    if len(seed_pool) > 50:
        seed_pool = random.sample(seed_pool, 50)

    sorted_seed_pool = sorted(
        seed_pool, key=lambda x: (1 if x["status"] in ["completed", "watching"] else 0, x["rating"] or 0), reverse=True
    )
    recent_seeds = sorted_seed_pool[:5]
    remaining_pool = [s for s in seed_pool if s not in recent_seeds]
    random_seeds = select_weighted_seeds(remaining_pool, 10)
    top_picks_seeds = recent_seeds + random_seeds

    # AniList Bulk query for top picks seeds
    al_ids = [int(s["anilist_id"]) for s in top_picks_seeds if s["anilist_id"]]
    al_recs = []
    anilist_id_to_title = {str(s["anilist_id"]): s["title"] for s in top_picks_seeds if s.get("anilist_id")}
    if al_ids and user.get("anilist_token") and user.get("anilist_enabled", True):
        al_recs = await get_anilist_recommendations_bulk(user["anilist_token"], al_ids)

    for rec in al_recs:
        media = rec.get("mediaRecommendation")
        if not media:
            continue

        if media.get("status") == "NOT_YET_RELEASED":
            continue

        # Exclude OVA, SPECIAL, MUSIC, TV_SHORT and short durations (<= 5 minutes)
        m_format = media.get("format")
        duration = media.get("duration")
        if m_format in ["OVA", "SPECIAL", "MUSIC", "TV_SHORT"]:
            continue
        if duration is not None and duration <= 5:
            continue

        seed_media = rec.get("media") or {}
        seed_aid = str(seed_media.get("id")) if seed_media.get("id") else None
        seed_title = anilist_id_to_title.get(seed_aid) if seed_aid else None

        aid = str(media.get("id"))
        mid = str(media.get("idMal")) if media.get("idMal") else None

        if filter_watched:
            if aid in watched_anilist_ids or (mid and mid in watched_mal_ids):
                continue

        year = (media.get("startDate") or {}).get("year")
        if year and (year < rec_year_min or year > rec_year_max):
            continue

        item_type = "movie" if m_format == "MOVIE" else "series"

        genres = media.get("genres", []) or []
        excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
        if any(g in excluded_genres for g in genres):
            continue

        pop_score = media.get("popularity") or 0
        avg_score = media.get("averageScore") or 0
        if rec_popularity == "mainstream":
            if pop_score < 25000:
                continue
        elif rec_popularity == "gems":
            if pop_score >= 25000 or avg_score < 73:
                continue

        title_pref = media.get("title", {})
        if rec_language == "ja":
            title = title_pref.get("romaji") or title_pref.get("userPreferred") or title_pref.get("english")
        else:
            title = title_pref.get("english") or title_pref.get("userPreferred") or title_pref.get("romaji")

        if filter_watched and title.lower() in watched_titles:
            continue

        if not is_proper_anime(title):
            continue

        poster = (media.get("coverImage") or {}).get("large") or (media.get("coverImage") or {}).get("medium") or ""
        syn = clean_html(media.get("description") or "")

        key = f"mal:{mid}" if mid else f"anilist:{aid}"
        if key not in rec_candidates:
            rec_candidates[key] = {
                "id": key,
                "type": item_type,
                "name": title,
                "poster": poster,
                "poster_al": poster,
                "anilist_id": aid,
                "mal_id": mid,
                "score": rec.get("rating", 1),
                "description": "AniList Community Recommendation.",
                "synopsis": syn,
                "inspired_by_titles": [seed_title] if seed_title else [],
            }
        else:
            rec_candidates[key]["score"] += rec.get("rating", 1)
            if syn and not rec_candidates[key].get("synopsis"):
                rec_candidates[key]["synopsis"] = syn
            if seed_title and seed_title not in rec_candidates[key]["inspired_by_titles"]:
                rec_candidates[key]["inspired_by_titles"].append(seed_title)

    # MAL recommendations query for top 5 MAL shows
    mal_seed_shows = [s for s in top_picks_seeds if s["mal_id"]][:5]
    if mal_seed_shows and user.get("mal_access_token") and user.get("mal_enabled", True):
        tasks = [get_mal_recommendations_for_id(user["mal_access_token"], s["mal_id"]) for s in mal_seed_shows]
        mal_recs_lists = await asyncio.gather(*tasks)

        genre_mal_ids = [
            str(r.get("node", {}).get("id"))
            for sublist in mal_recs_lists
            for r in sublist
            if r.get("node", {}).get("id")
        ]
        genre_id_cache_map = get_cached_ids_by_mal_bulk(genre_mal_ids)

        for s, rec_list in zip(mal_seed_shows, mal_recs_lists):
            seed_title = s["title"]
            for rec in rec_list:
                node = rec.get("node", {})
                mid = str(node.get("id"))
                alt_titles = node.get("alternative_titles") or {}
                if title_lang == "english":
                    title = alt_titles.get("en") or node.get("title") or "Unknown Title"
                elif title_lang == "japanese":
                    title = alt_titles.get("ja") or node.get("title") or "Unknown Title"
                else:
                    title = node.get("title") or alt_titles.get("en") or "Unknown Title"

                if node.get("status") == "not_yet_aired":
                    continue

                syn = clean_html(node.get("synopsis") or "")
                m_type = node.get("media_type")
                duration = node.get("average_episode_duration")
                if m_type in ["ova", "special", "music"] or not is_proper_anime(title, syn):
                    continue
                if duration is not None and duration <= 300:
                    continue

                if filter_watched:
                    if mid in watched_mal_ids:
                        continue
                    if title.lower() in watched_titles:
                        continue

                year = (node.get("start_season") or {}).get("year")
                if year and (year < rec_year_min or year > rec_year_max):
                    continue

                item_type = "movie" if m_type == "movie" else "series"

                genres = [g.get("name") for g in node.get("genres", []) if g.get("name")]
                excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
                if any(g in excluded_genres for g in genres):
                    continue

                pop_rank = node.get("popularity")
                mean_score = node.get("mean")
                if rec_popularity == "mainstream":
                    if pop_rank and pop_rank > 1200:
                        continue
                elif rec_popularity == "gems":
                    if (pop_rank and pop_rank <= 1200) or (mean_score and mean_score < 7.3):
                        continue

                poster = (
                    (node.get("main_picture") or {}).get("large")
                    or (node.get("main_picture") or {}).get("medium")
                    or ""
                )
                syn = clean_html(node.get("synopsis") or "")

                c_doc = genre_id_cache_map.get(str(mid))
                aid = str(c_doc["anilist_id"]) if c_doc and c_doc.get("anilist_id") else None

                from app.lib.meta_providers import get_al_cover, get_effective_meta_providers

                al_poster = get_al_cover(aid)
                rec_poster_pref = get_effective_meta_providers(user).get("poster", "kitsu")
                chosen_poster = al_poster if (rec_poster_pref == "anilist" and al_poster) else poster

                key = f"mal:{mid}"
                if key not in rec_candidates:
                    rec_candidates[key] = {
                        "id": key,
                        "type": item_type,
                        "name": title,
                        "poster": chosen_poster,
                        "poster_mal": poster,
                        "poster_al": al_poster or poster,
                        "mal_id": str(mid),
                        "anilist_id": aid,
                        "score": rec.get("num_recommendations", 1),
                        "description": "MAL Community Recommendation.",
                        "synopsis": syn,
                        "inspired_by_titles": [seed_title],
                    }
                else:
                    rec_candidates[key]["score"] += rec.get("num_recommendations", 1)
                    if syn and not rec_candidates[key].get("synopsis"):
                        rec_candidates[key]["synopsis"] = syn
                    if seed_title not in rec_candidates[key]["inspired_by_titles"]:
                        rec_candidates[key]["inspired_by_titles"].append(seed_title)

    top_picks = sorted(rec_candidates.values(), key=lambda x: x["score"], reverse=True)
    for tp in top_picks:
        tp.pop("score", None)
        syn = tp.get("synopsis") or ""
        inspired_by = tp.get("inspired_by_titles", [])
        if inspired_by:
            desc = f"Inspired by your history: {', '.join(inspired_by)}."
        else:
            desc = tp.get("description") or "Community Recommendation."
        tp["description"] = f"{desc}  \n\n{syn}" if syn else desc

    # 3. Generate "Because you Watched"
    item_recs = []
    seed_show = None
    seed_candidates = [s for s in seed_pool if (s["rating"] or 0) >= 7 or s["status"] in ["completed", "watching"]]
    if not seed_candidates and seed_pool:
        seed_candidates = seed_pool
    if seed_candidates:
        seed_show = random.choice(seed_candidates)

    if seed_show:
        item_recs = await get_recommendations_for_seeds(
            [seed_show],
            user,
            watched_mal_ids,
            watched_anilist_ids,
            watched_titles,
            watched_kitsu_ids=watched_kitsu_ids,
        )
        for ir in item_recs:
            desc = f"Recommended because you watched {seed_show['title']}."
            syn = ir.get("synopsis") or ""
            ir["description"] = f"{desc}  \n\n{syn}" if syn else desc

    if not item_recs:
        item_recs = []
        for fb in fallbacks:
            if len(item_recs) >= 5:
                break

            if filter_watched:
                title = fb.get("name", "")
                if title and title.lower() in watched_titles:
                    continue
                fb_id = fb["id"]
                if ":" in fb_id:
                    tracker, ext_id = fb_id.split(":", 1)
                    if tracker == "mal" and ext_id in watched_mal_ids:
                        continue
                    if tracker == "anilist" and ext_id in watched_anilist_ids:
                        continue
                    if tracker == "kitsu" and ext_id in watched_kitsu_ids:
                        continue

            item_copy = fb.copy()
            desc = "Popular trending anime you might enjoy."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            item_recs.append(item_copy)
        seed_show = {"title": "Fullmetal Alchemist: Brotherhood"}

    if filter_watched and item_recs:
        filtered_item_recs = []
        for ir in item_recs:
            title = ir.get("name", "")
            if title and title.lower() in watched_titles:
                continue
            ir_id = ir.get("id")
            if ir_id and ":" in ir_id:
                tracker, ext_id = ir_id.split(":", 1)
                if tracker == "mal" and ext_id in watched_mal_ids:
                    continue
                if tracker == "anilist" and ext_id in watched_anilist_ids:
                    continue
                if tracker == "kitsu" and ext_id in watched_kitsu_ids:
                    continue
            filtered_item_recs.append(ir)
        item_recs = filtered_item_recs

    # 4. Generate "Inspired by your Favorites"
    loved_count = 8
    if len(seed_pool) < 16:
        loved_count = max(1, len(seed_pool) // 2)
    loved_seeds = select_weighted_seeds(seed_pool, loved_count)
    loved_items = await get_recommendations_for_seeds(
        loved_seeds, user, watched_mal_ids, watched_anilist_ids, watched_titles, watched_kitsu_ids=watched_kitsu_ids
    )
    for lr in loved_items:
        inspired_by = lr.get("inspired_by_titles", [])
        if inspired_by:
            desc = f"Inspired by your favorites: {', '.join(inspired_by)}."
        else:
            desc = "Inspired by your favorites."
        syn = lr.get("synopsis") or ""
        lr["description"] = f"{desc}  \n\n{syn}" if syn else desc
    if not loved_items:
        loved_items = []
        for fb in fallbacks[:5]:
            item_copy = fb.copy()
            desc = "Popular trending anime you might enjoy."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            loved_items.append(item_copy)

    # 5. Generate "More from your Watchlist"
    remaining_liked_pool = [s for s in seed_pool if s not in loved_seeds]
    liked_count = 8
    if len(seed_pool) < 16:
        liked_count = len(seed_pool) - len(loved_seeds)
    liked_seeds = select_weighted_seeds(remaining_liked_pool, liked_count)
    liked_items = await get_recommendations_for_seeds(
        liked_seeds, user, watched_mal_ids, watched_anilist_ids, watched_titles, watched_kitsu_ids=watched_kitsu_ids
    )
    for lr in liked_items:
        inspired_by = lr.get("inspired_by_titles", [])
        if inspired_by:
            desc = f"Inspired by your watchlist: {', '.join(inspired_by)}."
        else:
            desc = "More from your watchlist."
        syn = lr.get("synopsis") or ""
        lr["description"] = f"{desc}  \n\n{syn}" if syn else desc
    if not liked_items:
        liked_items = []
        for fb in fallbacks[3:8]:
            item_copy = fb.copy()
            desc = "Popular trending anime you might enjoy."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            liked_items.append(item_copy)

    # 6. Genre Collections
    genre_counts = {}
    for show in merged_shows.values():
        if show["status"] == "planning":
            continue
        for g in show.get("genres", []):
            genre_counts[g] = genre_counts.get(g, 0) + 1
    sorted_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)
    fav_genres = [g[0] for g in sorted_genres[:2]]
    while len(fav_genres) < 2:
        for fallback_genre in ["Action", "Adventure", "Comedy", "Fantasy", "Drama"]:
            if fallback_genre not in fav_genres:
                fav_genres.append(fallback_genre)
                if len(fav_genres) >= 2:
                    break

    genre_1_name = fav_genres[0]
    genre_2_name = fav_genres[1]

    genre_1_items = await generate_genre_recommendations(
        genre_1_name,
        user,
        watched_mal_ids,
        watched_anilist_ids,
        watched_titles,
        watched_kitsu_ids=watched_kitsu_ids,
    )
    genre_2_items = await generate_genre_recommendations(
        genre_2_name,
        user,
        watched_mal_ids,
        watched_anilist_ids,
        watched_titles,
        watched_kitsu_ids=watched_kitsu_ids,
    )
    if not genre_1_items:
        genre_1_items = []
        for fb in fallbacks[1:6]:
            item_copy = fb.copy()
            desc = "Popular genre collection."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            genre_1_items.append(item_copy)
    if not genre_2_items:
        genre_2_items = []
        for fb in fallbacks[2:7]:
            item_copy = fb.copy()
            desc = "Popular genre collection."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            genre_2_items.append(item_copy)

    # 7. Enhance recommendations using Gemini API if key is provided
    gemini_api_key = user.get("gemini_api_key", "").strip()
    if gemini_api_key:
        candidate_groups = {
            "top_picks": top_picks,
            "item_recs": item_recs,
            "loved_items": loved_items,
            "liked_items": liked_items,
            "genre_1_items": genre_1_items,
            "genre_2_items": genre_2_items,
        }
        enhanced_groups = await enhance_recommendations_with_gemini(
            gemini_api_key, sorted_user_history, candidate_groups
        )
        top_picks = enhanced_groups["top_picks"]
        item_recs = enhanced_groups["item_recs"]
        loved_items = enhanced_groups["loved_items"]
        liked_items = enhanced_groups["liked_items"]
        genre_1_items = enhanced_groups["genre_1_items"]
        genre_2_items = enhanced_groups["genre_2_items"]

    # 8. Deduplicate and pad lists to prevent identical listings across rows
    shown_ids = set()
    watched_titles_filter = watched_titles if filter_watched else set()

    def pad_catalog(items, fallback_list, shown_ids_set, watched_titles_set, min_count=15, default_desc=None):
        padded_items = []
        for item in items:
            if watched_titles_set:
                title = item.get("name", "")
                if title and title.lower() in watched_titles_set:
                    continue
                item_id = item.get("id")
                if item_id and ":" in item_id:
                    tracker, ext_id = item_id.split(":", 1)
                    if tracker == "mal" and ext_id in watched_mal_ids:
                        continue
                    if tracker == "anilist" and ext_id in watched_anilist_ids:
                        continue
                    if tracker == "kitsu" and ext_id in watched_kitsu_ids:
                        continue
            if item["id"] not in shown_ids_set:
                shown_ids_set.add(item["id"])
                item_copy = item.copy()
                curr_desc = item_copy.get("description", "")
                if default_desc and "\n\n" not in curr_desc:
                    syn = item_copy.get("synopsis") or curr_desc
                    item_copy["description"] = f"{default_desc}  \n\n{syn}" if syn else default_desc
                padded_items.append(item_copy)

        for fb_item in fallback_list:
            if len(padded_items) >= min_count:
                break
            if fb_item["id"] in shown_ids_set:
                continue
            title = fb_item.get("name", "")
            if title and title.lower() in watched_titles_set:
                continue
            fb_id = fb_item["id"]
            if ":" in fb_id:
                tracker, ext_id = fb_id.split(":", 1)
                if tracker == "mal" and ext_id in watched_mal_ids:
                    continue
                if tracker == "anilist" and ext_id in watched_anilist_ids:
                    continue
                if tracker == "kitsu" and ext_id in watched_kitsu_ids:
                    continue
            shown_ids_set.add(fb_item["id"])
            item_copy = fb_item.copy()
            fb_desc = item_copy.get("synopsis") or item_copy.get("description") or ""
            if default_desc:
                item_copy["description"] = f"{default_desc}  \n\n{fb_desc}" if fb_desc else default_desc
            padded_items.append(item_copy)

        if len(padded_items) < min_count:
            for fb_item in fallback_list:
                if len(padded_items) >= min_count:
                    break
                if any(x["id"] == fb_item["id"] for x in padded_items):
                    continue
                title = fb_item.get("name", "")
                if title and title.lower() in watched_titles_set:
                    continue
                fb_id = fb_item["id"]
                if ":" in fb_id:
                    tracker, ext_id = fb_id.split(":", 1)
                    if tracker == "mal" and ext_id in watched_mal_ids:
                        continue
                    if tracker == "anilist" and ext_id in watched_anilist_ids:
                        continue
                    if tracker == "kitsu" and ext_id in watched_kitsu_ids:
                        continue
                item_copy = fb_item.copy()
                fb_desc = item_copy.get("synopsis") or item_copy.get("description") or ""
                if default_desc:
                    item_copy["description"] = f"{default_desc}  \n\n{fb_desc}" if fb_desc else default_desc
                padded_items.append(item_copy)
        return padded_items

    top_picks = pad_catalog(
        top_picks,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        min_count=15,
        default_desc="Popular community recommendation.",
    )
    item_recs = pad_catalog(
        item_recs,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        min_count=15,
        default_desc="Popular community recommendation.",
    )
    loved_items = pad_catalog(
        loved_items,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        min_count=15,
        default_desc="Popular trending anime you might enjoy.",
    )
    liked_items = pad_catalog(
        liked_items,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        min_count=15,
        default_desc="Popular trending anime you might enjoy.",
    )
    genre_1_items = pad_catalog(
        genre_1_items,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        min_count=15,
        default_desc=f"Popular {genre_1_name} collection.",
    )
    genre_2_items = pad_catalog(
        genre_2_items,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        min_count=15,
        default_desc=f"Popular {genre_2_name} collection.",
    )

    def apply_sorting_order(metas):
        if rec_sorting_order == "series_first":
            return sorted(metas, key=lambda x: 0 if x.get("type") == "series" else 1)
        elif rec_sorting_order == "movies_first":
            return sorted(metas, key=lambda x: 0 if x.get("type") == "movie" else 1)
        return metas

    top_picks = apply_sorting_order(top_picks)[:30]
    item_recs = apply_sorting_order(item_recs)[:30]
    loved_items = apply_sorting_order(loved_items)[:30]
    liked_items = apply_sorting_order(liked_items)[:30]
    genre_1_items = apply_sorting_order(genre_1_items)[:30]
    genre_2_items = apply_sorting_order(genre_2_items)[:30]

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    recommendations_cache_collection.update_one(
        {"uid": user_id},
        {
            "$set": {
                "uid": user_id,
                "rec_items": top_picks,
                "item_items": item_recs,
                "item_seed_title": seed_show["title"] if seed_show else "Steins;Gate",
                "loved_items": loved_items,
                "liked_items": liked_items,
                "genre_1_items": genre_1_items,
                "genre_1_name": genre_1_name,
                "genre_2_items": genre_2_items,
                "genre_2_name": genre_2_name,
                "last_updated": now_utc,
                "expires_at": now_utc + datetime.timedelta(days=30),
            }
        },
        upsert=True,
    )
    logger.info("Successfully updated recommendations cache for user %s", user_id)

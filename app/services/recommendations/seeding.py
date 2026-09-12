import asyncio
import datetime
import logging
import random

from app.lib.id_resolver import resolve, resolve_anilist_to_kitsu, resolve_mal_to_kitsu
from app.services.http import get_client
from app.services.recommendations.fetchers import (
    get_anilist_recommendations_bulk,
    get_mal_recommendations_for_id,
)
from app.services.recommendations.utils import clean_html, is_proper_anime

logger = logging.getLogger(__name__)


def select_weighted_seeds(pool, count):
    if len(pool) <= count:
        return pool
    selected = []
    pool_copy = list(pool)
    while len(selected) < count and pool_copy:
        weights = []
        for x in pool_copy:
            rating = x.get("rating") or 0
            if rating >= 9:
                w = 10
            elif 7 <= rating <= 8:
                w = 7
            elif 1 <= rating <= 6:
                w = 4
            else:  # unrated
                w = 5
            weights.append(w)
        choice = random.choices(pool_copy, weights=weights, k=1)[0]
        selected.append(choice)
        pool_copy.remove(choice)
    return selected


async def get_recommendations_for_seeds(
    seeds: list[dict],
    user: dict,
    watched_mal_ids: set,
    watched_anilist_ids: set,
    watched_titles: set,
    max_seeds: int = 15,
    watched_kitsu_ids: set | None = None,
) -> list[dict]:
    if not seeds:
        return []
    if watched_kitsu_ids is None:
        watched_kitsu_ids = set()

    rec_popularity = user.get("rec_popularity", "balanced")
    rec_year_min = user.get("rec_year_min", 1980)
    rec_year_max = user.get("rec_year_max", datetime.datetime.now(datetime.timezone.utc).year + 1)
    rec_excluded_movie_genres = user.get("rec_excluded_movie_genres", [])
    rec_excluded_series_genres = user.get("rec_excluded_series_genres", [])
    filter_watched = user.get("recommendations_filter_watched", True)

    title_lang = (user.get("title_language", "english") or "english").lower() if user else "english"
    rec_candidates = {}

    # 1. Fetch from AniList in bulk
    al_ids = [int(s["anilist_id"]) for s in seeds[:max_seeds] if s["anilist_id"]]
    al_recs = []
    anilist_id_to_title = {str(s["anilist_id"]): s["title"] for s in seeds if s.get("anilist_id")}
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

        # Watched filters
        if filter_watched:
            if aid in watched_anilist_ids or (mid and mid in watched_mal_ids):
                continue

        # Year filter
        year = (media.get("startDate") or {}).get("year")
        if year and (year < rec_year_min or year > rec_year_max):
            continue

        item_type = "movie" if m_format == "MOVIE" else "series"

        # Excluded genres filter
        genres = media.get("genres", []) or []
        excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
        if any(g in excluded_genres for g in genres):
            continue

        # Popularity filters
        pop_score = media.get("popularity") or 0
        avg_score = media.get("averageScore") or 0
        if rec_popularity == "mainstream":
            if pop_score < 25000:
                continue
        elif rec_popularity == "gems":
            if pop_score >= 25000 or avg_score < 73:
                continue

        # Choose title based on user language preference
        title_pref = media.get("title", {})
        if title_lang == "japanese":
            title = (
                title_pref.get("native")
                or title_pref.get("userPreferred")
                or title_pref.get("english")
                or "Unknown Title"
            )
        elif title_lang == "romaji":
            title = (
                title_pref.get("romaji")
                or title_pref.get("userPreferred")
                or title_pref.get("english")
                or "Unknown Title"
            )
        else:
            title = (
                title_pref.get("english")
                or title_pref.get("userPreferred")
                or title_pref.get("romaji")
                or "Unknown Title"
            )

        if filter_watched and title.lower() in watched_titles:
            continue

        desc = media.get("description") or ""
        if not is_proper_anime(title, desc):
            continue

        poster = (media.get("coverImage") or {}).get("large") or (media.get("coverImage") or {}).get("medium") or ""
        syn = clean_html(media.get("description") or "")

        avg_score_val = round((avg_score / 10.0 if avg_score > 10 else float(avg_score)), 1) if avg_score else 0.0
        year_val = int(year or 0)
        episodes_val = int(media.get("episodes") or 0)

        key = f"mal:{mid}" if mid else f"anilist:{aid}"
        if key not in rec_candidates:
            rec_candidates[key] = {
                "id": key,
                "type": item_type,
                "name": title,
                "poster": poster,
                "poster_al": poster,
                "background": media.get("bannerImage"),
                "anilist_id": aid,
                "mal_id": mid,
                "score": avg_score_val,
                "average_score": avg_score_val,
                "year": year_val,
                "episodes": episodes_val,
                "popularity": int(pop_score or 0),
                "rec_votes": rec.get("rating", 1),
                "description": "Recommended based on your history.",
                "synopsis": syn,
                "inspired_by_titles": [seed_title] if seed_title else [],
            }
        else:
            rec_candidates[key]["rec_votes"] += rec.get("rating", 1)
            if not rec_candidates[key].get("score") and avg_score_val:
                rec_candidates[key]["score"] = avg_score_val
                rec_candidates[key]["average_score"] = avg_score_val
            if not rec_candidates[key].get("year") and year_val:
                rec_candidates[key]["year"] = year_val
            if not rec_candidates[key].get("episodes") and episodes_val:
                rec_candidates[key]["episodes"] = episodes_val
            if syn and not rec_candidates[key].get("synopsis"):
                rec_candidates[key]["synopsis"] = syn
            if seed_title and seed_title not in rec_candidates[key]["inspired_by_titles"]:
                rec_candidates[key]["inspired_by_titles"].append(seed_title)

    # 2. Fetch from MAL (limit to top 5 seeds for rate limits)
    mal_seed_shows = [s for s in seeds[:5] if s["mal_id"]]
    if mal_seed_shows and user.get("mal_access_token") and user.get("mal_enabled", True):
        tasks = [get_mal_recommendations_for_id(user["mal_access_token"], s["mal_id"]) for s in mal_seed_shows]
        mal_recs_lists = await asyncio.gather(*tasks)

        from app.services.db import get_cached_ids_by_mal_bulk

        all_mal_ids = [
            str(r.get("node", {}).get("id"))
            for sublist in mal_recs_lists
            for r in sublist
            if r.get("node", {}).get("id")
        ]
        mal_id_cache_map = get_cached_ids_by_mal_bulk(all_mal_ids)

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

                # Watched filters
                if filter_watched:
                    if mid in watched_mal_ids:
                        continue
                    if title.lower() in watched_titles:
                        continue

                # Year filter
                year = (node.get("start_season") or {}).get("year")
                if year and (year < rec_year_min or year > rec_year_max):
                    continue

                item_type = "movie" if m_type == "movie" else "series"

                # Excluded genres filter
                genres = [g.get("name") for g in node.get("genres", []) if g.get("name")]
                excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
                if any(g in excluded_genres for g in genres):
                    continue

                # Popularity filters
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

                c_doc = mal_id_cache_map.get(str(mid))
                aid = str(c_doc["anilist_id"]) if c_doc and c_doc.get("anilist_id") else None

                from app.lib.meta_providers import get_al_cover, get_effective_meta_providers

                al_poster = get_al_cover(aid)
                rec_poster_pref = get_effective_meta_providers(user).get("poster", "kitsu")
                chosen_poster = al_poster if (rec_poster_pref == "anilist" and al_poster) else poster

                mal_score_val = round(float(mean_score), 1) if mean_score else 0.0
                mal_year_val = int(year or 0)
                mal_episodes_val = int(node.get("num_episodes") or 0)
                mal_pop_val = int(pop_rank or 0)

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
                        "score": mal_score_val,
                        "average_score": mal_score_val,
                        "year": mal_year_val,
                        "episodes": mal_episodes_val,
                        "popularity": mal_pop_val,
                        "rec_votes": rec.get("num_recommendations", 1),
                        "description": "Recommended based on your history.",
                        "synopsis": syn,
                        "inspired_by_titles": [seed_title],
                    }
                else:
                    rec_candidates[key]["rec_votes"] += rec.get("num_recommendations", 1)
                    if not rec_candidates[key].get("score") and mal_score_val:
                        rec_candidates[key]["score"] = mal_score_val
                        rec_candidates[key]["average_score"] = mal_score_val
                    if not rec_candidates[key].get("year") and mal_year_val:
                        rec_candidates[key]["year"] = mal_year_val
                    if not rec_candidates[key].get("episodes") and mal_episodes_val:
                        rec_candidates[key]["episodes"] = mal_episodes_val
                    if syn and not rec_candidates[key].get("synopsis"):
                        rec_candidates[key]["synopsis"] = syn
                    if seed_title not in rec_candidates[key]["inspired_by_titles"]:
                        rec_candidates[key]["inspired_by_titles"].append(seed_title)

    # 3. Kitsu Media Relationships (fetch sequels, prequels, spin-offs for up to 15 seeds)
    kitsu_seed_shows = list(seeds[:15])
    if kitsu_seed_shows:

        async def fetch_kitsu_relationships_for_seed(s):
            try:
                kitsu_id = None
                if s.get("mal_id"):
                    kitsu_id = await resolve_mal_to_kitsu(s["mal_id"])
                elif s.get("anilist_id"):
                    kitsu_id = await resolve_anilist_to_kitsu(s["anilist_id"])
                if not kitsu_id:
                    return s, []

                url = f"https://kitsu.io/api/edge/anime/{kitsu_id}/media-relationships?include=destination"
                headers = {
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                    "User-Agent": "Mozilla/5.0",
                }
                client = get_client()
                resp = await client.get(url, headers=headers, timeout=10)
                if resp.status_code != 200:
                    return s, []

                data = resp.json()
                included = data.get("included") or []
                related_items = []
                for item in included:
                    if item.get("type") == "anime":
                        kid = item.get("id")
                        attrs = item.get("attributes", {})
                        if not kid or not attrs:
                            continue

                        subtype = (attrs.get("subtype") or "tv").lower()
                        episode_length = attrs.get("episodeLength")
                        if subtype in ["ova", "special", "music"]:
                            continue
                        if episode_length is not None and episode_length <= 5:
                            continue

                        if attrs.get("status") in ["upcoming", "unreleased", "tba"]:
                            continue

                        start_date = attrs.get("startDate")
                        k_year = None
                        if start_date:
                            try:
                                k_year = int(start_date[:4])
                            except ValueError:
                                pass

                        item_type = "movie" if subtype == "movie" else "series"
                        titles = attrs.get("titles") or {}
                        title = attrs.get("canonicalTitle") or titles.get("en") or titles.get("en_jp")
                        if not is_proper_anime(title):
                            continue
                        poster_img = attrs.get("posterImage") or {}
                        poster = (
                            poster_img.get("large")
                            or poster_img.get("medium")
                            or poster_img.get("original")
                            or ""
                        )
                        if poster:
                            poster = poster.split("?")[0]
                        synopsis = attrs.get("synopsis") or ""

                        related_items.append({
                            "kitsu_id": kid,
                            "name": title,
                            "poster": poster,
                            "type": item_type,
                            "year": k_year,
                            "description": synopsis[:200] + "..." if len(synopsis) > 200 else synopsis,
                        })
                return s, related_items
            except Exception as e:
                logger.warning("Kitsu relationship lookup failed for seed %s: %s", s.get("title"), e)
                return s, []

        kitsu_tasks = [fetch_kitsu_relationships_for_seed(s) for s in kitsu_seed_shows]
        kitsu_results = await asyncio.gather(*kitsu_tasks)

        for s, related_items in kitsu_results:
            seed_title = s["title"]
            for r_item in related_items:
                if filter_watched and r_item["name"].lower() in watched_titles:
                    continue

                mid, aid = await resolve(r_item["kitsu_id"])

                if filter_watched:
                    if (
                        (mid and mid in watched_mal_ids)
                        or (aid and aid in watched_anilist_ids)
                        or (r_item.get("kitsu_id") and str(r_item["kitsu_id"]) in watched_kitsu_ids)
                    ):
                        continue

                year = r_item.get("year")
                if year and (year < rec_year_min or year > rec_year_max):
                    continue

                key = f"mal:{mid}" if mid else f"anilist:{aid}" if aid else f"kitsu:{r_item['kitsu_id']}"
                syn = clean_html(r_item.get("description") or "")

                from app.lib.meta_providers import get_al_cover, get_effective_meta_providers

                al_poster = get_al_cover(aid)
                rec_poster_pref = get_effective_meta_providers(user).get("poster", "kitsu")
                chosen_poster = al_poster if (rec_poster_pref == "anilist" and al_poster) else r_item["poster"]

                if key not in rec_candidates:
                    rec_candidates[key] = {
                        "id": key,
                        "type": r_item["type"],
                        "name": r_item["name"],
                        "poster": chosen_poster,
                        "poster_kitsu": r_item["poster"],
                        "poster_al": al_poster or r_item["poster"],
                        "kitsu_id": str(r_item["kitsu_id"]),
                        "mal_id": str(mid) if mid else None,
                        "anilist_id": str(aid) if aid else None,
                        "score": float(r_item.get("score") or r_item.get("average_score") or 0.0),
                        "average_score": float(r_item.get("score") or r_item.get("average_score") or 0.0),
                        "year": int(r_item.get("year") or 0),
                        "episodes": int(r_item.get("episodes") or 0),
                        "rec_votes": 10,
                        "description": r_item["description"] or "Franchise sequel, prequel, or spin-off.",
                        "synopsis": syn,
                        "inspired_by_titles": [seed_title],
                    }
                else:
                    rec_candidates[key]["rec_votes"] += 10
                    if syn and not rec_candidates[key].get("synopsis"):
                        rec_candidates[key]["synopsis"] = syn
                    if seed_title not in rec_candidates[key]["inspired_by_titles"]:
                        rec_candidates[key]["inspired_by_titles"].append(seed_title)

    sorted_recs = sorted(rec_candidates.values(), key=lambda x: x.get("rec_votes", 0), reverse=True)
    for r in sorted_recs:
        r.pop("rec_votes", None)
        if not r.get("score") and r.get("average_score"):
            r["score"] = r["average_score"]
    return sorted_recs

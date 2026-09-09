import asyncio

from app.lib.meta_providers import get_al_cover, get_effective_meta_providers
from app.services.db import get_cached_ids_by_mal_bulk
from app.services.recommendations.fetchers import (
    get_anilist_recommendations_bulk,
    get_mal_recommendations_for_id,
)
from app.services.recommendations.utils import clean_html, is_proper_anime


async def generate_top_picks(
    user: dict,
    user_id: str,
    top_picks_seeds: list[dict],
    filter_watched: bool,
    watched_mal_ids: set[str],
    watched_anilist_ids: set[str],
    watched_titles: set[str],
    rec_language: str,
    rec_popularity: str,
    rec_year_min: int,
    rec_year_max: int,
    rec_excluded_movie_genres: list[str],
    rec_excluded_series_genres: list[str],
    title_lang: str,
) -> list[dict]:
    """
    Generates community-recommended Top Picks by querying AniList and MAL recommendation APIs
    against the user's top seed anime, applying content, duration, year, and popularity filters.
    """
    rec_candidates = {}

    # 1. AniList Bulk query for top picks seeds
    al_ids = [int(s["anilist_id"]) for s in top_picks_seeds if s.get("anilist_id")]
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

    # 2. MAL recommendations query for top 5 MAL shows
    mal_seed_shows = [s for s in top_picks_seeds if s.get("mal_id")][:5]
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

                c_doc = genre_id_cache_map.get(str(mid))
                aid = str(c_doc["anilist_id"]) if c_doc and c_doc.get("anilist_id") else None

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

    return top_picks

import datetime
import logging

from app.services.recommendations.fetchers import get_top_anime_by_genre
from app.services.recommendations.utils import clean_html, is_proper_anime

logger = logging.getLogger(__name__)


async def generate_genre_recommendations(
    genre: str,
    user: dict,
    watched_mal_ids: set,
    watched_anilist_ids: set,
    watched_titles: set,
    watched_kitsu_ids: set | None = None,
) -> list[dict]:
    if watched_kitsu_ids is None:
        watched_kitsu_ids = set()
    rec_popularity = user.get("rec_popularity", "balanced")
    rec_year_min = user.get("rec_year_min", 1980)
    rec_year_max = user.get("rec_year_max", datetime.datetime.now(datetime.timezone.utc).year + 1)
    rec_excluded_movie_genres = user.get("rec_excluded_movie_genres", [])
    rec_excluded_series_genres = user.get("rec_excluded_series_genres", [])
    filter_watched = user.get("recommendations_filter_watched", True)

    sort_order = "POPULARITY_DESC"
    if rec_popularity == "gems":
        sort_order = "SCORE_DESC"

    token = user.get("anilist_token")
    media_list = await get_top_anime_by_genre(token, genre, sort_order)

    recs = []
    for media in media_list:
        if not media:
            continue

        if media.get("status") == "NOT_YET_RELEASED":
            continue

        m_format = media.get("format")
        duration = media.get("duration")
        if m_format in ["OVA", "SPECIAL", "MUSIC", "TV_SHORT"]:
            continue
        if duration is not None and duration <= 5:
            continue

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
        title_lang = (user.get("title_language", "english") or "english").lower() if user else "english"
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

        if not is_proper_anime(title):
            continue

        poster = (media.get("coverImage") or {}).get("large") or (media.get("coverImage") or {}).get("medium") or ""

        key = f"mal:{mid}" if mid else f"anilist:{aid}"
        syn = clean_html(media.get("description") or "")
        desc_header = f"Popular {genre} anime based on your taste."
        full_desc = f"{desc_header}  \n\n{syn}" if syn else desc_header

        recs.append({
            "id": key,
            "type": item_type,
            "name": title,
            "poster": poster,
            "poster_al": poster,
            "background": media.get("bannerImage"),
            "anilist_id": aid,
            "mal_id": mid,
            "description": full_desc,
            "synopsis": syn,
        })
    return recs

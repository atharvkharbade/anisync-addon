import datetime
import logging
import random

from app.services.db import get_user
from app.services.recommendations.ai_enhancer import enhance_recommendations_with_gemini
from app.services.recommendations.cache import get_popular_fallbacks, recommendations_cache_collection
from app.services.recommendations.history import fetch_user_watchlist_history
from app.services.recommendations.seeding import select_weighted_seeds

from .genres import generate_favorite_genre_recommendations
from .heuristics import generate_seed_based_recommendations
from .padding import apply_sorting_order, pad_catalog
from .top_picks import generate_top_picks

logger = logging.getLogger(__name__)


async def _update_recommendations_cache_impl(user_id: str, force: bool = False):
    import app.services.recommendations.pipeline as pipeline_pkg

    user = pipeline_pkg.get_user(user_id)
    if not user or not user.get("enable_recommendations", False):
        return
    fallbacks = pipeline_pkg.get_popular_fallbacks()

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
        key=lambda x: (1 if x.get("status") in ["completed", "watching"] else 0, x.get("rating") or 0),
        reverse=True,
    )

    seed_pool = [s for s in merged_shows.values() if s.get("status") in ["completed", "watching", "on_hold"]]
    if not seed_pool:
        seed_pool = [s for s in merged_shows.values() if s.get("status") == "planning"]
    if len(seed_pool) > 50:
        seed_pool = random.sample(seed_pool, 50)

    sorted_seed_pool = sorted(
        seed_pool,
        key=lambda x: (1 if x.get("status") in ["completed", "watching"] else 0, x.get("rating") or 0),
        reverse=True,
    )
    recent_seeds = sorted_seed_pool[:5]
    remaining_pool = [s for s in seed_pool if s not in recent_seeds]
    random_seeds = select_weighted_seeds(remaining_pool, 10)
    top_picks_seeds = recent_seeds + random_seeds

    # 2. Generate "Top Picks" (Community Recs)
    top_picks = await generate_top_picks(
        user,
        user_id,
        top_picks_seeds,
        filter_watched,
        watched_mal_ids,
        watched_anilist_ids,
        watched_titles,
        rec_language,
        rec_popularity,
        rec_year_min,
        rec_year_max,
        rec_excluded_movie_genres,
        rec_excluded_series_genres,
        title_lang,
    )

    # 3-5. Generate "Because you Watched", "Inspired by your Favorites", "More from your Watchlist"
    (
        item_recs,
        seed_show,
        loved_items,
        liked_items,
    ) = await generate_seed_based_recommendations(
        user,
        seed_pool,
        fallbacks,
        watched_mal_ids,
        watched_anilist_ids,
        watched_kitsu_ids,
        watched_titles,
        filter_watched,
    )

    # 6. Favorite Genre Collections
    (
        genre_1_items,
        genre_1_name,
        genre_2_items,
        genre_2_name,
    ) = await generate_favorite_genre_recommendations(
        merged_shows,
        user,
        fallbacks,
        watched_mal_ids,
        watched_anilist_ids,
        watched_kitsu_ids,
        watched_titles,
    )

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

    top_picks = pad_catalog(
        top_picks,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        watched_mal_ids=watched_mal_ids,
        watched_anilist_ids=watched_anilist_ids,
        watched_kitsu_ids=watched_kitsu_ids,
        min_count=15,
        default_desc="Popular community recommendation.",
        fallback_offset=0,
    )
    item_recs = pad_catalog(
        item_recs,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        watched_mal_ids=watched_mal_ids,
        watched_anilist_ids=watched_anilist_ids,
        watched_kitsu_ids=watched_kitsu_ids,
        min_count=15,
        default_desc="Popular community recommendation.",
        fallback_offset=15,
    )
    loved_items = pad_catalog(
        loved_items,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        watched_mal_ids=watched_mal_ids,
        watched_anilist_ids=watched_anilist_ids,
        watched_kitsu_ids=watched_kitsu_ids,
        min_count=15,
        default_desc="Popular trending anime you might enjoy.",
        fallback_offset=30,
    )
    liked_items = pad_catalog(
        liked_items,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        watched_mal_ids=watched_mal_ids,
        watched_anilist_ids=watched_anilist_ids,
        watched_kitsu_ids=watched_kitsu_ids,
        min_count=15,
        default_desc="Popular trending anime you might enjoy.",
        fallback_offset=45,
    )
    genre_1_items = pad_catalog(
        genre_1_items,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        watched_mal_ids=watched_mal_ids,
        watched_anilist_ids=watched_anilist_ids,
        watched_kitsu_ids=watched_kitsu_ids,
        min_count=15,
        default_desc=f"Popular {genre_1_name} collection.",
        fallback_offset=60,
    )
    genre_2_items = pad_catalog(
        genre_2_items,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        watched_mal_ids=watched_mal_ids,
        watched_anilist_ids=watched_anilist_ids,
        watched_kitsu_ids=watched_kitsu_ids,
        min_count=15,
        default_desc=f"Popular {genre_2_name} collection.",
        fallback_offset=75,
    )

    top_picks = apply_sorting_order(top_picks, rec_sorting_order)[:30]
    item_recs = apply_sorting_order(item_recs, rec_sorting_order)[:30]
    loved_items = apply_sorting_order(loved_items, rec_sorting_order)[:30]
    liked_items = apply_sorting_order(liked_items, rec_sorting_order)[:30]
    genre_1_items = apply_sorting_order(genre_1_items, rec_sorting_order)[:30]
    genre_2_items = apply_sorting_order(genre_2_items, rec_sorting_order)[:30]

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

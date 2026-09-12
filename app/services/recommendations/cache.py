import asyncio
import logging
import re

from app.api import anilist as anilist_api
from app.services.db import db, get_user
from app.services.recommendations.utils import is_proper_anime

logger = logging.getLogger(__name__)

recommendations_cache_collection = db.get_collection("recommendations_cache")
popular_fallbacks_collection = db.get_collection("popular_fallbacks")

currently_updating_users = set()
POPULAR_FALLBACKS = []


def get_cached_recommendations(user_id: str) -> dict | None:
    return recommendations_cache_collection.find_one({"uid": user_id})


def get_popular_fallbacks() -> list[dict]:
    """Retrieve fallback list from database cache, or fallback to the static list if empty."""
    try:
        cached = list(popular_fallbacks_collection.find({}, {"_id": 0}))
        if cached and len(cached) >= 15:
            return cached
    except Exception as e:
        logger.error("Failed to read popular fallbacks from MongoDB: %s", e)
    return POPULAR_FALLBACKS


async def update_popular_fallbacks_cache():
    """Fetch the top 80 most popular anime from AniList and cache them in MongoDB."""
    query = """
    query {
      Page(page: 1, perPage: 80) {
        media(type: ANIME, sort: POPULARITY_DESC, isAdult: false) {
          id
          idMal
          status
          format
          duration
          episodes
          averageScore
          popularity
          startDate {
            year
          }
          title {
            english
            romaji
            userPreferred
          }
          coverImage {
            large
          }
          bannerImage
          description
        }
      }
    }
    """
    try:
        logger.info("Updating popular fallbacks cache from AniList...")
        res = await anilist_api._gql(None, query)
        data = res.get("data", {}).get("Page", {}).get("media", [])
        if data:
            new_items = []
            for media in data:
                if media.get("status") == "NOT_YET_RELEASED":
                    continue
                # Exclude OVA, SPECIAL, MUSIC, TV_SHORT from popular fallbacks and short durations (<= 5 minutes)
                m_format = media.get("format")
                duration = media.get("duration")
                if m_format in ["OVA", "SPECIAL", "MUSIC", "TV_SHORT"]:
                    continue
                if duration is not None and duration <= 5:
                    continue
                mal_id = media.get("idMal")
                item_id = f"mal:{mal_id}" if mal_id else f"anilist:{media.get('id')}"
                item_type = "movie" if m_format == "MOVIE" else "series"
                title_pref = media.get("title", {})
                name = title_pref.get("english") or title_pref.get("userPreferred") or title_pref.get("romaji")
                if not is_proper_anime(name):
                    continue
                poster = (media.get("coverImage") or {}).get("large") or ""
                desc = media.get("description") or ""
                desc = re.sub("<[^<]+?>", "", desc)
                desc = desc[:150] + "..." if len(desc) > 150 else desc
                desc = desc.replace("\n", " ").replace("  ", " ").strip()
                avg_sc = media.get("averageScore") or 0
                sc_val = round((avg_sc / 10.0 if avg_sc > 10 else float(avg_sc)), 1) if avg_sc else 0.0
                new_items.append({
                    "id": item_id,
                    "type": item_type,
                    "name": name,
                    "poster": poster,
                    "poster_al": poster,
                    "background": media.get("bannerImage"),
                    "anilist_id": str(media.get("id")),
                    "mal_id": str(mal_id) if mal_id else None,
                    "score": sc_val,
                    "year": int((media.get("startDate") or {}).get("year") or 0),
                    "episodes": int(media.get("episodes") or 0),
                    "popularity": int(media.get("popularity") or 0),
                    "description": desc,
                })
            if new_items:
                popular_fallbacks_collection.delete_many({})
                popular_fallbacks_collection.insert_many(new_items)
                logger.info("Successfully cached %d popular fallbacks from AniList.", len(new_items))
                return
        logger.warning("AniList returned empty data for popular fallbacks, attempting Jikan fallback...")
    except Exception as e:
        logger.error("Failed to update popular fallbacks cache from AniList: %s, trying Jikan...", e)

    try:
        from app.api.jikan import get_top_anime
        from app.services.db import get_cached_ids_by_mal_bulk

        jikan_top = await get_top_anime(type_filter="tv", page=1)
        if jikan_top:
            fallback_mids = [str(item.get("mal_id")) for item in jikan_top[:40] if item.get("mal_id")]
            fallback_id_map = get_cached_ids_by_mal_bulk(fallback_mids)

            new_items = []
            for item in jikan_top[:40]:
                mal_id = item.get("mal_id")
                name = item.get("title_english") or item.get("title") or "Unknown Anime"
                desc = item.get("synopsis") or ""
                desc = re.sub("<[^<]+?>", "", desc)
                desc = desc[:150] + "..." if len(desc) > 150 else desc
                desc = desc.replace("\n", " ").replace("  ", " ").strip()
                images = item.get("images", {}).get("jpg", {})
                poster = images.get("large_image_url") or images.get("image_url") or ""
                if mal_id:
                    c_doc = fallback_id_map.get(str(mal_id))
                    aid = str(c_doc["anilist_id"]) if c_doc and c_doc.get("anilist_id") else None
                    new_items.append({
                        "id": f"mal:{mal_id}",
                        "type": "series",
                        "name": name,
                        "poster": poster,
                        "poster_mal": poster,
                        "mal_id": str(mal_id),
                        "anilist_id": aid,
                        "score": round(float(item.get("score") or 0.0), 1),
                        "year": int(item.get("year") or 0),
                        "episodes": int(item.get("episodes") or 0),
                        "popularity": int(item.get("popularity") or 0),
                        "description": desc,
                    })
            if new_items:
                popular_fallbacks_collection.delete_many({})
                popular_fallbacks_collection.insert_many(new_items)
                logger.info("Successfully cached %d popular fallbacks from Jikan API.", len(new_items))
    except Exception as ex:
        logger.error("Failed to update popular fallbacks from Jikan fallback: %s", ex)


async def popular_fallbacks_loop():
    """Background loop to update popular fallbacks once every 24 hours."""
    await asyncio.sleep(5)  # Wait for app startup
    while True:
        try:
            await update_popular_fallbacks_cache()
        except Exception as e:
            logger.error("Error in popular fallbacks loop: %s", e)
        await asyncio.sleep(24 * 3600)


def trigger_popular_fallbacks_update_background():
    """Start the background popular fallbacks updater task."""
    asyncio.create_task(popular_fallbacks_loop())


async def update_recommendations_cache(user_id: str, force: bool = False):
    if user_id in currently_updating_users:
        return
    currently_updating_users.add(user_id)
    try:
        from app.services.recommendations.engine import _update_recommendations_cache_impl

        await _update_recommendations_cache_impl(user_id, force)
    except Exception as e:
        logger.exception("Error updating recommendations for user %s: %s", user_id, e)
    finally:
        currently_updating_users.discard(user_id)


def trigger_recommendation_update_background(user_id: str, force: bool = False):
    user = get_user(user_id)
    if not user or not user.get("enable_recommendations", False):
        return
    asyncio.create_task(update_recommendations_cache(user_id, force=force))

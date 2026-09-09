import logging
from datetime import UTC, datetime, timedelta

from .connection import db, jikan_cache_collection

# ── Jikan episode filler cache ────────────────────────────────────────────────

JIKAN_CACHE_TTL_HOURS = 168


def get_jikan_filler_cache(mal_id: str, episode: int) -> bool | None:
    """Return cached filler status for an episode, or None if not cached / expired."""
    try:
        doc = jikan_cache_collection.find_one({"mal_id": str(mal_id), "episode": int(episode)})
        if doc:
            cached_at = doc.get("cached_at", datetime.min)
            # Ensure naive UTC for age comparison if doc was stored naive
            if hasattr(cached_at, "tzinfo") and cached_at.tzinfo is not None:
                now = datetime.now(UTC)
            else:
                now = datetime.now(UTC).replace(tzinfo=None)
            age = now - cached_at
            if age < timedelta(hours=JIKAN_CACHE_TTL_HOURS):
                return bool(doc["filler"])
    except Exception as e:
        logging.error("Jikan cache read error: %s", e)
    return None


def set_jikan_filler_cache(mal_id: str, episode: int, filler: bool):
    """Cache the Jikan filler result for an episode."""
    try:
        jikan_cache_collection.update_one(
            {"mal_id": str(mal_id), "episode": int(episode)},
            {
                "$set": {
                    "mal_id": str(mal_id),
                    "episode": int(episode),
                    "filler": filler,
                    "cached_at": datetime.now(UTC).replace(tzinfo=None),
                }
            },
            upsert=True,
        )
    except Exception as e:
        logging.error("Jikan cache write error: %s", e)


# ── AniList media cache ───────────────────────────────────────────────────────


def get_cached_anilist_media(anilist_id: int) -> dict | None:
    """Retrieve cached AniList media metadata and dynamically update timeUntilAiring."""
    try:
        col = db.get_collection("anilist_meta_cache")
        now = datetime.now(UTC)
        doc = col.find_one({"anilist_id": anilist_id, "expires_at": {"$gt": now}})
        if not doc:
            return None

        media_data = doc.get("data")
        if not media_data or not isinstance(media_data, dict):
            return None

        # Dynamically recalculate timeUntilAiring for live accuracy
        next_ep = media_data.get("nextAiringEpisode")
        if next_ep and isinstance(next_ep, dict) and next_ep.get("airingAt"):
            airing_at = next_ep["airingAt"]
            current_epoch = int(now.timestamp())
            if current_epoch >= airing_at:
                # Episode already aired, consider cache stale so fresh data is fetched
                return None
            next_ep["timeUntilAiring"] = max(0, airing_at - current_epoch)

        return media_data
    except Exception as e:
        logging.error("Failed to read from anilist_meta_cache for anilist_id=%s: %s", anilist_id, e)
        return None


def cache_anilist_media(anilist_id: int, media_data: dict) -> None:
    """Cache AniList media metadata with smart TTL."""
    if not media_data or not isinstance(media_data, dict):
        return

    try:
        col = db.get_collection("anilist_meta_cache")
        now = datetime.now(UTC)

        status = media_data.get("status", "")
        next_ep = media_data.get("nextAiringEpisode")

        # Expiry calculations:
        if status in ["FINISHED", "CANCELLED"]:
            # Static metadata for finished anime is cached for 30 days
            expires_at = now + timedelta(days=30)
        elif next_ep and isinstance(next_ep, dict) and next_ep.get("airingAt"):
            # Airing anime expires 15 mins after the next episode airs, capped at 12h
            target_time = datetime.fromtimestamp(next_ep["airingAt"], tz=UTC) + timedelta(minutes=15)
            max_cap = now + timedelta(hours=12)
            expires_at = min(target_time, max_cap)
        elif status == "NOT_YET_RELEASED":
            expires_at = now + timedelta(days=2)
        else:
            expires_at = now + timedelta(hours=12)

        # Remove any sensitive/user-specific mediaListEntry before saving to public cache
        cached_payload = {k: v for k, v in media_data.items() if k != "mediaListEntry"}

        col.update_one(
            {"anilist_id": anilist_id},
            {
                "$set": {
                    "anilist_id": anilist_id,
                    "data": cached_payload,
                    "expires_at": expires_at,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
    except Exception as e:
        logging.error("Failed to write to anilist_meta_cache for anilist_id=%s: %s", anilist_id, e)


def get_al_cover(aid: str | int | None) -> str:
    """Return cached AniList cover image URL from anilist_airing_cache if available."""
    if not aid:
        return ""
    try:
        col = db.get_collection("anilist_airing_cache")
        doc = col.find_one({"anilist_id": int(aid)})
        if doc and doc.get("coverImage"):
            return str(doc["coverImage"])
    except Exception:
        pass
    return ""

import logging
from datetime import UTC, datetime, timedelta

from .connection import id_cache_collection, id_fallback_misses_collection


def get_cached_ids(kitsu_id: str) -> dict | None:
    try:
        if isinstance(kitsu_id, str):
            kitsu_id = kitsu_id.replace("kitsu:", "").strip()
        return id_cache_collection.find_one({"kitsu_id": int(kitsu_id)})
    except (ValueError, TypeError):
        return None


def get_cached_ids_by_mal(mal_id: str) -> dict | None:
    try:
        return id_cache_collection.find_one({"mal_id": str(mal_id)})
    except (ValueError, TypeError):
        return None


def get_cached_ids_by_mal_bulk(mal_ids: list[str]) -> dict[str, dict]:
    """Retrieve multiple cached ID mappings by MAL ID in a single MongoDB query."""
    if not mal_ids:
        return {}
    try:
        clean_ids = [str(m) for m in mal_ids if m]
        query_vals = clean_ids + [int(m) for m in clean_ids if str(m).isdigit()]
        docs = list(id_cache_collection.find({"mal_id": {"$in": query_vals}}))
        result = {}
        for d in docs:
            mid = str(d.get("mal_id") or "")
            if mid:
                result[mid] = d
        return result
    except Exception as e:
        logging.error("Failed bulk id_cache lookup by mal_id: %s", e)
        return {}


def get_cached_ids_by_anilist(anilist_id: str) -> dict | None:
    try:
        return id_cache_collection.find_one({"anilist_id": str(anilist_id)})
    except (ValueError, TypeError):
        return None


def get_cached_ids_by_simkl(simkl_id: str) -> dict | None:
    try:
        query = {"$or": [{"simkl_id": str(simkl_id)}]}
        if str(simkl_id).isdigit():
            query["$or"].append({"simkl_id": int(simkl_id)})
            query["$or"].append({"simkl": int(simkl_id)})
        return id_cache_collection.find_one(query)
    except Exception:
        return None


def cache_ids(
    kitsu_id: str,
    mal_id: str | None,
    anilist_id: str | None,
    simkl_id: str | None = None,
    imdb_id: str | None = None,
    tmdb_id: str | None = None,
    tvdb_id: str | None = None,
):
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        doc = {
            "kitsu_id": int(kitsu_id) if kitsu_id else None,
            "mal_id": str(mal_id) if mal_id else None,
            "anilist_id": str(anilist_id) if anilist_id else None,
            "simkl_id": str(simkl_id) if simkl_id else None,
            "imdb_id": str(imdb_id) if imdb_id else None,
            "tmdb_id": str(tmdb_id) if tmdb_id else None,
            "tvdb_id": str(tvdb_id) if tvdb_id else None,
            "updated_at": now,
        }
        # Filter out None kitsu_id
        if doc["kitsu_id"] is None:
            return
        update_fields = {k: v for k, v in doc.items() if v is not None}
        id_cache_collection.update_one(
            {"kitsu_id": doc["kitsu_id"]},
            {
                "$set": update_fields,
                "$setOnInsert": {
                    "expires_at": now + timedelta(days=7),
                    "created_at": now,
                },
                "$unset": {"is_fallback": ""},
            },
            upsert=True,
        )
    except Exception as e:
        logging.error("Cache write error: %s", e)


def attach_simkl_id(kitsu_id: str, simkl_id: str):
    """
    Attaches simkl_id to an existing id_cache doc without touching mal_id/anilist_id.
    Never upserts, and never replaces a different simkl_id already stored on the doc.
    """
    try:
        if not kitsu_id or not simkl_id:
            return
        id_cache_collection.update_one(
            {
                "kitsu_id": int(kitsu_id),
                "$or": [{"simkl_id": {"$exists": False}}, {"simkl_id": None}, {"simkl_id": str(simkl_id)}],
            },
            {"$set": {"simkl_id": str(simkl_id)}},
        )
    except Exception as e:
        logging.error("attach_simkl_id write error: %s", e)


def cache_fallback_ids(
    kitsu_id: str,
    mal_id: str | None,
    anilist_id: str | None,
    simkl_id: str | None = None,
):
    """
    Caches an unverified title-fallback guess.
    CRITICAL: Uses $setOnInsert so that if ANY document already exists for kitsu_id,
    it is NEVER modified or overwritten by a guess.
    Expires quickly (3 days) to allow authoritative sources to backfill.
    """
    try:
        kid = int(kitsu_id) if kitsu_id else None
        if not kid:
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        id_cache_collection.update_one(
            {"kitsu_id": kid},
            {
                "$setOnInsert": {
                    "kitsu_id": kid,
                    "mal_id": str(mal_id) if mal_id else None,
                    "anilist_id": str(anilist_id) if anilist_id else None,
                    "simkl_id": str(simkl_id) if simkl_id else None,
                    "is_fallback": True,
                    "created_at": now,
                    "updated_at": now,
                    "expires_at": now + timedelta(days=3),
                }
            },
            upsert=True,
        )
    except Exception as e:
        logging.error("cache_fallback_ids write error: %s", e)


def record_fallback_miss(source: str, source_id: str, reason: str = "", ttl_hours: int = 24):
    """
    Records a negative cache entry (24 hours by default) to prevent repeated fallback searches
    and API rate limiting for anime that have no Kitsu entry. Use a short ttl_hours for
    failures that may be transient (upstream timeouts, rate limits).
    """
    try:
        if not source or not source_id:
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        id_fallback_misses_collection.update_one(
            {"source": str(source), "source_id": str(source_id)},
            {
                "$set": {
                    "source": str(source),
                    "source_id": str(source_id),
                    "reason": str(reason),
                    "updated_at": now,
                    "expires_at": now + timedelta(hours=ttl_hours),
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    except Exception as e:
        logging.error("record_fallback_miss write error: %s", e)


def is_fallback_miss(source: str, source_id: str) -> bool:
    """Checks whether a negative fallback cache entry is active."""
    try:
        if not source or not source_id:
            return False
        doc = id_fallback_misses_collection.find_one({"source": str(source), "source_id": str(source_id)})
        return doc is not None
    except Exception as e:
        logging.error("is_fallback_miss read error: %s", e)
        return False

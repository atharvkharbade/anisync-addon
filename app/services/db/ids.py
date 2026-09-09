import logging

from .connection import id_cache_collection


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
        doc = {
            "kitsu_id": int(kitsu_id) if kitsu_id else None,
            "mal_id": str(mal_id) if mal_id else None,
            "anilist_id": str(anilist_id) if anilist_id else None,
            "simkl_id": str(simkl_id) if simkl_id else None,
            "imdb_id": str(imdb_id) if imdb_id else None,
            "tmdb_id": str(tmdb_id) if tmdb_id else None,
            "tvdb_id": str(tvdb_id) if tvdb_id else None,
        }
        # Filter out None kitsu_id
        if doc["kitsu_id"] is None:
            return
        existing = id_cache_collection.find_one({"kitsu_id": doc["kitsu_id"]})
        if existing:
            update_doc = {}
            for k, v in doc.items():
                if v is not None:
                    update_doc[k] = v
            if update_doc:
                id_cache_collection.update_one({"kitsu_id": doc["kitsu_id"]}, {"$set": update_doc})
        else:
            id_cache_collection.insert_one(doc)
    except Exception as e:
        logging.error("Cache write error: %s", e)

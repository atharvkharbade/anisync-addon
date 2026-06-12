import logging
from datetime import datetime, timedelta

from pymongo import MongoClient
from pymongo.synchronous.collection import Collection
from pymongo.synchronous.database import Database

from config import Config

client: MongoClient = MongoClient(
    Config.MONGO_URI,
    maxPoolSize=100,
    minPoolSize=10,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=5000,
    retryWrites=True,
    retryReads=True,
)
db: Database = client.get_database(Config.MONGO_DB)

# Ensure database indexes on initialization
try:
    db.get_collection("users").create_index("uid", unique=True)
    db.get_collection("users").create_index("mal_id")
    db.get_collection("users").create_index("anilist_id")
    db.get_collection("users").create_index("simkl_id")

    db.get_collection("rate_limits").create_index([("ip", 1), ("route", 1), ("timestamp", -1)])
    db.get_collection("rate_limits").create_index("timestamp", expireAfterSeconds=60)
    db.get_collection("sessions").create_index("expiry", expireAfterSeconds=0)
    db.get_collection("fribb_mappings").create_index("kitsu_id")
    db.get_collection("fribb_mappings").create_index("mal_id")
    db.get_collection("fribb_mappings").create_index("anilist_id")
    db.get_collection("jikan_cache").create_index([("mal_id", 1), ("episode", 1)])

    # id_cache indexes
    db.get_collection("id_cache").create_index("kitsu_id")
    db.get_collection("id_cache").create_index("mal_id")
    db.get_collection("id_cache").create_index("anilist_id")
    db.get_collection("id_cache").create_index("simkl_id")

    # Caching collections indexes
    db.get_collection("user_watchlist_cache").create_index([("uid", 1), ("tracker", 1), ("status", 1)])
    db.get_collection("user_watchlist_cache").create_index("expires_at", expireAfterSeconds=0)

    db.get_collection("anilist_airing_cache").create_index("anilist_id")
    db.get_collection("anilist_airing_cache").create_index("expires_at", expireAfterSeconds=0)

    db.get_collection("kitsu_search_cache").create_index([("query", 1), ("offset", 1)])
    db.get_collection("kitsu_search_cache").create_index("expires_at", expireAfterSeconds=0)
except Exception as e:
    logging.error("Failed to initialize database indexes: %s", e)

users_collection: Collection = db.get_collection("users")
id_cache_collection: Collection = db.get_collection("id_cache")
jikan_cache_collection: Collection = db.get_collection("jikan_cache")


# ── Jikan episode filler cache ────────────────────────────────────────────────

JIKAN_CACHE_TTL_HOURS = 168


def get_jikan_filler_cache(mal_id: str, episode: int) -> bool | None:
    """Return cached filler status for an episode, or None if not cached / expired."""
    try:
        doc = jikan_cache_collection.find_one({"mal_id": str(mal_id), "episode": int(episode)})
        if doc:
            age = datetime.utcnow() - doc.get("cached_at", datetime.min)
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
                    "cached_at": datetime.utcnow(),
                }
            },
            upsert=True,
        )
    except Exception as e:
        logging.error("Jikan cache write error: %s", e)


# ── User helpers ──────────────────────────────────────────────────────────────


def get_user(user_id: str) -> dict | None:
    if not user_id:
        return None
    # 1. Try exact match first
    user = users_collection.find_one({"uid": user_id})
    if user:
        return user

    # 2. Support stripping prefixes (e.g., al_6613976 -> 6613976)
    if user_id.startswith("al_"):
        stripped = user_id[3:]
        user = users_collection.find_one({"uid": stripped})
        if user:
            return user
    elif user_id.startswith("simkl_"):
        stripped = user_id[6:]
        user = users_collection.find_one({"uid": stripped})
        if user:
            return user

    # 3. Support adding prefixes (e.g., 6613976 -> al_6613976)
    if user_id.isdigit():
        for prefix in ["al_", "simkl_"]:
            user = users_collection.find_one({"uid": f"{prefix}{user_id}"})
            if user:
                return user

    return None


def find_user_by_mal_id(mal_id: str) -> dict | None:
    return users_collection.find_one({"$or": [{"uid": str(mal_id)}, {"mal_id": str(mal_id)}]})


def find_user_by_anilist_id(anilist_id: str) -> dict | None:
    return users_collection.find_one(
        {"$or": [{"uid": f"al_{anilist_id}"}, {"uid": str(anilist_id)}, {"anilist_id": str(anilist_id)}]}
    )


def find_user_by_simkl_id(simkl_id: str) -> dict | None:
    return users_collection.find_one(
        {"$or": [{"uid": f"simkl_{simkl_id}"}, {"uid": str(simkl_id)}, {"simkl_id": str(simkl_id)}]}
    )


def store_user(user_details: dict) -> bool:
    uid = user_details.get("uid") or user_details.get("id")
    if not uid:
        return False
    user_details["uid"] = str(uid)
    existing = users_collection.find_one({"uid": str(uid)})
    if existing:
        return users_collection.replace_one({"uid": str(uid)}, user_details).acknowledged
    return users_collection.insert_one(user_details).acknowledged


def get_valid_mal_user(user_id: str) -> tuple[dict, str | None]:
    user = get_user(user_id)
    if not user:
        return {}, "User not found."
    if not user.get("mal_enabled"):
        return user, None  # not an error — MAL just disabled
    if not user.get("mal_access_token") or not user.get("mal_refresh_token"):
        return {}, "MAL not connected. Please log in."
    expiry = user.get("mal_expires_at")
    if expiry and datetime.utcnow() > expiry:
        return {}, "MAL session expired. Please refresh."
    return user, None


# ── ARM ID cache ──────────────────────────────────────────────────────────────


def get_cached_ids(kitsu_id: str) -> dict | None:
    try:
        return id_cache_collection.find_one({"kitsu_id": int(kitsu_id)})
    except (ValueError, TypeError):
        return None


def get_cached_ids_by_mal(mal_id: str) -> dict | None:
    try:
        return id_cache_collection.find_one({"mal_id": str(mal_id)})
    except (ValueError, TypeError):
        return None


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


def invalidate_user_watchlist_cache(user_id: str):
    """Delete all cached watchlist documents for a specific user."""
    try:
        db.get_collection("user_watchlist_cache").delete_many({"uid": str(user_id)})
        logging.info("Invalidated watchlist cache for user %s", user_id)
    except Exception as e:
        logging.error("Failed to invalidate watchlist cache for user %s: %s", user_id, e)


def get_user_watch_progress(
    user_id: str, mal_id: str | None = None, anilist_id: str | None = None, simkl_id: str | None = None
) -> int:
    """Find the user's maximum watch progress (watched episode count) across cached watchlists."""
    if not user_id:
        return 0

    max_progress = 0
    try:
        cache_col = db.get_collection("user_watchlist_cache")
        docs = list(cache_col.find({"uid": str(user_id)}))

        mal_str = str(mal_id) if mal_id else None
        anilist_str = str(anilist_id) if anilist_id else None
        simkl_str = str(simkl_id) if simkl_id else None

        for doc in docs:
            tracker = doc.get("tracker")
            data = doc.get("data")
            if not data:
                continue

            if tracker == "mal":
                for item in data:
                    node = item.get("node") or {}
                    nid = str(node.get("id") or "")
                    if nid and mal_str and nid == mal_str:
                        status_obj = node.get("my_list_status") or {}
                        max_progress = max(max_progress, status_obj.get("num_episodes_watched") or 0)

            elif tracker == "anilist":
                lists = data.get("lists") or []
                for lst in lists:
                    entries = lst.get("entries") or []
                    for entry in entries:
                        media = entry.get("media") or {}
                        al_id = str(media.get("id") or "")
                        al_mal_id = str(media.get("idMal") or "")
                        if (anilist_str and al_id == anilist_str) or (mal_str and al_mal_id == mal_str):
                            max_progress = max(max_progress, entry.get("progress") or 0)

            elif tracker == "simkl":
                for item in data:
                    show_obj = item.get("show") or item.get("anime") or item
                    ids = show_obj.get("ids") or {}
                    s_mal = str(ids.get("mal") or "")
                    s_al = str(ids.get("anilist") or "")
                    s_simkl = str(ids.get("simkl") or "")

                    match = False
                    if mal_str and s_mal and s_mal == mal_str:
                        match = True
                    elif anilist_str and s_al and s_al == anilist_str:
                        match = True
                    elif simkl_str and s_simkl and s_simkl == simkl_str:
                        match = True

                    if match:
                        prog = (
                            item.get("watched_episodes_count")
                            or item.get("episodes_watched")
                            or item.get("progress")
                            or 0
                        )
                        max_progress = max(max_progress, prog)

    except Exception as e:
        logging.error("Failed to query watch progress for user %s: %s", user_id, e)

    return max_progress

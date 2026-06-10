import logging
from datetime import datetime, timedelta
from typing import Optional

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
    retryReads=True
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

def get_jikan_filler_cache(mal_id: str, episode: int) -> Optional[bool]:
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
            {"$set": {"mal_id": str(mal_id), "episode": int(episode), "filler": filler, "cached_at": datetime.utcnow()}},
            upsert=True,
        )
    except Exception as e:
        logging.error("Jikan cache write error: %s", e)


# ── User helpers ──────────────────────────────────────────────────────────────

def get_user(user_id: str) -> Optional[dict]:
    return users_collection.find_one({"uid": user_id})


def find_user_by_mal_id(mal_id: str) -> Optional[dict]:
    return users_collection.find_one({"$or": [{"uid": str(mal_id)}, {"mal_id": str(mal_id)}]})


def find_user_by_anilist_id(anilist_id: str) -> Optional[dict]:
    return users_collection.find_one({"$or": [{"uid": f"al_{anilist_id}"}, {"anilist_id": str(anilist_id)}]})


def find_user_by_simkl_id(simkl_id: str) -> Optional[dict]:
    return users_collection.find_one({"$or": [{"uid": f"simkl_{simkl_id}"}, {"simkl_id": str(simkl_id)}]})


def store_user(user_details: dict) -> bool:
    uid = user_details.get("uid") or user_details.get("id")
    if not uid:
        return False
    user_details["uid"] = str(uid)
    existing = users_collection.find_one({"uid": str(uid)})
    if existing:
        return users_collection.replace_one(
            {"uid": str(uid)}, user_details
        ).acknowledged
    return users_collection.insert_one(user_details).acknowledged


def get_valid_mal_user(user_id: str) -> tuple[dict, Optional[str]]:
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

def get_cached_ids(kitsu_id: str) -> Optional[dict]:
    try:
        return id_cache_collection.find_one({"kitsu_id": int(kitsu_id)})
    except (ValueError, TypeError):
        return None


def get_cached_ids_by_mal(mal_id: str) -> Optional[dict]:
    try:
        return id_cache_collection.find_one({"mal_id": str(mal_id)})
    except (ValueError, TypeError):
        return None


def get_cached_ids_by_anilist(anilist_id: str) -> Optional[dict]:
    try:
        return id_cache_collection.find_one({"anilist_id": str(anilist_id)})
    except (ValueError, TypeError):
        return None


def get_cached_ids_by_simkl(simkl_id: str) -> Optional[dict]:
    try:
        query = {"$or": [{"simkl_id": str(simkl_id)}]}
        if str(simkl_id).isdigit():
            query["$or"].append({"simkl_id": int(simkl_id)})
            query["$or"].append({"simkl": int(simkl_id)})
        return id_cache_collection.find_one(query)
    except Exception:
        return None


def cache_ids(kitsu_id: str, mal_id: Optional[str], anilist_id: Optional[str], simkl_id: Optional[str] = None):
    try:
        doc = {
            "kitsu_id": int(kitsu_id) if kitsu_id else None,
            "mal_id": str(mal_id) if mal_id else None,
            "anilist_id": str(anilist_id) if anilist_id else None,
            "simkl_id": str(simkl_id) if simkl_id else None,
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



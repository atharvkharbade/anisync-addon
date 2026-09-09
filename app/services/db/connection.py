import logging

from pymongo import MongoClient
from pymongo.synchronous.collection import Collection
from pymongo.synchronous.database import Database

from config import Config

client: MongoClient = MongoClient(
    Config.MONGO_URI,
    maxPoolSize=25,
    minPoolSize=5,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=5000,
    retryWrites=True,
    retryReads=True,
)
db: Database = client.get_database(Config.MONGO_DB)


def init_indexes():
    """Ensure database indexes on initialization."""
    try:
        db.get_collection("users").create_index("uid", unique=True)
        db.get_collection("users").create_index("mal_id")
        db.get_collection("users").create_index("anilist_id")
        db.get_collection("users").create_index("simkl_id")
        db.get_collection("users").create_index(
            "created_at",
            expireAfterSeconds=30 * 86400,
            partialFilterExpression={"is_guest": True},
        )

        db.get_collection("rate_limits").create_index([("ip", 1), ("route", 1), ("timestamp", -1)])
        db.get_collection("rate_limits").create_index("timestamp", expireAfterSeconds=60)
        db.get_collection("sessions").create_index("expiry", expireAfterSeconds=0)
        db.get_collection("fribb_mappings").create_index("kitsu_id")
        db.get_collection("fribb_mappings").create_index("mal_id")
        db.get_collection("fribb_mappings").create_index("anilist_id")
        db.get_collection("jikan_cache").create_index([("mal_id", 1), ("episode", 1)])
        db.get_collection("jikan_cache").create_index("cached_at", expireAfterSeconds=168 * 3600)

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

        db.get_collection("kitsu_meta_cache").create_index("kitsu_id", unique=True)
        db.get_collection("kitsu_meta_cache").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("anizp_meta_cache").create_index("key", unique=True)
        db.get_collection("anizp_meta_cache").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("cinemeta_meta_cache").create_index([("imdb_id", 1), ("media_type", 1)], unique=True)
        db.get_collection("cinemeta_meta_cache").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("anilist_meta_cache").create_index("anilist_id", unique=True)
        db.get_collection("anilist_meta_cache").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("discovery_catalogs_cache").create_index("catalog_id", unique=True)

        db.get_collection("recommendations_cache").create_index("uid", unique=True)
        db.get_collection("recommendations_cache").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("banner_ratios").create_index("url", unique=True)
    except Exception as e:
        logging.error("Failed to initialize database indexes: %s", e)


init_indexes()

users_collection: Collection = db.get_collection("users")
id_cache_collection: Collection = db.get_collection("id_cache")
jikan_cache_collection: Collection = db.get_collection("jikan_cache")

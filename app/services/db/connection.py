import logging
from datetime import UTC, datetime, timedelta

from pymongo import MongoClient
from pymongo.errors import OperationFailure
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


PURGE_RETENTION_DAYS = 30
META_TTL_MIGRATION_COLLECTIONS = (
    "tvdb_meta_cache",
    "fanart_meta_cache",
    "cinemeta_meta_cache",
    "kitsu_meta_cache",
    "tvdb_translation_cache",
    "tvdb_posters_cache",
    "tvdb_episodes_cache",
)


def _migrate_collection_to_purge_at(col: Collection, now: datetime) -> None:
    """Idempotently migrate a cache collection from expires_at TTL to purge_at TTL."""
    expires_idx = col.index_information().get("expires_at_1")
    if isinstance(expires_idx, dict) and "expireAfterSeconds" in expires_idx:
        try:
            col.drop_index("expires_at_1")
        except OperationFailure:
            pass

    col.update_many(
        {"purge_at": {"$exists": False}, "expires_at": {"$type": "date"}},
        [
            {
                "$set": {
                    "purge_at": {
                        "$dateAdd": {
                            "startDate": "$expires_at",
                            "unit": "day",
                            "amount": PURGE_RETENTION_DAYS,
                        }
                    }
                }
            }
        ],
    )
    col.update_many(
        {"purge_at": {"$exists": False}},
        {"$set": {"purge_at": now + timedelta(days=PURGE_RETENTION_DAYS)}},
    )
    col.create_index("purge_at", expireAfterSeconds=0)


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

        # id_cache indexes and legacy expiration backfill
        db.get_collection("id_cache").create_index("kitsu_id")
        db.get_collection("id_cache").create_index("mal_id")
        db.get_collection("id_cache").create_index("anilist_id")
        db.get_collection("id_cache").create_index("simkl_id")
        db.get_collection("id_cache").create_index("expires_at", expireAfterSeconds=0)

        # id_fallback_misses collection to prevent upstream rate-limit hammer on failed fallbacks
        db.get_collection("id_fallback_misses").create_index("expires_at", expireAfterSeconds=0)
        db.get_collection("id_fallback_misses").create_index([("source", 1), ("source_id", 1)])

        now = datetime.now(UTC).replace(tzinfo=None)
        db.get_collection("id_cache").update_many(
            {"expires_at": {"$exists": False}},
            {"$set": {"expires_at": now + timedelta(days=7), "updated_at": now}},
        )

        # Caching collections indexes
        db.get_collection("user_watchlist_cache").create_index([("uid", 1), ("tracker", 1), ("status", 1)])
        db.get_collection("user_watchlist_cache").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("user_anime_status_cache").create_index([("uid", 1), ("mal_id", 1), ("anilist_id", 1), ("simkl_id", 1)])
        db.get_collection("user_anime_status_cache").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("anilist_airing_cache").create_index("anilist_id")
        db.get_collection("anilist_airing_cache").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("kitsu_search_cache").create_index([("query", 1), ("offset", 1)])
        db.get_collection("kitsu_search_cache").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("kitsu_meta_cache").create_index("kitsu_id", unique=True)

        db.get_collection("anizp_meta_cache").create_index("key", unique=True)
        db.get_collection("anizp_meta_cache").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("cinemeta_meta_cache").create_index([("imdb_id", 1), ("media_type", 1)], unique=True)

        db.get_collection("fanart_meta_cache").create_index([("kind", 1), ("ext_id", 1)], unique=True)

        db.get_collection("tvdb_meta_cache").create_index([("kind", 1), ("ext_id", 1)], unique=True)

        db.get_collection("tvdb_translation_cache").create_index([("kind", 1), ("ext_id", 1), ("lang", 1)], unique=True)
        db.get_collection("tvdb_posters_cache").create_index([("kind", 1), ("ext_id", 1)], unique=True)
        db.get_collection("tvdb_episodes_cache").create_index([("tvdb_id", 1), ("lang", 1)], unique=True)

        for col_name in META_TTL_MIGRATION_COLLECTIONS:
            _migrate_collection_to_purge_at(db.get_collection(col_name), now)

        db.get_collection("tvdb_auth").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("tvdb_remote_ids").create_index("imdb_id", unique=True)
        db.get_collection("tvdb_remote_ids").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("anilist_meta_cache").create_index("anilist_id", unique=True)
        db.get_collection("anilist_meta_cache").create_index("expires_at", expireAfterSeconds=0)

        db.get_collection("discovery_catalogs_cache").create_index("catalog_id", unique=True)

        db.get_collection("recommendations_cache").create_index("uid", unique=True)
        db.get_collection("recommendations_cache").create_index("expires_at", expireAfterSeconds=0)

        # Migrate legacy 'fanart' backdrop provider to 'cinemeta'
        db.get_collection("users").update_many(
            {"meta_backdrop_provider": "fanart"},
            {"$set": {"meta_backdrop_provider": "cinemeta"}},
        )

        db.get_collection("banner_ratios").create_index("url", unique=True)
    except Exception as e:
        logging.error("Failed to initialize database indexes: %s", e)


init_indexes()

users_collection: Collection = db.get_collection("users")
id_cache_collection: Collection = db.get_collection("id_cache")
jikan_cache_collection: Collection = db.get_collection("jikan_cache")
id_fallback_misses_collection: Collection = db.get_collection("id_fallback_misses")
fanart_meta_cache_collection: Collection = db.get_collection("fanart_meta_cache")
tvdb_meta_cache_collection: Collection = db.get_collection("tvdb_meta_cache")
tvdb_translation_cache_collection: Collection = db.get_collection("tvdb_translation_cache")
tvdb_posters_cache_collection: Collection = db.get_collection("tvdb_posters_cache")
tvdb_episodes_cache_collection: Collection = db.get_collection("tvdb_episodes_cache")
tvdb_auth_collection: Collection = db.get_collection("tvdb_auth")
tvdb_remote_ids_collection: Collection = db.get_collection("tvdb_remote_ids")

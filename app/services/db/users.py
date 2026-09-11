import logging
import secrets
from datetime import UTC, datetime

from .connection import users_collection


def get_user(user_id: str, for_manifest: bool = False) -> dict | None:
    if not user_id:
        return None

    # 1. Try secure manifest_token match first (O(1) index lookup)
    user = users_collection.find_one({"manifest_token": user_id})
    if user:
        return user

    # 2. Try exact match on uid for legacy users
    user = users_collection.find_one({"uid": user_id})
    if user:
        if for_manifest and user.get("manifest_token") and not user.get("allow_legacy_uid", False):
            return None
        return user

    # 3. Support stripping prefixes (e.g., al_6613976 -> 6613976)
    if user_id.startswith("al_"):
        stripped = user_id[3:]
        user = users_collection.find_one({"uid": stripped})
        if user:
            if for_manifest and user.get("manifest_token") and not user.get("allow_legacy_uid", False):
                return None
            return user
    elif user_id.startswith("simkl_"):
        stripped = user_id[6:]
        user = users_collection.find_one({"uid": stripped})
        if user:
            if for_manifest and user.get("manifest_token") and not user.get("allow_legacy_uid", False):
                return None
            return user

    # 4. Support adding prefixes (e.g., 6613976 -> al_6613976)
    if user_id.isdigit():
        for prefix in ["al_", "simkl_"]:
            user = users_collection.find_one({"uid": f"{prefix}{user_id}"})
            if user:
                if for_manifest and user.get("manifest_token") and not user.get("allow_legacy_uid", False):
                    return None
                return user

    # 5. Support guest user auto-provisioning
    if user_id.startswith("guest_"):
        guest_user = {
            "uid": user_id,
            "manifest_token": secrets.token_urlsafe(16),
            "allow_legacy_uid": False,
            "username": "Guest User",
            "is_guest": True,
            "enable_discovery_catalogs": True,
            "enable_catalogs": False,
            "enable_recommendations": False,
            "enable_search": True,
            "hide_nsfw": True,
            "title_language": "english",
            "metadata_provider": "kitsu",
            "meta_synopsis_provider": "kitsu",
            "meta_episodes_provider": "anizp",
            "meta_poster_provider": "anilist",
            "meta_backdrop_provider": "fanart",
            "meta_airing_provider": "anilist",
            "catalogs": [
                "anisync_search",
                "anisync_spotlight",
                "anisync_schedule",
                "anisync_seasonal",
                "anisync_top_airing",
                "anisync_highest_rated",
                "anisync_most_popular",
            ],
            "created_at": datetime.now(UTC).replace(tzinfo=None),
        }
        try:
            users_collection.update_one({"uid": user_id}, {"$setOnInsert": guest_user}, upsert=True)
            return users_collection.find_one({"uid": user_id}) or guest_user
        except Exception as e:
            logging.error("Failed to auto-provision guest user %s: %s", user_id, e)
            return guest_user

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
        if "_id" in existing and "_id" not in user_details:
            user_details["_id"] = existing["_id"]
        if "manifest_token" in existing and "manifest_token" not in user_details:
            user_details["manifest_token"] = existing["manifest_token"]
        if "allow_legacy_uid" in existing and "allow_legacy_uid" not in user_details:
            user_details["allow_legacy_uid"] = existing["allow_legacy_uid"]
        return users_collection.replace_one({"uid": str(uid)}, user_details).acknowledged

    if "manifest_token" not in user_details:
        user_details["manifest_token"] = secrets.token_urlsafe(16)
        user_details["allow_legacy_uid"] = False
    return users_collection.insert_one(user_details).acknowledged

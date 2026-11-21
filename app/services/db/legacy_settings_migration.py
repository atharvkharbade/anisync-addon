import copy
import logging
from datetime import UTC, datetime, timedelta

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.routes.catalog.sorting.preferences import get_allowed_sorts_for_catalog
from app.services.db import db

MIGRATION_ID = "v1_6_legacy_settings"

POSSIBLE_CATS = [
    "anisync_search",
    "mal_watching",
    "mal_plan_to_watch",
    "mal_completed",
    "mal_on_hold",
    "mal_dropped",
    "anilist_watching",
    "anilist_planning",
    "anilist_completed",
    "anilist_paused",
    "anilist_dropped",
    "anilist_repeating",
    "simkl_watching",
    "simkl_plantowatch",
    "simkl_completed",
    "simkl_hold",
    "simkl_dropped",
    "comb_watching",
    "comb_plan_to_watch",
    "comb_completed",
    "comb_paused_on_hold",
    "comb_dropped",
    "anisync_rec",
    "anisync_loved",
    "anisync_liked",
    "anisync_spotlight",
    "anisync_schedule",
    "anisync_seasonal",
    "anisync_trending",
    "anisync_top_airing",
    "anisync_highest_rated",
    "anisync_most_popular",
]

# Status suffix -> custom_sort category key, mirroring the watchlist handlers'
# mal_map / anilist status mapping / simkl_map / comb_map (unknown statuses fall back to "watching").
_STATUS_TO_CATEGORY = {
    "watching": "watching",
    "repeating": "watching",
    "plan_to_watch": "planning",
    "planning": "planning",
    "plantowatch": "planning",
    "completed": "completed",
    "on_hold": "on_hold",
    "paused": "on_hold",
    "hold": "on_hold",
    "paused_on_hold": "on_hold",
    "dropped": "dropped",
}
_NO_CATEGORY = ("anisync_rec", "anisync_loved", "anisync_liked")


def sort_category_for(cat: str) -> str | None:
    """The default_category_key get_catalog_sorting receives for this catalog today.
    Inherited quirk: discovery catalogs and search pass "watching", so they pick up the watching sort."""
    if cat in _NO_CATEGORY:
        return None
    if cat.startswith("anisync_"):
        return "watching"
    for prefix in ("mal_", "anilist_", "simkl_", "comb_"):
        if cat.startswith(prefix):
            return _STATUS_TO_CATEGORY.get(cat[len(prefix):], "watching")
    return None


def _has_value(cfg: dict, key: str) -> bool:
    """True when catalog_configs already decides this key (readers treat empty / "default" as unset)."""
    val = cfg.get(key)
    if key == "sort_by":
        return bool(val) and val != "default"
    if key in ("title", "placement"):
        return bool(val)
    return key in cfg


def migrate_user_doc(user: dict) -> tuple[dict, dict]:
    """
    Computes ($set, $unset) dicts to migrate a single user document to modern catalog_configs.
    Pure helper function for easier unit testing.
    """
    set_fields = {}
    unset_fields = {}

    raw_cfgs = user.get("catalog_configs")
    cfgs = copy.deepcopy(raw_cfgs) if isinstance(raw_cfgs, dict) else {}
    cfgs_modified = False

    # 1. C: catalog_shapes
    if "catalog_shapes" in user:
        unset_fields["catalog_shapes"] = ""
        shapes = user.get("catalog_shapes")
        if isinstance(shapes, dict):
            for cat, val in shapes.items():
                if not isinstance(cat, str):
                    continue
                c_cfg = cfgs.setdefault(cat, {})
                if val == "landscape":
                    if c_cfg.get("shape") != "landscape":
                        c_cfg["shape"] = "landscape"
                        cfgs_modified = True
                elif val == "poster":
                    if "shape" not in c_cfg:
                        c_cfg["shape"] = "poster"
                        cfgs_modified = True

    # 2. C: catalog_titles
    if "catalog_titles" in user:
        unset_fields["catalog_titles"] = ""
        titles = user.get("catalog_titles")
        if isinstance(titles, dict):
            for cat, val in titles.items():
                if not isinstance(cat, str):
                    continue
                if isinstance(val, str) and val.strip():
                    c_cfg = cfgs.setdefault(cat, {})
                    if not _has_value(c_cfg, "title"):
                        c_cfg["title"] = val.strip()
                        cfgs_modified = True

    # 3. C: catalog_placements
    if "catalog_placements" in user:
        unset_fields["catalog_placements"] = ""
        placements = user.get("catalog_placements")
        if isinstance(placements, dict):
            for cat, val in placements.items():
                if not isinstance(cat, str):
                    continue
                if val in ("all", "discover_only"):
                    c_cfg = cfgs.setdefault(cat, {})
                    if not _has_value(c_cfg, "placement"):
                        c_cfg["placement"] = val
                        cfgs_modified = True

    # 4. C: catalog_shuffles
    if "catalog_shuffles" in user:
        unset_fields["catalog_shuffles"] = ""
        shuffles = user.get("catalog_shuffles")
        if isinstance(shuffles, dict):
            for cat, val in shuffles.items():
                if not isinstance(cat, str):
                    continue
                if val is not None:
                    c_cfg = cfgs.setdefault(cat, {})
                    if "shuffle" not in c_cfg:
                        c_cfg["shuffle"] = bool(val)
                        cfgs_modified = True

    # 5. C: catalog_sorts
    if "catalog_sorts" in user:
        unset_fields["catalog_sorts"] = ""
        sorts = user.get("catalog_sorts")
        if isinstance(sorts, dict):
            for cat, val in sorts.items():
                if not isinstance(cat, str):
                    continue
                if isinstance(val, dict):
                    by = val.get("by")
                    order = val.get("order", "desc")
                    if by and by != "default":
                        c_cfg = cfgs.setdefault(cat, {})
                        if not _has_value(c_cfg, "sort_by"):
                            c_cfg["sort_by"] = by
                            c_cfg["sort_order"] = order
                            cfgs_modified = True

    # 6. C: catalog_poster_arts
    if "catalog_poster_arts" in user:
        unset_fields["catalog_poster_arts"] = ""
        arts = user.get("catalog_poster_arts")
        if isinstance(arts, dict):
            for cat, val in arts.items():
                if not isinstance(cat, str):
                    continue
                if val is None:
                    continue
                # formatting.py reads catalog_poster_arts before catalog_configs, so the old value decides today
                c_cfg = cfgs.setdefault(cat, {})
                if c_cfg.get("poster_art", object()) != val:
                    c_cfg["poster_art"] = val
                    cfgs_modified = True

    # 7. D1: shuffle_discovery_catalogs
    if "shuffle_discovery_catalogs" in user:
        unset_fields["shuffle_discovery_catalogs"] = ""
        if user.get("shuffle_discovery_catalogs") is True:
            for cat in POSSIBLE_CATS:
                if cat.startswith("anisync_") and cat != "anisync_schedule":
                    c_cfg = cfgs.setdefault(cat, {})
                    if "shuffle" not in c_cfg:
                        c_cfg["shuffle"] = True
                        cfgs_modified = True

    # 8. D2: dubbed_only_discovery
    if "dubbed_only_discovery" in user:
        unset_fields["dubbed_only_discovery"] = ""

    # 9. D3: custom_sort_enabled and custom_sort_*
    for k in ("watching", "planning", "completed", "on_hold", "dropped"):
        for suffix in ("by", "order"):
            field_name = f"custom_sort_{k}_{suffix}"
            if field_name in user:
                unset_fields[field_name] = ""
    if "custom_sort_enabled" in user:
        unset_fields["custom_sort_enabled"] = ""
        if user.get("custom_sort_enabled") is True:
            for cat in POSSIBLE_CATS:
                cat_key = sort_category_for(cat)
                if not cat_key:
                    continue
                sort_by = user.get(f"custom_sort_{cat_key}_by", "default")
                sort_order = user.get(f"custom_sort_{cat_key}_order", "desc")
                if not sort_by or sort_by == "default" or sort_by not in get_allowed_sorts_for_catalog(cat):
                    continue
                c_cfg = cfgs.setdefault(cat, {})
                if c_cfg.get("shuffle") is True or _has_value(c_cfg, "sort_by"):
                    continue
                c_cfg["sort_by"] = sort_by
                c_cfg["sort_order"] = sort_order
                cfgs_modified = True

    # 10. E1: meta_artwork_provider
    if "meta_artwork_provider" in user:
        unset_fields["meta_artwork_provider"] = ""
        if user.get("meta_artwork_provider") and not user.get("meta_poster_provider"):
            set_fields["meta_poster_provider"] = user.get("meta_artwork_provider")

    # 11. E2: meta_backdrop_provider
    cur_backdrop = (user.get("meta_backdrop_provider") or "").strip().lower()
    if cur_backdrop == "anilist":
        set_fields["meta_backdrop_provider"] = "tvdb"
    elif cur_backdrop == "fanart":
        set_fields["meta_backdrop_provider"] = "cinemeta"

    # 12. F1: Implicit RPDB
    if not user.get("poster_provider") and user.get("rpdb_api_key"):
        set_fields["poster_provider"] = "rpdb"

    # 13. F2: topposters safety
    if user.get("poster_provider") == "topposters":
        set_fields["poster_provider"] = "top_poster"

    for cat, c_data in cfgs.items():
        if isinstance(c_data, dict) and c_data.get("poster_provider") == "topposters":
            c_data["poster_provider"] = "top_poster"
            cfgs_modified = True

    if cfgs_modified or (raw_cfgs is None and cfgs):
        set_fields["catalog_configs"] = cfgs

    return set_fields, unset_fields


def run_legacy_settings_migration():
    """
    Executes one-time database settings migration v1_6_legacy_settings.
    Converts legacy dictionaries and old global settings into modern catalog_configs.
    """
    mig_col = db.get_collection("migrations")
    try:
        mig_col.create_index("id", unique=True)
    except Exception as e:
        logging.warning("Could not ensure unique index on migrations: %s", e)

    doc = mig_col.find_one({"id": MIGRATION_ID})
    if doc and doc.get("status") == "done":
        logging.info("Migration %s already completed, skipping.", MIGRATION_ID)
        return

    now = datetime.now(UTC).replace(tzinfo=None)
    stale_threshold = now - timedelta(minutes=30)

    try:
        mig_col.insert_one({"id": MIGRATION_ID, "status": "running", "started_at": now})
    except DuplicateKeyError:
        claim = mig_col.find_one_and_update(
            {
                "id": MIGRATION_ID,
                "$or": [
                    {"status": "partial"},
                    {"status": "running", "started_at": {"$lt": stale_threshold}},
                ],
            },
            {"$set": {"status": "running", "started_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if not claim:
            logging.info("Migration %s is currently running or already completed elsewhere.", MIGRATION_ID)
            return

    logging.info("Starting legacy settings migration %s...", MIGRATION_ID)
    total_scanned = 0
    migrated_count = 0
    error_count = 0

    try:
        users_col = db.get_collection("users")
        cursor = users_col.find({}, no_cursor_timeout=True)
        try:
            for user in cursor:
                total_scanned += 1
                uid = user.get("uid")
                if not uid:
                    continue
                try:
                    set_fields, unset_fields = migrate_user_doc(user)
                    if set_fields or unset_fields:
                        update_op = {}
                        if set_fields:
                            update_op["$set"] = set_fields
                        if unset_fields:
                            update_op["$unset"] = unset_fields
                        users_col.update_one({"uid": uid}, update_op)
                        migrated_count += 1
                except Exception as user_err:
                    error_count += 1
                    logging.error("Failed to migrate settings for user %s: %s", uid, user_err)
        finally:
            cursor.close()

        if error_count == 0:
            mig_col.update_one(
                {"id": MIGRATION_ID},
                {
                    "$set": {
                        "status": "done",
                        "applied_at": datetime.now(UTC).replace(tzinfo=None),
                        "total_scanned": total_scanned,
                        "migrated_count": migrated_count,
                    }
                },
            )
            logging.info(
                "Completed migration %s: scanned=%d, migrated=%d",
                MIGRATION_ID,
                total_scanned,
                migrated_count,
            )
        else:
            mig_col.update_one(
                {"id": MIGRATION_ID},
                {"$set": {"status": "partial", "error_count": error_count, "total_scanned": total_scanned,
                          "migrated_count": migrated_count}},
            )
            logging.error(
                "Migration %s finished with %d errors (scanned=%d, migrated=%d)",
                MIGRATION_ID,
                error_count,
                total_scanned,
                migrated_count,
            )
    except Exception as e:
        logging.error("Fatal error during legacy settings migration %s: %s", MIGRATION_ID, e)

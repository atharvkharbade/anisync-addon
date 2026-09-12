def is_catalog_shuffle_enabled(user, catalog_id):
    """
    Checks whether shuffle is enabled for this catalog.
    Checks per-catalog configuration (catalog_configs / catalog_shuffles) first, then falls back to legacy global discovery shuffle.
    """
    cat_cfg = (user.get("catalog_configs", {}) or {}).get(catalog_id, {})
    if isinstance(cat_cfg, dict) and "shuffle" in cat_cfg:
        return bool(cat_cfg["shuffle"])

    cat_shuffle = (user.get("catalog_shuffles") or {}).get(catalog_id)
    if cat_shuffle is not None:
        return bool(cat_shuffle)

    # Legacy fallback: only for discovery catalogs except schedule
    if user.get("shuffle_discovery_catalogs", False) and catalog_id.startswith("anisync_") and catalog_id != "anisync_schedule":
        return True
    return False


def is_catalog_dubbed_enabled(user, catalog_id: str) -> bool:
    """
    Checks whether dub filtering is enabled for this catalog.
    Checks per-catalog configuration (catalog_configs[catalog_id]['dubbed'] or audio == 'dubbed') first,
    then falls back to legacy global discovery dubbed toggle (user.dubbed_only_discovery).
    """
    if not user or not catalog_id:
        return False

    cat_cfg = (user.get("catalog_configs", {}) or {}).get(catalog_id, {})
    if isinstance(cat_cfg, dict):
        if "dubbed" in cat_cfg:
            return bool(cat_cfg["dubbed"])
        audio_val = cat_cfg.get("audio")
        if audio_val in ("dubbed", "dub"):
            return True
        if audio_val in ("all", "sub", "both"):
            return False

    # Legacy fallback: user.dubbed_only_discovery applies to general discovery catalogs
    if user.get("dubbed_only_discovery") and catalog_id.startswith("anisync_") and catalog_id not in ["anisync_search", "anisync_rec", "anisync_loved", "anisync_liked"]:
        return True

    return False


def get_allowed_sorts_for_catalog(catalog_id: str) -> list:
    """
    Returns the valid, supported sort fields for a specific catalog.
    Prevents meaningless sorts where 0% metadata exists (which otherwise silently
    falls back to alphabetical sorting due to tie-breaker rules).
    """
    if not catalog_id:
        return ["default", "score", "release_date", "episodes", "title"]

    # Search: Keep it simple and relevant (relevance default, score, release_date)
    if catalog_id == "anisync_search":
        return ["default", "score", "release_date"]

    # Recommendations: Algorithm-ranked; airing_date and last_updated are irrelevant / unavailable
    if catalog_id in ["anisync_rec", "anisync_loved", "anisync_liked"]:
        return ["default", "score", "release_date", "episodes", "title"]

    # Airing Discovery: Next airing episode is relevant; last_updated has 0% data on public catalogs
    if catalog_id in ["anisync_top_airing", "anisync_seasonal", "anisync_schedule"]:
        return ["default", "score", "release_date", "airing_date", "episodes", "title"]

    # Other Discovery catalogs (Trending, Highest Rated, Most Popular, Spotlight, etc.)
    # All are static/public catalogs with no user timestamps (0% last_updated) and mostly finished titles (no airing_date)
    if catalog_id.startswith("anisync_"):
        return ["default", "score", "release_date", "episodes", "title"]

    # Finished & Dropped User Watchlists:
    # last_updated is 100% available (user completion/drop date).
    # airing_date is 0% available because finished/dropped shows do not have upcoming airing episodes.
    if any(k in catalog_id for k in ["completed", "dropped"]):
        return ["default", "score", "release_date", "episodes", "title", "last_updated"]

    # Active User Watchlists (watching, plan_to_watch, planning, plantowatch, on_hold, paused, repeating):
    # All 7 sorts are fully valid and supported with rich user & tracker metadata.
    return ["default", "score", "release_date", "airing_date", "episodes", "title", "last_updated"]


def get_catalog_sorting(user, catalog_id, default_category_key=None, url_filters=None):
    """
    Resolves custom sort settings for a catalog.
    Checks URL parameters first (e.g. sort_by, sort, sort_order from Stremio/Nuvio client query),
    then per-catalog configuration (catalog_configs / catalog_sorts), then falls back to legacy category-wide sort.
    Returns: (is_custom, sort_by, sort_order)
    """
    allowed = get_allowed_sorts_for_catalog(catalog_id)

    if isinstance(url_filters, dict):
        url_sort = url_filters.get("sort_by") or url_filters.get("sort")
        if url_sort and url_sort != "default":
            if url_sort in allowed:
                url_order = url_filters.get("sort_order") or "desc"
                return True, url_sort, url_order
            return False, "default", "desc"

    # If shuffle is enabled for this catalog, saved custom sorting is suppressed
    if is_catalog_shuffle_enabled(user, catalog_id):
        return False, "default", "desc"

    cat_cfg = (user.get("catalog_configs", {}) or {}).get(catalog_id, {})
    if isinstance(cat_cfg, dict):
        cfg_sort = cat_cfg.get("sort_by")
        if cfg_sort and cfg_sort != "default":
            if cfg_sort in allowed:
                return True, cfg_sort, cat_cfg.get("sort_order", "desc")
            return False, "default", "desc"

    cat_sort = (user.get("catalog_sorts") or {}).get(catalog_id)
    if cat_sort and isinstance(cat_sort, dict) and cat_sort.get("by", "default") != "default":
        sort_by = cat_sort.get("by", "default")
        if sort_by in allowed:
            return True, sort_by, cat_sort.get("order", "desc")
        return False, "default", "desc"

    if user.get("custom_sort_enabled", False) and default_category_key:
        sort_by = user.get(f"custom_sort_{default_category_key}_by", "default")
        sort_order = user.get(f"custom_sort_{default_category_key}_order", "desc")
        if sort_by != "default" and sort_by in allowed:
            return True, sort_by, sort_order

    return False, "default", "desc"


def resolve_title_lang(user: dict | None) -> str:
    """
    Extracts and normalizes the user's preferred title language setting.
    Defaults to 'english'.
    """
    if isinstance(user, dict):
        return (user.get("title_language") or "english").lower().strip()
    return "english"


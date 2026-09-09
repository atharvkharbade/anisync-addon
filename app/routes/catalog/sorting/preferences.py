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


def get_catalog_sorting(user, catalog_id, default_category_key=None, url_filters=None):
    """
    Resolves custom sort settings for a catalog.
    Checks URL parameters first (e.g. sort_by, sort, sort_order from Stremio/Nuvio client query),
    then per-catalog configuration (catalog_configs / catalog_sorts), then falls back to legacy category-wide sort.
    Returns: (is_custom, sort_by, sort_order)
    """
    if isinstance(url_filters, dict):
        url_sort = url_filters.get("sort_by") or url_filters.get("sort")
        if url_sort and url_sort != "default":
            url_order = url_filters.get("sort_order") or "desc"
            return True, url_sort, url_order

    # If shuffle is enabled for this catalog, saved custom sorting is suppressed
    if is_catalog_shuffle_enabled(user, catalog_id):
        return False, "default", "desc"

    cat_cfg = (user.get("catalog_configs", {}) or {}).get(catalog_id, {})
    if isinstance(cat_cfg, dict):
        cfg_sort = cat_cfg.get("sort_by")
        if cfg_sort and cfg_sort != "default":
            return True, cfg_sort, cat_cfg.get("sort_order", "desc")

    cat_sort = (user.get("catalog_sorts") or {}).get(catalog_id)
    if cat_sort and isinstance(cat_sort, dict) and cat_sort.get("by", "default") != "default":
        return True, cat_sort.get("by", "default"), cat_sort.get("order", "desc")

    if user.get("custom_sort_enabled", False) and default_category_key:
        sort_by = user.get(f"custom_sort_{default_category_key}_by", "default")
        sort_order = user.get(f"custom_sort_{default_category_key}_order", "desc")
        if sort_by != "default":
            return True, sort_by, sort_order

    return False, "default", "desc"

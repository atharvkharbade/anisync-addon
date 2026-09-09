CATALOGS = [
    {
        "type": "anime",
        "id": "mal_watching",
        "name": "MAL: Watching",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "mal_plan_to_watch",
        "name": "MAL: Plan to Watch",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "mal_completed",
        "name": "MAL: Completed",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "mal_on_hold",
        "name": "MAL: On Hold",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "mal_dropped",
        "name": "MAL: Dropped",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anilist_watching",
        "name": "AniList: Watching",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anilist_planning",
        "name": "AniList: Planning",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anilist_completed",
        "name": "AniList: Completed",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anilist_paused",
        "name": "AniList: Paused",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anilist_dropped",
        "name": "AniList: Dropped",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anilist_repeating",
        "name": "AniList: Repeating",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "simkl_watching",
        "name": "Simkl: Watching",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "simkl_plantowatch",
        "name": "Simkl: Plan to Watch",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "simkl_completed",
        "name": "Simkl: Completed",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "simkl_hold",
        "name": "Simkl: On Hold",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "simkl_dropped",
        "name": "Simkl: Dropped",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "comb_watching",
        "name": "Watching",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "comb_plan_to_watch",
        "name": "Plan to Watch",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "comb_completed",
        "name": "Completed",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "comb_paused_on_hold",
        "name": "On Hold",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "comb_dropped",
        "name": "Dropped",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anisync_rec",
        "name": "Top Picks for You",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anisync_loved",
        "name": "Inspired by your Favorites",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anisync_liked",
        "name": "More from your Watchlist",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anisync_spotlight",
        "name": "Spotlight",
        "extra": [
            {
                "name": "genre",
                "options": ["Feature Films", "New Movies", "OVAs & Specials", "Classic Masterpieces"],
                "isRequired": False,
            },
            {"name": "skip"},
        ],
    },
    {
        "type": "anime",
        "id": "anisync_schedule",
        "name": "Weekly Release Calendar",
        "extra": [
            {
                "name": "genre",
                "options": ["Airing Today", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                "isRequired": False,
            },
            {"name": "skip"},
        ],
    },
    {
        "type": "anime",
        "id": "anisync_seasonal",
        "name": "Seasonal Showcase",
        "extra": [
            {
                "name": "genre",
                "options": ["Current Season", "Next Season", "Upcoming", "Winter", "Spring", "Summer", "Fall"],
                "isRequired": False,
            },
            {"name": "skip"},
        ],
    },
    {
        "type": "anime",
        "id": "anisync_trending",
        "name": "Trending",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anisync_top_airing",
        "name": "Top Airing",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anisync_highest_rated",
        "name": "Highest Rated",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anisync_most_popular",
        "name": "Most Popular",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anisync_search",
        "name": "Search",
        "extra": [{"name": "search", "isRequired": True}, {"name": "skip"}],
    },
]

REC_CATALOG_IDS = ["anisync_rec", "anisync_loved", "anisync_liked"]

DISCOVERY_CATALOG_IDS = [
    "anisync_spotlight",
    "anisync_schedule",
    "anisync_seasonal",
    "anisync_trending",
    "anisync_top_airing",
    "anisync_highest_rated",
    "anisync_most_popular",
]


def get_configured_catalog(cat: dict, catalog_configs: dict, catalog_shapes: dict, catalog_titles: dict, catalog_placements: dict) -> dict:
    c = cat.copy()
    cat_id = c.get("id")
    cat_cfg = catalog_configs.get(cat_id, {}) if isinstance(catalog_configs, dict) else {}
    shape = cat_cfg.get("shape") or catalog_shapes.get(cat_id, "poster")
    if shape == "landscape":
        c["posterShape"] = "landscape"
    else:
        c["posterShape"] = "poster"

    # Custom title override
    title = cat_cfg.get("title") or catalog_titles.get(cat_id)
    if title and str(title).strip():
        c["name"] = str(title).strip()

    # Placement: "discover_only" hides row from Home board while keeping in Discover (Nuvio)
    placement = cat_cfg.get("placement") or catalog_placements.get(cat_id)
    if placement == "discover_only":
        c["showInHome"] = False

    return c


def filter_user_catalogs(user: dict) -> list[dict]:
    """Filter catalogs and apply shapes, custom titles, and placements based on active integrations and settings."""
    if user.get("is_guest"):
        enable_catalogs = False
        enable_recommendations = False
    else:
        enable_catalogs = user.get("enable_catalogs", True)
        enable_recommendations = user.get("enable_recommendations", True)

    enable_search = user.get("enable_search", True)
    enable_discovery_catalogs = user.get("enable_discovery_catalogs", True)

    if not enable_catalogs and not enable_search and not enable_recommendations and not enable_discovery_catalogs:
        return []

    mal_enabled = user.get("mal_access_token") and user.get("mal_enabled", True)
    anilist_enabled = user.get("anilist_token") and user.get("anilist_enabled", True)
    simkl_enabled = user.get("simkl_access_token") and user.get("simkl_enabled", True)
    combine_enabled = user.get("combine_watchlists", False)
    user_catalogs = user.get("catalogs")
    if user_catalogs is not None:
        user_catalogs = [c if c != "anime_tracker_search" else "anisync_search" for c in user_catalogs]

    catalog_shapes = user.get("catalog_shapes", {}) or {}
    catalog_configs = user.get("catalog_configs", {}) or {}
    catalog_titles = user.get("catalog_titles", {}) or {}
    catalog_placements = user.get("catalog_placements", {}) or {}

    def _configure(cat):
        return get_configured_catalog(cat, catalog_configs, catalog_shapes, catalog_titles, catalog_placements)

    active_catalogs = []

    # 1. Add custom sorted catalogs first (if user has saved preferences)
    if user_catalogs is not None:
        for cat_id in user_catalogs:
            for cat in CATALOGS:
                if cat["id"] == cat_id:
                    # Apply visibility checks
                    if cat_id == "anisync_search":
                        if not enable_search:
                            continue
                    elif cat_id in REC_CATALOG_IDS:
                        if not enable_recommendations:
                            continue
                    elif cat_id in DISCOVERY_CATALOG_IDS:
                        if not enable_discovery_catalogs:
                            continue
                    else:
                        if not enable_catalogs:
                            continue
                        if cat_id.startswith("mal_") and (combine_enabled or not mal_enabled):
                            continue
                        if cat_id.startswith("anilist_") and (combine_enabled or not anilist_enabled):
                            continue
                        if cat_id.startswith("simkl_") and (combine_enabled or not simkl_enabled):
                            continue
                        if cat_id.startswith("comb_") and (
                            not combine_enabled or not (mal_enabled or anilist_enabled or simkl_enabled)
                        ):
                            continue

                    configured_cat = _configure(cat)
                    if configured_cat not in active_catalogs:
                        active_catalogs.append(configured_cat)

        # 2. Append any other CATALOGS that were not explicitly in the sorted user_catalogs (e.g. search catalogs)
        has_comb_in_user_catalogs = any(c.startswith("comb_") for c in user_catalogs)
        has_single_in_user_catalogs = any(c.startswith(("mal_", "anilist_", "simkl_")) for c in user_catalogs)

        for cat in CATALOGS:
            cat_id = cat["id"]
            if cat_id == "anisync_search":
                if not enable_search:
                    continue
            elif cat_id in REC_CATALOG_IDS:
                if not enable_recommendations:
                    continue
                if cat_id not in user_catalogs:
                    continue
            elif cat_id in DISCOVERY_CATALOG_IDS:
                if not enable_discovery_catalogs:
                    continue
                if cat_id not in user_catalogs:
                    continue
            else:
                if not enable_catalogs:
                    continue
                if cat_id.startswith("mal_") and (combine_enabled or not mal_enabled):
                    continue
                if cat_id.startswith("anilist_") and (combine_enabled or not anilist_enabled):
                    continue
                if cat_id.startswith("simkl_") and (combine_enabled or not simkl_enabled):
                    continue
                if cat_id.startswith("comb_") and (
                    not combine_enabled or not (mal_enabled or anilist_enabled or simkl_enabled)
                ):
                    continue
                # Omit if the user explicitly unchecked it
                if cat_id not in user_catalogs:
                    if cat_id.startswith("comb_") and not has_comb_in_user_catalogs:
                        pass
                    elif cat_id.startswith(("mal_", "anilist_", "simkl_")) and not has_single_in_user_catalogs:
                        pass
                    else:
                        continue

            configured_cat = _configure(cat)
            if configured_cat not in active_catalogs:
                active_catalogs.append(configured_cat)
    else:
        # Fallback to default catalog order if user has not customized them
        for cat in CATALOGS:
            cat_id = cat["id"]
            if cat_id == "anisync_search":
                if not enable_search:
                    continue
            elif cat_id in REC_CATALOG_IDS:
                if not enable_recommendations:
                    continue
            elif cat_id in DISCOVERY_CATALOG_IDS:
                if not enable_discovery_catalogs:
                    continue
            else:
                if not enable_catalogs:
                    continue
                if cat_id.startswith("mal_") and (combine_enabled or not mal_enabled):
                    continue
                if cat_id.startswith("anilist_") and (combine_enabled or not anilist_enabled):
                    continue
                if cat_id.startswith("simkl_") and (combine_enabled or not simkl_enabled):
                    continue
                if cat_id.startswith("comb_") and (
                    not combine_enabled or not (mal_enabled or anilist_enabled or simkl_enabled)
                ):
                    continue

            configured_cat = _configure(cat)
            if configured_cat not in active_catalogs:
                active_catalogs.append(configured_cat)

    return active_catalogs

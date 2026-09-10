import asyncio
import json
import logging
from datetime import UTC, datetime

import sys
from quart import flash, has_request_context, request

from app.services.db import invalidate_user_watchlist_cache
from app.services.db import store_user as _db_store_user


def store_user(user):
    ui_mod = sys.modules.get("app.routes.ui")
    if ui_mod and hasattr(ui_mod, "store_user"):
        return ui_mod.store_user(user)
    return _db_store_user(user)

from .validation import check_gemini_api_key_valid

POSSIBLE_CATS = [
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
    "anisync_dubbed_trending",
    "anisync_dubbed_seasonal",
    "anisync_dubbed_popular",
    "anisync_dubbed_movies",
]


async def handle_configure_form(user: dict, form) -> dict | None:
    """Processes the configure form submission and returns a JSON response if AJAX, or None."""
    uid = user["uid"]

    if "mal_enabled" in form:
        user["mal_enabled"] = form.get("mal_enabled") == "true"
    if "anilist_enabled" in form:
        user["anilist_enabled"] = form.get("anilist_enabled") == "true"
    if "simkl_enabled" in form:
        user["simkl_enabled"] = form.get("simkl_enabled") == "true"
    if "combine_watchlists" in form:
        user["combine_watchlists"] = form.get("combine_watchlists") == "true"
    if "sync_unlisted" in form:
        user["sync_unlisted"] = form.get("sync_unlisted") == "true"
    if "enable_new_episodes_badge" in form:
        user["enable_new_episodes_badge"] = form.get("enable_new_episodes_badge") == "true"
    if "sort_by_new_episodes" in form:
        user["sort_by_new_episodes"] = form.get("sort_by_new_episodes") == "true"
    if "enable_catalogs" in form:
        user["enable_catalogs"] = form.get("enable_catalogs") == "true"
    if "enable_search" in form:
        user["enable_search"] = form.get("enable_search") == "true"
    if "rpdb_in_search" in form:
        user["rpdb_in_search"] = form.get("rpdb_in_search") == "true"
    if "show_filler_tags" in form:
        user["show_filler_tags"] = form.get("show_filler_tags") == "true"
    if "hide_nsfw" in form:
        user["hide_nsfw"] = form.get("hide_nsfw") == "true"
    if "show_watched_tags" in form:
        user["show_watched_tags"] = form.get("show_watched_tags") == "true"
    if "show_tracking_in_synopsis" in form:
        user["show_tracking_in_synopsis"] = form.get("show_tracking_in_synopsis") == "true"
    if "show_airing_in_synopsis" in form:
        user["show_airing_in_synopsis"] = form.get("show_airing_in_synopsis") == "true"

    if "custom_sort_enabled" in form:
        user["custom_sort_enabled"] = form.get("custom_sort_enabled") == "true"
    if "custom_sort_watching_by" in form:
        user["custom_sort_watching_by"] = form.get("custom_sort_watching_by", "default")
    if "custom_sort_watching_order" in form:
        user["custom_sort_watching_order"] = form.get("custom_sort_watching_order", "desc")
    if "custom_sort_planning_by" in form:
        user["custom_sort_planning_by"] = form.get("custom_sort_planning_by", "default")
    if "custom_sort_planning_order" in form:
        user["custom_sort_planning_order"] = form.get("custom_sort_planning_order", "desc")
    if "custom_sort_completed_by" in form:
        user["custom_sort_completed_by"] = form.get("custom_sort_completed_by", "default")
    if "custom_sort_completed_order" in form:
        user["custom_sort_completed_order"] = form.get("custom_sort_completed_order", "desc")
    if "custom_sort_on_hold_by" in form:
        user["custom_sort_on_hold_by"] = form.get("custom_sort_on_hold_by", "default")
    if "custom_sort_on_hold_order" in form:
        user["custom_sort_on_hold_order"] = form.get("custom_sort_on_hold_order", "desc")
    if "custom_sort_dropped_by" in form:
        user["custom_sort_dropped_by"] = form.get("custom_sort_dropped_by", "default")
    if "custom_sort_dropped_order" in form:
        user["custom_sort_dropped_order"] = form.get("custom_sort_dropped_order", "desc")

    if "title_language" in form:
        user["title_language"] = form.get("title_language", "english")

    if "metadata_provider" in form:
        user["metadata_provider"] = form.get("metadata_provider", "kitsu").lower()

    if "meta_synopsis_provider" in form:
        user["meta_synopsis_provider"] = form.get("meta_synopsis_provider", "kitsu").lower()

    if "meta_episodes_provider" in form:
        user["meta_episodes_provider"] = form.get("meta_episodes_provider", "anizp").lower()

    if "meta_poster_provider" in form:
        user["meta_poster_provider"] = form.get("meta_poster_provider", "anilist").lower()

    if "meta_backdrop_provider" in form:
        user["meta_backdrop_provider"] = form.get("meta_backdrop_provider", "fanart").lower()

    if "meta_artwork_provider" in form:
        user["meta_artwork_provider"] = form.get("meta_artwork_provider", "anilist").lower()

    if "meta_airing_provider" in form:
        user["meta_airing_provider"] = form.get("meta_airing_provider", "anilist").lower()

    # Save visible catalogs selection list in custom sorted order
    if "sorted_catalogs" in form:
        sorted_input = form.get("sorted_catalogs") or ""
        sorted_ids = [x.strip() for x in sorted_input.split(",") if x.strip()]

        enabled_list = []
        for cat in sorted_ids:
            if cat in POSSIBLE_CATS and form.get(f"cat_{cat}"):
                enabled_list.append(cat)

        for cat in POSSIBLE_CATS:
            if cat not in enabled_list and form.get(f"cat_{cat}"):
                enabled_list.append(cat)

        user["catalogs"] = enabled_list

        # Save per-catalog custom configurations
        catalog_shapes = user.get("catalog_shapes", {}) or {}
        catalog_titles = user.get("catalog_titles", {}) or {}
        catalog_placements = user.get("catalog_placements", {}) or {}
        catalog_sorts = user.get("catalog_sorts", {}) or {}
        catalog_shuffles = user.get("catalog_shuffles", {}) or {}
        catalog_poster_arts = user.get("catalog_poster_arts", {}) or {}
        catalog_configs = user.get("catalog_configs", {}) or {}

        raw_configs_str = form.get("catalog_configs")
        if raw_configs_str:
            try:
                parsed_configs = json.loads(raw_configs_str)
                if isinstance(parsed_configs, dict):
                    for cat_id, cfg in parsed_configs.items():
                        if not isinstance(cfg, dict):
                            continue
                        if cat_id not in catalog_configs:
                            catalog_configs[cat_id] = {}
                        catalog_configs[cat_id].update(cfg)

                        if "shape" in cfg and cfg["shape"] in ["poster", "landscape"]:
                            catalog_shapes[cat_id] = cfg["shape"]
                        if "title" in cfg:
                            title_val = str(cfg["title"]).strip()
                            if title_val:
                                catalog_titles[cat_id] = title_val
                            elif cat_id in catalog_titles:
                                del catalog_titles[cat_id]
                        if "placement" in cfg and cfg["placement"] in ["all", "discover_only"]:
                            catalog_placements[cat_id] = cfg["placement"]
                        if "poster_art" in cfg:
                            art_val = cfg.get("poster_art")
                            if art_val is False or art_val == "false" or art_val == "off":
                                catalog_poster_arts[cat_id] = False
                                catalog_configs[cat_id]["poster_art"] = False
                            else:
                                if cat_id in catalog_poster_arts:
                                    del catalog_poster_arts[cat_id]
                                catalog_configs[cat_id].pop("poster_art", None)
                        else:
                            if cat_id in catalog_poster_arts:
                                del catalog_poster_arts[cat_id]
                            catalog_configs[cat_id].pop("poster_art", None)

                        if "sort_by" in cfg:
                            sort_by = cfg.get("sort_by", "default")
                            sort_order = cfg.get("sort_order", "desc")
                            if sort_by != "default":
                                catalog_sorts[cat_id] = {"by": sort_by, "order": sort_order}
                            elif cat_id in catalog_sorts:
                                del catalog_sorts[cat_id]
                        if "shuffle" in cfg:
                            catalog_shuffles[cat_id] = bool(cfg["shuffle"])
                        if "dubbed" in cfg:
                            catalog_configs[cat_id]["dubbed"] = bool(cfg["dubbed"])
                        if "audio" in cfg:
                            catalog_configs[cat_id]["audio"] = "dubbed" if cfg.get("audio") == "dubbed" else "all"
                        if "dub_language" in cfg:
                            lang_val = str(cfg.get("dub_language", "")).strip().lower()
                            if lang_val in ["english", "spanish", "german", "french", "italian", "portuguese"]:
                                catalog_configs[cat_id]["dub_language"] = lang_val
            except Exception as e:
                logging.error("Failed to parse catalog_configs: %s", e)

        for cat in POSSIBLE_CATS:
            shape_val = form.get(f"shape_{cat}")
            if shape_val:
                val = shape_val.strip().lower()
                if val in ["landscape", "poster"]:
                    catalog_shapes[cat] = val
                    if cat not in catalog_configs:
                        catalog_configs[cat] = {}
                    catalog_configs[cat]["shape"] = val

        user["catalog_shapes"] = catalog_shapes
        user["catalog_titles"] = catalog_titles
        user["catalog_placements"] = catalog_placements
        user["catalog_poster_arts"] = catalog_poster_arts
        user["catalog_sorts"] = catalog_sorts
        user["catalog_shuffles"] = catalog_shuffles
        user["catalog_configs"] = catalog_configs

    if "enable_recommendations" in form:
        user["enable_recommendations"] = form.get("enable_recommendations") == "true"
    if "recommendations_filter_watched" in form:
        user["recommendations_filter_watched"] = form.get("recommendations_filter_watched") == "true"
    if "enable_discovery_catalogs" in form:
        user["enable_discovery_catalogs"] = form.get("enable_discovery_catalogs") == "true"
    if "shuffle_discovery_catalogs" in form:
        user["shuffle_discovery_catalogs"] = form.get("shuffle_discovery_catalogs") == "true"
    if "enable_dubbed_catalogs" in form:
        user["enable_dubbed_catalogs"] = form.get("enable_dubbed_catalogs") == "true"
    if "dubbed_language" in form:
        from app.services.dub_service import normalize_dub_language

        user["dubbed_language"] = normalize_dub_language(form.get("dubbed_language"))
    if "dubbed_only_discovery" in form:
        user["dubbed_only_discovery"] = form.get("dubbed_only_discovery") == "true"

    if "poster_provider" in form:
        user["poster_provider"] = form.get("poster_provider", "none").strip()
    if "badge_style" in form:
        user["badge_style"] = form.get("badge_style", "modern").strip()
    if "top_poster_key" in form:
        user["top_poster_key"] = form.get("top_poster_key", "").strip()
    if "custom_poster_pattern" in form:
        user["custom_poster_pattern"] = form.get("custom_poster_pattern", "").strip()

    rpdb_task = None
    gemini_task = None
    top_poster_task = None
    rpdb_key = user.get("rpdb_api_key", "")
    gemini_key = user.get("gemini_api_key", "")

    if "rpdb_api_key" in form:
        rpdb_key = form.get("rpdb_api_key", "").strip()
        user["rpdb_api_key"] = rpdb_key
        if rpdb_key:
            from app.services.poster import validate_rpdb_api_key

            rpdb_task = validate_rpdb_api_key(rpdb_key)
        else:
            user["rpdb_key_valid"] = False
            user["rpdb_key_last_checked"] = None

    if "top_poster_key" in form:
        top_key = form.get("top_poster_key", "").strip()
        user["top_poster_key"] = top_key
        if top_key:
            from app.services.poster import validate_top_poster_api_key

            top_poster_task = validate_top_poster_api_key(top_key)
        else:
            user["top_key_valid"] = False

    if "gemini_api_key" in form:
        gemini_key = form.get("gemini_api_key", "").strip()
        user["gemini_api_key"] = gemini_key
        if gemini_key:
            gemini_task = check_gemini_api_key_valid(gemini_key)
        else:
            user["gemini_key_valid"] = False

    if rpdb_task or gemini_task or top_poster_task:
        tasks = []
        if rpdb_task:
            tasks.append(rpdb_task)
        if top_poster_task:
            tasks.append(top_poster_task)
        if gemini_task:
            tasks.append(gemini_task)

        results = await asyncio.gather(*tasks)

        idx = 0
        if rpdb_task:
            rpdb_valid = results[idx]
            user["rpdb_key_valid"] = rpdb_valid
            user["rpdb_key_last_checked"] = datetime.now(UTC).replace(tzinfo=None)
            idx += 1
        if top_poster_task:
            top_valid = results[idx]
            user["top_key_valid"] = top_valid
            idx += 1
        if gemini_task:
            gemini_valid = results[idx][0]
            user["gemini_key_valid"] = gemini_valid

    if "rec_excluded_movie_genres" in form:
        user["rec_excluded_movie_genres"] = form.getlist("rec_excluded_movie_genres")
    if "rec_excluded_series_genres" in form:
        user["rec_excluded_series_genres"] = form.getlist("rec_excluded_series_genres")

    migrated = False
    if not user.get("manifest_token") or user.get("allow_legacy_uid", True):
        import secrets
        user["manifest_token"] = user.get("manifest_token") or secrets.token_urlsafe(16)
        user["allow_legacy_uid"] = False
        migrated = True

    store_user(user)
    invalidate_user_watchlist_cache(uid)

    from config import Config
    base = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}"
    token_or_uid = user.get("manifest_token") or uid
    manifest_url = f"{base}/{token_or_uid}/manifest.json"
    manifest_magnet = f"stremio://{Config.REDIRECT_URL}/{token_or_uid}/manifest.json"

    if "enable_recommendations" in form and user["enable_recommendations"]:
        from app.services.recommendations import trigger_recommendation_update_background

        trigger_recommendation_update_background(uid, force=True)

    current_poster_provider = user.get("poster_provider", "none")
    top_key = user.get("top_poster_key", "")
    validation_failed_msg = []
    if current_poster_provider == "rpdb" and rpdb_key and not user.get("rpdb_key_valid", False):
        validation_failed_msg.append("Invalid RPDB API key")
    if current_poster_provider in ("top_poster", "topposters") and top_key and not user.get("top_key_valid", False):
        validation_failed_msg.append("Invalid TOP Posters API key")
    if user.get("enable_recommendations") and gemini_key and not user.get("gemini_key_valid", False):
        validation_failed_msg.append("Invalid Gemini API key")

    is_json = False
    if has_request_context():
        is_json = request.headers.get("Accept") == "application/json" or request.headers.get("X-Requested-With") == "XMLHttpRequest"

    success_msg = (
        "Your addon URL has changed for security. Click <strong class=\"text-white fw-bold\">Direct Install</strong> to re-add it in your app!"
        if migrated
        else "Preferences saved successfully!"
    )

    if validation_failed_msg:
        msg_str = " & ".join(validation_failed_msg)
        if is_json:
            return {
                "status": "warning",
                "message": f"Preferences saved, but: {msg_str}",
                "migrated": migrated,
                "manifest_url": manifest_url,
                "manifest_magnet": manifest_magnet,
                "rpdb_valid": user.get("rpdb_key_valid", False),
                "top_valid": user.get("top_key_valid", False),
                "gemini_valid": user.get("gemini_key_valid", False),
            }
        if has_request_context():
            await flash(f"Preferences saved, but: {msg_str}", "warning")
    else:
        if is_json:
            return {
                "status": "success",
                "message": success_msg,
                "migrated": migrated,
                "manifest_url": manifest_url,
                "manifest_magnet": manifest_magnet,
                "rpdb_valid": user.get("rpdb_key_valid", False),
                "top_valid": user.get("top_key_valid", False),
                "gemini_valid": user.get("gemini_key_valid", False),
            }
        if has_request_context():
            await flash(success_msg, "success")

    return None

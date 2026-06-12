import asyncio
import datetime

from quart import Blueprint, flash, make_response, redirect, render_template, request, session, url_for

from app.routes.utils import rate_limit
from app.services.db import get_user, store_user
from config import Config

ui_bp = Blueprint("ui", __name__)


async def sync_user_profiles_task(user_id: str):
    import logging

    from app.api import anilist as al_api
    from app.api import mal as mal_api
    from app.services.db import get_user, store_user

    user = get_user(user_id)
    if not user:
        return

    updated = False
    now = datetime.datetime.utcnow()

    # 1. Sync MAL profile details if connected and enabled
    if user.get("mal_access_token") and user.get("mal_enabled", True):
        expiry = user.get("mal_expires_at")
        if expiry and datetime.datetime.utcnow() > expiry:
            logging.warning("Skipping MAL profile sync - token expired for user %s", user_id)
        else:
            try:
                user_info = await mal_api.get_user_details(user["mal_access_token"])
                if user_info.get("name"):
                    user["name"] = user_info["name"]
                if user_info.get("picture"):
                    user["mal_picture"] = user_info["picture"]
                    user["picture"] = user_info["picture"]
                updated = True
            except Exception as e:
                logging.error("Failed to sync MAL profile in background: %s", e)

    # 2. Sync AniList profile details if connected and enabled
    if user.get("anilist_token") and user.get("anilist_enabled", True):
        try:
            viewer = await al_api.get_viewer(user["anilist_token"])
            if viewer.get("name"):
                user["anilist_username"] = viewer["name"]
            if viewer.get("avatar", {}).get("large"):
                user["anilist_picture"] = viewer["avatar"]["large"]
                if not user.get("mal_picture"):
                    user["picture"] = viewer["avatar"]["large"]
            updated = True
        except Exception as e:
            logging.error("Failed to sync AniList profile in background: %s", e)

    # 3. Sync Simkl profile details if connected and enabled
    if user.get("simkl_access_token") and user.get("simkl_enabled", True):
        from app.api import simkl as simkl_api

        try:
            user_info = await simkl_api.get_user_details(user["simkl_access_token"])
            simkl_username = user_info.get("user", {}).get("name")
            simkl_avatar = user_info.get("user", {}).get("avatar")
            if simkl_username:
                user["simkl_username"] = simkl_username
            if simkl_avatar:
                user["simkl_avatar"] = simkl_avatar
                if not user.get("mal_picture") and not user.get("anilist_picture"):
                    user["picture"] = simkl_avatar
            updated = True
        except Exception as e:
            logging.error("Failed to sync Simkl profile in background: %s", e)

    if updated:
        user["last_profile_sync"] = now
        store_user(user)


ANIME_GENRES = [
    "Action",
    "Adventure",
    "Comedy",
    "Drama",
    "Fantasy",
    "Horror",
    "Mahou Shoujo",
    "Mecha",
    "Music",
    "Mystery",
    "Psychological",
    "Romance",
    "Sci-Fi",
    "Slice of Life",
    "Sports",
    "Supernatural",
    "Thriller",
    "Suspense",
    "Award Winning",
    "Boys Love",
    "Girls Love",
    "Ecchi",
    "Gourmet",
]


async def _render(template: str, **kwargs):
    """Render a template, always injecting current_user for the navbar."""
    user_session = session.get("user")
    current_user = get_user(user_session["uid"]) if user_session else None
    return await render_template(template, current_user=current_user, **kwargs)


@ui_bp.route("/")
@rate_limit(limit=30, period_seconds=60)
async def index():
    if session.get("user"):
        return redirect(url_for("ui.configure"))
    resp = await make_response(await _render("index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@ui_bp.route("/configure", methods=["GET", "POST"])
@ui_bp.route("/<user_id>/configure")
@rate_limit(limit=30, period_seconds=60)
async def configure(user_id: str = ""):
    user_session = session.get("user")
    if not user_session:
        return redirect(url_for("ui.index"))

    user = get_user(user_session["uid"])
    if not user:
        await flash("User not found. Please log in again.", "danger")
        return redirect(url_for("ui.index"))

    uid = user["uid"]

    # Time-gated background profile sync (once every 7 days)
    last_sync = user.get("last_profile_sync")
    now_check = datetime.datetime.utcnow()
    if not last_sync or (now_check - last_sync) > datetime.timedelta(days=7):
        asyncio.create_task(sync_user_profiles_task(uid))

    base = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}"
    manifest_url = f"{base}/{uid}/manifest.json"
    manifest_magnet = f"stremio://{Config.REDIRECT_URL}/{uid}/manifest.json"

    if request.method == "POST":
        form = await request.form
        user["mal_enabled"] = form.get("mal_enabled") == "true"
        user["anilist_enabled"] = form.get("anilist_enabled") == "true"
        user["simkl_enabled"] = form.get("simkl_enabled") == "true"
        user["combine_watchlists"] = form.get("combine_watchlists") == "true"
        user["sync_unlisted"] = form.get("sync_unlisted") == "true"
        user["sort_by_new_episodes"] = form.get("sort_by_new_episodes") == "true"
        user["enable_catalogs"] = form.get("enable_catalogs") == "true"
        user["enable_search"] = form.get("enable_search") == "true"
        user["rpdb_in_search"] = form.get("rpdb_in_search") == "true"
        user["show_filler_tags"] = form.get("show_filler_tags") == "true"
        user["show_watched_tags"] = form.get("show_watched_tags") == "true"

        user["custom_sort_enabled"] = form.get("custom_sort_enabled") == "true"
        user["custom_sort_watching_by"] = form.get("custom_sort_watching_by", "default")
        user["custom_sort_watching_order"] = form.get("custom_sort_watching_order", "desc")
        user["custom_sort_planning_by"] = form.get("custom_sort_planning_by", "default")
        user["custom_sort_planning_order"] = form.get("custom_sort_planning_order", "desc")
        user["custom_sort_completed_by"] = form.get("custom_sort_completed_by", "default")
        user["custom_sort_completed_order"] = form.get("custom_sort_completed_order", "desc")
        user["custom_sort_on_hold_by"] = form.get("custom_sort_on_hold_by", "default")
        user["custom_sort_on_hold_order"] = form.get("custom_sort_on_hold_order", "desc")
        user["custom_sort_dropped_by"] = form.get("custom_sort_dropped_by", "default")
        user["custom_sort_dropped_order"] = form.get("custom_sort_dropped_order", "desc")

        try:
            user["new_episode_interval"] = int(form.get("new_episode_interval", 24))
        except (ValueError, TypeError):
            user["new_episode_interval"] = 24

        # Save visible catalogs selection list in custom sorted order
        sorted_input = form.get("sorted_catalogs") or ""
        sorted_ids = [x.strip() for x in sorted_input.split(",") if x.strip()]

        enabled_list = []
        possible_cats = [
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
        ]

        # Add enabled ones in custom sorted order
        for cat in sorted_ids:
            if cat in possible_cats and form.get(f"cat_{cat}"):
                enabled_list.append(cat)

        # Fallback to append any remaining enabled ones
        for cat in possible_cats:
            if cat not in enabled_list and form.get(f"cat_{cat}"):
                enabled_list.append(cat)

        user["catalogs"] = enabled_list
        user["enable_recommendations"] = form.get("enable_recommendations") == "true"
        user["recommendations_filter_watched"] = form.get("recommendations_filter_watched") == "true"
        gemini_key = form.get("gemini_api_key", "").strip()
        rpdb_key = form.get("rpdb_api_key", "").strip()

        user["gemini_api_key"] = gemini_key
        user["rpdb_api_key"] = rpdb_key

        rpdb_task = None
        gemini_task = None

        if rpdb_key:
            from app.services.rpdb import validate_rpdb_api_key

            rpdb_task = validate_rpdb_api_key(rpdb_key)
        else:
            user["rpdb_key_valid"] = False
            user["rpdb_key_last_checked"] = None

        if gemini_key:
            gemini_task = check_gemini_api_key_valid(gemini_key)
        else:
            user["gemini_key_valid"] = False

        if rpdb_task or gemini_task:
            tasks = []
            if rpdb_task:
                tasks.append(rpdb_task)
            if gemini_task:
                tasks.append(gemini_task)

            results = await asyncio.gather(*tasks)

            idx = 0
            if rpdb_task:
                rpdb_valid = results[idx]
                user["rpdb_key_valid"] = rpdb_valid
                user["rpdb_key_last_checked"] = datetime.datetime.utcnow()
                idx += 1
            if gemini_task:
                gemini_valid = results[idx][0]
                user["gemini_key_valid"] = gemini_valid
        else:
            user["rpdb_key_valid"] = False
            user["gemini_key_valid"] = False

        user["rec_language"] = form.get("rec_language", "en")
        user["rec_popularity"] = form.get("rec_popularity", "balanced")
        user["rec_sorting_order"] = form.get("rec_sorting_order", "default")
        try:
            user["rec_year_min"] = int(form.get("rec_year_min", 1980))
        except (ValueError, TypeError):
            user["rec_year_min"] = 1980
        try:
            user["rec_year_max"] = int(form.get("rec_year_max", 2026))
        except (ValueError, TypeError):
            user["rec_year_max"] = 2026

        user["rec_excluded_movie_genres"] = form.getlist("rec_excluded_movie_genres")
        user["rec_excluded_series_genres"] = form.getlist("rec_excluded_series_genres")

        store_user(user)
        from app.services.db import invalidate_user_watchlist_cache

        invalidate_user_watchlist_cache(uid)

        if user["enable_recommendations"]:
            from app.services.recommendations import trigger_recommendation_update_background

            trigger_recommendation_update_background(uid, force=True)

        validation_failed_msg = []
        if rpdb_key and not user.get("rpdb_key_valid", False):
            validation_failed_msg.append("Invalid RPDB API key")
        if gemini_key and not user.get("gemini_key_valid", False):
            validation_failed_msg.append("Invalid Gemini API key")

        if validation_failed_msg:
            msg_str = " & ".join(validation_failed_msg)
            if (
                request.headers.get("Accept") == "application/json"
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            ):
                return {
                    "status": "warning",
                    "message": f"Preferences saved, but: {msg_str}",
                    "rpdb_valid": user.get("rpdb_key_valid", False),
                    "gemini_valid": user.get("gemini_key_valid", False),
                }
            await flash(f"Preferences saved, but: {msg_str}", "warning")
        else:
            if (
                request.headers.get("Accept") == "application/json"
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            ):
                return {
                    "status": "success",
                    "message": "Preferences saved successfully!",
                    "rpdb_valid": user.get("rpdb_key_valid", False),
                    "gemini_valid": user.get("gemini_key_valid", False),
                }
            await flash("Preferences saved.", "success")

    current_year = datetime.datetime.now().year
    resp = await make_response(
        await _render(
            "configure.html",
            user=user,
            manifest_url=manifest_url,
            manifest_magnet=manifest_magnet,
            anime_genres=ANIME_GENRES,
            current_year=current_year,
        )
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


async def check_gemini_api_key_valid(api_key: str) -> tuple[bool, str]:
    import httpx

    if not api_key:
        return False, "Key cannot be empty"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": "Hello, respond with OK if you read this."}]}]}
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True, "API key is valid ✓"
            else:
                try:
                    error_msg = resp.json().get("error", {}).get("message", "Invalid API key")
                except Exception:
                    error_msg = "Invalid API key"
                return False, error_msg
    except Exception as e:
        return False, f"Validation failed: {str(e)}"


@ui_bp.route("/gemini/validation", methods=["POST"])
@rate_limit(limit=10, period_seconds=60)
async def validate_gemini_key():
    data = await request.get_json() or {}
    api_key = data.get("api_key", "").strip()
    is_valid, msg = await check_gemini_api_key_valid(api_key)
    if is_valid:
        return {"status": "success", "message": msg}
    return {"status": "error", "message": msg}, 400


@ui_bp.route("/rpdb/validation", methods=["POST"])
@rate_limit(limit=10, period_seconds=60)
async def validate_rpdb_key():
    data = await request.get_json() or {}
    api_key = data.get("api_key", "").strip()
    if not api_key:
        return {"status": "error", "message": "Key cannot be empty"}, 400
    try:
        from app.services.rpdb import validate_rpdb_api_key

        is_valid = await validate_rpdb_api_key(api_key)
        if is_valid:
            return {"status": "success", "message": "API key is valid ✓"}
        else:
            return {"status": "error", "message": "Invalid RPDB API key"}, 400
    except Exception as e:
        return {"status": "error", "message": f"Validation failed: {str(e)}"}, 500


@ui_bp.route("/health")
@rate_limit(limit=60, period_seconds=60)
async def health():
    import asyncio

    from app.services.db import db

    try:
        # Verify MongoDB is reachable
        await asyncio.to_thread(db.command, "ping")
        return {"status": "healthy", "database": "connected"}, 200
    except Exception as e:
        return {"status": "unhealthy", "database": str(e)}, 500

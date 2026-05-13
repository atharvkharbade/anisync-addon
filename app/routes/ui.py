from quart import Blueprint, flash, make_response, redirect, render_template, request, session, url_for

from app.services.db import get_user, store_user
from app.routes.utils import rate_limit
from config import Config

ui_bp = Blueprint("ui", __name__)

ANIME_GENRES = [
    "Action", "Adventure", "Comedy", "Drama", "Fantasy", "Horror", 
    "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological", 
    "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", 
    "Thriller", "Suspense", "Award Winning", "Boys Love", "Girls Love", 
    "Ecchi", "Gourmet"
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
    base = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}"
    manifest_url = f"{base}/{uid}/manifest.json"
    manifest_magnet = f"stremio://{Config.REDIRECT_URL}/{uid}/manifest.json"

    if request.method == "POST":
        form = await request.form
        user["mal_enabled"] = form.get("mal_enabled") == "true"
        user["anilist_enabled"] = form.get("anilist_enabled") == "true"
        user["combine_watchlists"] = form.get("combine_watchlists") == "true"
        user["sync_unlisted"] = form.get("sync_unlisted") == "true"
        user["sort_by_new_episodes"] = form.get("sort_by_new_episodes") == "true"
        user["enable_catalogs"] = form.get("enable_catalogs") == "true"
        user["enable_search"] = form.get("enable_search") == "true"
        user["show_filler_tags"] = form.get("show_filler_tags") == "true"
        try:
            user["new_episode_interval"] = int(form.get("new_episode_interval", 24))
        except (ValueError, TypeError):
            user["new_episode_interval"] = 24

        # Save visible catalogs selection list in custom sorted order
        sorted_input = form.get("sorted_catalogs") or ""
        sorted_ids = [x.strip() for x in sorted_input.split(",") if x.strip()]

        enabled_list = []
        possible_cats = [
            "mal_watching", "mal_plan_to_watch", "mal_completed", "mal_on_hold", "mal_dropped",
            "anilist_watching", "anilist_planning", "anilist_completed", "anilist_paused", "anilist_dropped", "anilist_repeating",
            "comb_watching", "comb_plan_to_watch", "comb_completed", "comb_paused_on_hold", "comb_dropped",
            "anisync_rec", "anisync_loved", "anisync_liked"
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
        user["gemini_api_key"] = form.get("gemini_api_key", "").strip()

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
        if request.headers.get("Accept") == "application/json" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"status": "success", "message": "Settings saved successfully."}
        await flash("Settings saved.", "success")

    import datetime
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


@ui_bp.route("/gemini/validation", methods=["POST"])
@rate_limit(limit=10, period_seconds=60)
async def validate_gemini_key():
    import httpx
    data = await request.get_json() or {}
    api_key = data.get("api_key", "").strip()
    if not api_key:
        return {"status": "error", "message": "Key cannot be empty"}, 400
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": "Hello, respond with OK if you read this."}]}]}
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return {"status": "success", "message": "API key is valid ✓"}
            else:
                try:
                    error_msg = resp.json().get("error", {}).get("message", "Invalid API key")
                except Exception:
                    error_msg = "Invalid API key"
                return {"status": "error", "message": error_msg}, 400
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

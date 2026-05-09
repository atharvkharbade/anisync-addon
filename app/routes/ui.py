from quart import Blueprint, flash, make_response, redirect, render_template, request, session, url_for

from app.services.db import get_user, store_user
from config import Config

ui_bp = Blueprint("ui", __name__)


async def _render(template: str, **kwargs):
    """Render a template, always injecting current_user for the navbar."""
    user_session = session.get("user")
    current_user = get_user(user_session["uid"]) if user_session else None
    return await render_template(template, current_user=current_user, **kwargs)


@ui_bp.route("/")
async def index():
    if session.get("user"):
        return redirect(url_for("ui.configure"))
    resp = await make_response(await _render("index.html"))
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@ui_bp.route("/configure", methods=["GET", "POST"])
@ui_bp.route("/<user_id>/configure")
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
            "anilist_watching", "anilist_planning", "anilist_completed", "anilist_paused", "anilist_dropped", "anilist_repeating"
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

        store_user(user)
        if request.headers.get("Accept") == "application/json" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"status": "success", "message": "Settings saved successfully."}
        await flash("Settings saved.", "success")

    resp = await make_response(
        await _render(
            "configure.html",
            user=user,
            manifest_url=manifest_url,
            manifest_magnet=manifest_magnet,
        )
    )
    resp.headers["Cache-Control"] = "private, max-age=60"
    return resp

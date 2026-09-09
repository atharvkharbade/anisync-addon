import asyncio
import logging
import secrets
import sys
from datetime import UTC, datetime, timedelta

from quart import flash, make_response, redirect, request, session, url_for

from app.routes.utils import rate_limit
from app.services.db import get_user as _db_get_user
from app.services.db import store_user as _db_store_user
from config import Config

from .blueprint import ANIME_GENRES, _render, ui_bp
from .config_form import handle_configure_form


def get_user(uid):
    ui_mod = sys.modules.get("app.routes.ui")
    if ui_mod and hasattr(ui_mod, "get_user"):
        return ui_mod.get_user(uid)
    return _db_get_user(uid)


def store_user(user):
    ui_mod = sys.modules.get("app.routes.ui")
    if ui_mod and hasattr(ui_mod, "store_user"):
        return ui_mod.store_user(user)
    return _db_store_user(user)


def _get_sync_task():
    ui_mod = sys.modules.get("app.routes.ui")
    if ui_mod and hasattr(ui_mod, "sync_user_profiles_task"):
        return ui_mod.sync_user_profiles_task
    from .profile_sync import sync_user_profiles_task

    return sync_user_profiles_task


@ui_bp.route("/")
@rate_limit(limit=30, period_seconds=60)
async def index():
    user_session = session.get("user")
    if user_session and "uid" in user_session:
        user = get_user(user_session["uid"])
        if user:
            return redirect(url_for("ui.configure"))
        session.pop("user", None)
    resp = await make_response(await _render("index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@ui_bp.route("/guest-login")
@rate_limit(limit=10, period_seconds=60)
async def guest_login():
    user_session = session.get("user")
    if user_session and user_session.get("uid"):
        user = get_user(user_session["uid"])
        if user:
            return redirect(url_for("ui.configure"))
        session.pop("user", None)

    guest_uid = f"guest_{secrets.token_hex(8)}"
    guest_user = {
        "uid": guest_uid,
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
        "meta_artwork_provider": "anilist",
        "meta_airing_provider": "anilist",
        "catalogs": [
            "anisync_spotlight",
            "anisync_schedule",
            "anisync_seasonal",
            "anisync_trending",
            "anisync_top_airing",
            "anisync_highest_rated",
            "anisync_most_popular",
        ],
        "enable_dubbed_catalogs": False,
        "dubbed_language": "english",
        "dubbed_only_discovery": False,
        "created_at": datetime.now(UTC).replace(tzinfo=None),
    }
    store_user(guest_user)
    session["user"] = {"uid": guest_uid, "username": "Guest User", "is_guest": True}
    return redirect(url_for("ui.configure"))


@ui_bp.route("/configure", methods=["GET", "POST"])
@ui_bp.route("/<user_id>/configure", methods=["GET", "POST"])
@rate_limit(limit=30, period_seconds=60)
async def configure(user_id: str = ""):
    user_session = session.get("user")
    if not user_session:
        if user_id and user_id.startswith("guest_"):
            existing_guest = get_user(user_id)
            if existing_guest:
                session["user"] = {"uid": user_id, "username": "Guest User", "is_guest": True}
                user_session = session["user"]
        if not user_session:
            return redirect(url_for("ui.index"))

    user = get_user(user_session.get("uid", ""))
    if not user:
        session.pop("user", None)
        await flash("User not found. Please log in again.", "danger")
        return redirect(url_for("ui.index"))

    uid = user["uid"]

    # Time-gated background profile sync (once every 7 days)
    last_sync = user.get("last_profile_sync")
    now_check = datetime.now(UTC).replace(tzinfo=None)
    if not user.get("is_guest") and (not last_sync or (now_check - last_sync) > timedelta(days=7)):
        asyncio.create_task(_get_sync_task()(uid))

    base = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}"
    manifest_url = f"{base}/{uid}/manifest.json"
    manifest_magnet = f"stremio://{Config.REDIRECT_URL}/{uid}/manifest.json"

    if request.method == "POST":
        form = await request.form
        json_res = await handle_configure_form(user, form)
        if json_res:
            return json_res

    from app.services.dub_service import SUPPORTED_DUB_LANGUAGES

    resp = await make_response(
        await _render(
            "configure.html",
            user=user,
            manifest_url=manifest_url,
            manifest_magnet=manifest_magnet,
            anime_genres=ANIME_GENRES,
            supported_dub_languages=SUPPORTED_DUB_LANGUAGES,
        )
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@ui_bp.route("/delete-account", methods=["POST"])
@rate_limit(limit=10, period_seconds=60)
async def delete_account():
    user_session = session.get("user")
    if not user_session or not user_session.get("uid"):
        return redirect(url_for("ui.index"))

    uid = user_session["uid"]

    try:
        from app.services.db import db, users_collection

        users_collection.delete_one({"uid": uid})
        users_collection.delete_one({"_id": uid})
        db.get_collection("user_watchlist_cache").delete_many({"uid": uid})
        db.get_collection("sessions").delete_many({"data.user.uid": uid})

        session.clear()
        await flash("User records deleted successfully.", "success")
    except Exception as e:
        logging.error("Failed to delete account for uid=%s: %s", uid, e)
        await flash("Failed to delete user records. Please try again.", "danger")

    return redirect(url_for("ui.index"))

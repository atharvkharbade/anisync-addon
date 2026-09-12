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
            "anisync_search",
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
    session["is_new_user"] = True
    return redirect(url_for("ui.configure"))


@ui_bp.route("/configure", methods=["GET", "POST"])
@ui_bp.route("/<user_id>/configure", methods=["GET", "POST"])
@rate_limit(limit=30, period_seconds=60)
async def configure(user_id: str = ""):
    if user_id:
        existing_user = get_user(user_id)
        if existing_user:
            session["user"] = {
                "uid": existing_user["uid"],
                "username": existing_user.get("username") or existing_user.get("mal_username") or "User",
                "is_guest": existing_user.get("is_guest", False),
            }
            user_session = session["user"]
        else:
            return redirect(url_for("ui.index"))
    else:
        user_session = session.get("user")

    # Fallback for AJAX / POST if session cookie wasn't provided or expired
    if not user_session and request.method == "POST":
        form_data = await request.form
        fallback_id = form_data.get("user_id") or form_data.get("manifest_token")
        if fallback_id:
            existing_user = get_user(fallback_id)
            if existing_user:
                session["user"] = {
                    "uid": existing_user["uid"],
                    "username": existing_user.get("username") or existing_user.get("mal_username") or "User",
                    "is_guest": existing_user.get("is_guest", False),
                }
                user_session = session["user"]

    if not user_session:
        if request.headers.get("Accept") == "application/json" or request.is_json:
            return {"status": "error", "message": "Session expired or invalid. Please reload the page."}, 401
        return redirect(url_for("ui.index"))

    user = get_user(user_session.get("uid", ""))
    if not user:
        session.pop("user", None)
        if request.headers.get("Accept") == "application/json" or request.is_json:
            return {"status": "error", "message": "User not found. Please log in again."}, 401
        await flash("User not found. Please log in again.", "danger")
        return redirect(url_for("ui.index"))

    uid = user["uid"]

    # Time-gated background profile sync (once every 7 days)
    last_sync = user.get("last_profile_sync")
    now_check = datetime.now(UTC).replace(tzinfo=None)
    if not user.get("is_guest") and (not last_sync or (now_check - last_sync) > timedelta(days=7)):
        asyncio.create_task(_get_sync_task()(uid))

    base = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}"
    token_or_uid = user.get("manifest_token") or uid
    manifest_url = f"{base}/{token_or_uid}/manifest.json"
    manifest_magnet = f"stremio://{Config.REDIRECT_URL}/{token_or_uid}/manifest.json"

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
    form_data = await request.form
    user_session = session.get("user") or {}

    uid = form_data.get("user_id") or user_session.get("uid")
    manifest_token = form_data.get("manifest_token")

    if not uid and not manifest_token:
        return redirect(url_for("ui.index"))

    try:
        from app.services.db import db, users_collection

        # 1. Locate all records matching this user session, uid, or manifest_token
        query_conditions = []
        if uid:
            query_conditions.extend([{"uid": str(uid)}, {"_id": str(uid)}])
        if manifest_token:
            query_conditions.append({"manifest_token": str(manifest_token)})

        matched_users = list(users_collection.find({"$or": query_conditions}))

        uids_to_delete = set()
        if uid:
            uids_to_delete.add(str(uid))

        tracker_query_or = []
        for u in matched_users:
            if u.get("uid"):
                uids_to_delete.add(str(u["uid"]))
            if u.get("mal_id"):
                tracker_query_or.append({"mal_id": str(u["mal_id"])})
                uids_to_delete.add(str(u["mal_id"]))
            if u.get("anilist_id"):
                tracker_query_or.append({"anilist_id": str(u["anilist_id"])})
                uids_to_delete.add(str(u["anilist_id"]))
                uids_to_delete.add(f"al_{u['anilist_id']}")
            if u.get("simkl_id"):
                tracker_query_or.append({"simkl_id": str(u["simkl_id"])})
                uids_to_delete.add(str(u["simkl_id"]))
                uids_to_delete.add(f"simkl_{u['simkl_id']}")

        # 2. Also search for any duplicate or legacy user documents sharing the same tracker IDs
        if tracker_query_or:
            duplicate_docs = list(users_collection.find({"$or": tracker_query_or}))
            for d in duplicate_docs:
                if d.get("uid"):
                    uids_to_delete.add(str(d["uid"]))
                if d.get("mal_id"):
                    uids_to_delete.add(str(d["mal_id"]))
                if d.get("anilist_id"):
                    uids_to_delete.add(str(d["anilist_id"]))
                    uids_to_delete.add(f"al_{d['anilist_id']}")
                if d.get("simkl_id"):
                    uids_to_delete.add(str(d["simkl_id"]))
                    uids_to_delete.add(f"simkl_{d['simkl_id']}")

        delete_filters = [{"uid": {"$in": list(uids_to_delete)}}]
        if tracker_query_or:
            delete_filters.extend(tracker_query_or)
        if manifest_token:
            delete_filters.append({"manifest_token": str(manifest_token)})

        users_collection.delete_many({"$or": delete_filters})

        for del_uid in uids_to_delete:
            db.get_collection("user_watchlist_cache").delete_many({"uid": str(del_uid)})
            db.get_collection("recommendations_cache").delete_many({"$or": [{"uid": str(del_uid)}, {"user_id": str(del_uid)}]})
            db.get_collection("sessions").delete_many({"data.user.uid": str(del_uid)})

        session.clear()
        session["account_deleted"] = True
        await flash("User records deleted successfully.", "success")
    except Exception as e:
        logging.error("Failed to delete account for uid=%s: %s", uid, e)
        await flash("Failed to delete user records. Please try again.", "danger")

    return redirect(url_for("ui.index"))

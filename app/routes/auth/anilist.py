import secrets
from datetime import datetime

from quart import flash, redirect, render_template, request, session, url_for

from app.api import anilist as al_api
from app.routes.auth.blueprint import auth_bp
from app.routes.auth.helpers import resolve_or_create_user
from app.routes.utils import log_error, rate_limit
from app.services.db import find_user_by_anilist_id, get_user, invalidate_user_watchlist_cache, store_user
from config import Config


@auth_bp.route("/authorize-anilist")
@rate_limit(limit=10, period_seconds=60)
async def authorize_anilist():
    state = secrets.token_urlsafe(32)
    session["anilist_oauth_state"] = state
    anilist_url = (
        f"https://anilist.co/api/v2/oauth/authorize"
        f"?client_id={Config.ANILIST_CLIENT_ID}"
        f"&response_type=token"
        f"&state={state}"
    )
    return redirect(anilist_url)


@auth_bp.route("/anilist-callback")
@rate_limit(limit=10, period_seconds=60)
async def anilist_callback():
    return await render_template("anilist_callback.html")


@auth_bp.route("/anilist-save", methods=["POST"])
@rate_limit(limit=10, period_seconds=60)
async def anilist_save():
    form = await request.form
    token = form.get("token", "").strip()
    req_state = form.get("state", "").strip()
    saved_state = session.pop("anilist_oauth_state", None)

    if not saved_state or not req_state or req_state != saved_state:
        return {"ok": False, "error": "Invalid OAuth state (CSRF check failed)"}, 403

    if not token:
        return {"ok": False, "error": "No token provided"}, 400

    try:
        viewer = await al_api.get_viewer(token)
        anilist_uid = str(viewer["id"])
        anilist_username = viewer.get("name", "")
        anilist_picture = viewer.get("avatar", {}).get("large", "")

        uid, user = resolve_or_create_user(session, anilist_uid, find_user_by_anilist_id)

        user.pop("is_guest", None)
        user["enable_catalogs"] = True
        user["enable_recommendations"] = True

        user.update(
            {
                "uid": uid,
                "anilist_id": anilist_uid,
                "anilist_token": token,
                "anilist_username": anilist_username,
                "anilist_enabled": True if user.get("anilist_token_expired") else user.get("anilist_enabled", True),
                "anilist_picture": anilist_picture,
                "picture": anilist_picture or user.get("mal_picture") or user.get("picture") or "",
                "last_profile_sync": datetime.utcnow(),
                "anilist_consecutive_auth_errors": 0,
            }
        )
        user.pop("anilist_last_auth_error_at", None)
        user.pop("anilist_token_expired", None)
        user.pop("anilist_expired_at", None)
        store_user(user)
        return {"ok": True, "username": anilist_username}

    except Exception as e:
        log_error("ANILIST_SAVE", str(e))
        return {"ok": False, "error": "Invalid token"}, 400


@auth_bp.route("/disconnect-anilist")
@rate_limit(limit=10, period_seconds=60)
async def disconnect_anilist():
    user_session = session.get("user")
    if not user_session:
        return redirect(url_for("ui.index"))

    user = get_user(user_session["uid"])
    if user:
        user.pop("anilist_token", None)
        user.pop("anilist_username", None)
        user.pop("anilist_picture", None)
        user.pop("anilist_token_expired", None)
        user.pop("anilist_expired_at", None)

        if not user.get("mal_access_token") and not user.get("simkl_access_token"):
            user.pop("picture", None)
            session.pop("user", None)
            store_user(user)
            invalidate_user_watchlist_cache(user_session["uid"])
            await flash("Disconnected from AniList and logged out.", "info")
            return redirect(url_for("ui.index"))
        else:
            user["picture"] = user.get("mal_picture") or user.get("simkl_avatar") or ""
            store_user(user)
            invalidate_user_watchlist_cache(user_session["uid"])
            await flash("Disconnected from AniList.", "info")
            return redirect(url_for("ui.configure"))

    return redirect(url_for("ui.index"))

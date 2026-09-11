import secrets
from datetime import datetime

from quart import flash, redirect, request, session, url_for

from app.api import simkl as simkl_api
from app.routes.auth.blueprint import auth_bp
from app.routes.auth.helpers import resolve_or_create_user
from app.routes.utils import log_error, rate_limit
from app.services.db import find_user_by_simkl_id, get_user, invalidate_user_watchlist_cache, store_user
from config import Config


@auth_bp.route("/authorize-simkl")
@rate_limit(limit=10, period_seconds=60)
async def authorize_simkl():
    state = secrets.token_urlsafe(32)
    session["simkl_oauth_state"] = state
    redirect_uri = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/simkl-callback"
    simkl_url = (
        f"https://simkl.com/oauth/authorize"
        f"?client_id={Config.SIMKL_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&state={state}"
    )
    return redirect(simkl_url)


@auth_bp.route("/simkl-callback")
@rate_limit(limit=10, period_seconds=60)
async def simkl_callback():
    # Verify state to prevent CSRF
    req_state = request.args.get("state")
    saved_state = session.pop("simkl_oauth_state", None)
    if not saved_state or req_state != saved_state:
        await flash("Authorization failed: Invalid OAuth state (CSRF check failed).", "danger")
        return redirect(url_for("ui.index"))

    if not (code := request.args.get("code")):
        await flash("Invalid callback. Please try again.", "warning")
        return redirect(url_for("ui.index"))

    try:
        token = await simkl_api.get_access_token(code)
        if not token:
            await flash("Failed to retrieve access token from Simkl.", "danger")
            return redirect(url_for("ui.index"))

        user_info = await simkl_api.get_user_details(token)
        simkl_id = str(
            user_info.get("account", {}).get("id") or user_info.get("user", {}).get("ids", {}).get("simkl") or ""
        )
        if not simkl_id:
            await flash("Failed to retrieve Simkl user ID.", "danger")
            return redirect(url_for("ui.index"))

        simkl_username = user_info.get("user", {}).get("name") or ""
        simkl_avatar = user_info.get("user", {}).get("avatar") or ""

        uid, user = resolve_or_create_user(session, simkl_id, find_user_by_simkl_id)

        user.pop("is_guest", None)
        user["enable_catalogs"] = True
        user.setdefault("enable_recommendations", False)
        user.setdefault("enable_discovery_catalogs", False)
        user.setdefault("sync_unlisted", True)
        user.setdefault("show_filler_tags", False)
        user.setdefault("show_airing_in_synopsis", False)
        user.setdefault("show_tracking_in_synopsis", False)

        user.update(
            {
                "uid": uid,
                "simkl_id": simkl_id,
                "simkl_access_token": token,
                "simkl_username": simkl_username,
                "simkl_avatar": simkl_avatar,
                "simkl_enabled": True if user.get("simkl_token_expired") else user.get("simkl_enabled", True),
                "picture": simkl_avatar
                or user.get("mal_picture")
                or user.get("anilist_picture")
                or user.get("picture")
                or "",
                "last_profile_sync": datetime.utcnow(),
            }
        )
        user.pop("simkl_token_expired", None)
        user.pop("simkl_expired_at", None)
        store_user(user)

        # Invalidate watchlist cache so it re-fetches with Simkl items included
        invalidate_user_watchlist_cache(uid)

        await flash("Connected to Simkl!", "success")
        return redirect(url_for("ui.configure"))

    except Exception as e:
        log_error("SIMKL_CALLBACK", str(e))
        await flash("Failed to connect to Simkl.", "danger")
        return redirect(url_for("ui.index"))


@auth_bp.route("/disconnect-simkl")
@rate_limit(limit=10, period_seconds=60)
async def disconnect_simkl():
    user_session = session.get("user")
    if not user_session:
        return redirect(url_for("ui.index"))

    user = get_user(user_session["uid"])
    if user:
        user.pop("simkl_access_token", None)
        user.pop("simkl_username", None)
        user.pop("simkl_avatar", None)
        user.pop("simkl_id", None)
        user.pop("simkl_token_expired", None)
        user.pop("simkl_expired_at", None)

        if not user.get("mal_access_token") and not user.get("anilist_token"):
            user.pop("picture", None)
            session.pop("user", None)
            store_user(user)
            invalidate_user_watchlist_cache(user_session["uid"])
            await flash("Disconnected from Simkl and logged out.", "info")
            return redirect(url_for("ui.index"))
        else:
            user["picture"] = user.get("mal_picture") or user.get("anilist_picture") or ""
            store_user(user)
            invalidate_user_watchlist_cache(user_session["uid"])
            await flash("Disconnected from Simkl.", "info")
            return redirect(url_for("ui.configure"))

    return redirect(url_for("ui.index"))

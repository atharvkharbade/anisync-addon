import secrets
from datetime import datetime, timedelta

from quart import flash, redirect, render_template, request, session, url_for

from app.api import mal as mal_api
from app.routes.auth.blueprint import auth_bp
from app.routes.auth.helpers import resolve_or_create_user
from app.routes.utils import log_error, rate_limit
from app.services.db import find_user_by_mal_id, get_user, invalidate_user_watchlist_cache, store_user


@auth_bp.route("/authorization")
@rate_limit(limit=10, period_seconds=60)
async def authorize_mal():
    code_verifier, code_challenge = mal_api.generate_pkce()
    state = secrets.token_urlsafe(32)

    session["code_verifier"] = code_verifier
    session["oauth_state"] = state

    auth_url = mal_api.get_auth_url(code_challenge, state)
    return await render_template("mal_connecting.html", redirect_url=auth_url)


@auth_bp.route("/callback")
@rate_limit(limit=10, period_seconds=60)
async def mal_callback():
    if request.args.get("error"):
        await flash(request.args.get("message", "MAL authorization failed."), "danger")
        return redirect(url_for("ui.index"))

    # Verify state to prevent CSRF
    req_state = request.args.get("state")
    saved_state = session.pop("oauth_state", None)
    if not saved_state or req_state != saved_state:
        await flash("Authorization failed: Invalid OAuth state (CSRF check failed).", "danger")
        return redirect(url_for("ui.index"))

    if not (code := request.args.get("code")):
        await flash("Invalid callback. Please try again.", "warning")
        return redirect(url_for("ui.index"))

    code_verifier = session.pop("code_verifier", None)
    if not code_verifier:
        await flash("Session error. Please try again.", "warning")
        return redirect(url_for("ui.index"))

    try:
        token_data = await mal_api.get_access_token(code, code_verifier)
        user_info = await mal_api.get_user_details(token_data["access_token"])

        mal_id = str(user_info["id"])

        uid, existing = resolve_or_create_user(session, mal_id, find_user_by_mal_id)

        existing.pop("is_guest", None)
        existing["enable_catalogs"] = True
        existing["enable_recommendations"] = True
        existing.update(
            {
                "uid": uid,
                "mal_id": mal_id,
                "name": user_info.get("name", ""),
                "mal_picture": user_info.get("picture", ""),
                "picture": user_info.get("picture", "") or existing.get("anilist_picture", ""),
                "mal_access_token": token_data["access_token"],
                "mal_refresh_token": token_data["refresh_token"],
                "mal_expires_at": datetime.utcnow() + timedelta(seconds=token_data["expires_in"]),
                "mal_enabled": True if existing.get("mal_token_expired") else existing.get("mal_enabled", True),
                "last_profile_sync": datetime.utcnow(),
            }
        )
        existing.pop("mal_token_expired", None)
        existing.pop("mal_expired_at", None)
        store_user(existing)

        await flash("Connected to MyAnimeList!", "success")
        return redirect(url_for("ui.configure"))

    except Exception as e:
        log_error("MAL_CALLBACK", str(e))
        await flash("Failed to connect to MyAnimeList.", "danger")
        return redirect(url_for("ui.index"))


@auth_bp.route("/refresh-mal")
@rate_limit(limit=10, period_seconds=60)
async def refresh_mal():
    user_session = session.get("user")
    if not user_session:
        await flash("Not logged in.", "warning")
        return redirect(url_for("ui.index"))

    user = get_user(user_session["uid"])
    if not user or not user.get("mal_refresh_token"):
        await flash("No MAL session to refresh.", "danger")
        return redirect(url_for("ui.index"))

    try:
        token_data = await mal_api.refresh_token(user["mal_refresh_token"])
        user.update(
            {
                "mal_access_token": token_data["access_token"],
                "mal_refresh_token": token_data["refresh_token"],
                "mal_expires_at": datetime.utcnow() + timedelta(seconds=token_data["expires_in"]),
            }
        )
        user.pop("mal_token_expired", None)
        user.pop("mal_expired_at", None)
        store_user(user)
        await flash("MAL session refreshed.", "success")
    except Exception as e:
        log_error("MAL_REFRESH", str(e))
        await flash("Failed to refresh MAL session.", "danger")

    return redirect(url_for("ui.configure"))


@auth_bp.route("/disconnect-mal")
@rate_limit(limit=10, period_seconds=60)
async def disconnect_mal():
    user_session = session.get("user")
    if not user_session:
        return redirect(url_for("ui.index"))

    user = get_user(user_session["uid"])
    if user:
        user.pop("mal_access_token", None)
        user.pop("mal_refresh_token", None)
        user.pop("mal_expires_at", None)
        user.pop("name", None)
        user.pop("mal_picture", None)
        user.pop("mal_token_expired", None)
        user.pop("mal_expired_at", None)

        if not user.get("anilist_token") and not user.get("simkl_access_token"):
            user.pop("picture", None)
            session.pop("user", None)
            store_user(user)
            invalidate_user_watchlist_cache(user_session["uid"])
            await flash("Disconnected from MyAnimeList and logged out.", "info")
            return redirect(url_for("ui.index"))
        else:
            user["picture"] = user.get("anilist_picture") or user.get("simkl_avatar") or ""
            store_user(user)
            invalidate_user_watchlist_cache(user_session["uid"])
            await flash("Disconnected from MyAnimeList.", "info")
            return redirect(url_for("ui.configure"))

    return redirect(url_for("ui.index"))

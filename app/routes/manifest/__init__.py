from quart import Blueprint

from app.routes.utils import is_valid_user_id, rate_limit, respond_with
from app.services.db import get_user

from .assets import (
    handle_favicon_ico,
    handle_logo_png,
    handle_logo_svg,
    handle_serve_asset,
    save_logo_to_path,
)
from .builder import MANIFEST, build_base_manifest, build_user_manifest
from .catalogs import CATALOGS, filter_user_catalogs

manifest_bp = Blueprint("manifest", __name__)


@manifest_bp.route("/logo.png")
@rate_limit(limit=60, period_seconds=60)
async def logo_png():
    return await handle_logo_png()


@manifest_bp.route("/favicon.ico")
@manifest_bp.route("/<user_id>/favicon.ico")
@rate_limit(limit=60, period_seconds=60)
async def favicon_ico(user_id: str | None = None):
    return await handle_favicon_ico()


@manifest_bp.route("/assets/<filename>")
async def serve_asset(filename: str):
    return await handle_serve_asset(filename)


@manifest_bp.route("/logo.svg")
@rate_limit(limit=60, period_seconds=60)
async def logo_svg():
    return await handle_logo_svg()


@manifest_bp.route("/manifest.json")
@rate_limit(limit=60, period_seconds=60)
async def base_manifest():
    unconfigured = build_base_manifest()
    return await respond_with(unconfigured, max_age=86400, stale_while_revalidate=86400)


@manifest_bp.route("/<user_id>/manifest.json")
@rate_limit(limit=60, period_seconds=60)
async def user_manifest(user_id: str):
    if not is_valid_user_id(user_id):
        unconfigured = build_base_manifest()
        return await respond_with(unconfigured, max_age=86400, stale_while_revalidate=86400)

    user = get_user(user_id, for_manifest=True)
    if not user:
        unconfigured = build_base_manifest()
        return await respond_with(unconfigured, max_age=86400, stale_while_revalidate=86400)

    manifest_data = build_user_manifest(user_id, user)
    max_age = 43200 if manifest_data.get("catalogs") else 86400
    return await respond_with(manifest_data, max_age=max_age, stale_while_revalidate=86400)


__all__ = [
    "manifest_bp",
    "CATALOGS",
    "MANIFEST",
    "save_logo_to_path",
    "logo_png",
    "favicon_ico",
    "serve_asset",
    "logo_svg",
    "base_manifest",
    "user_manifest",
    "filter_user_catalogs",
    "build_base_manifest",
    "build_user_manifest",
]

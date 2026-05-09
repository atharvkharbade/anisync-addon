import logging
from quart import Blueprint, abort, redirect, request

poster_bp = Blueprint("poster", __name__)


@poster_bp.route("/<user_id>/poster/<string:media_id>.jpg")
async def serve_modified_poster(user_id: str, media_id: str):
    """
    Safely redirect to the original poster URL.
    This handles any cached catalog images in Stremio without requiring PIL processing or download overhead.
    """
    original_url = request.args.get("url")
    if not original_url:
        return abort(400)

    # Perform a solid 302 redirect directly to the original image URL
    return redirect(original_url)

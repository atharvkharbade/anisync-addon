from quart import Blueprint, render_template, session

from app.services.db import get_user

ui_bp = Blueprint("ui", __name__)

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
    current_user = get_user(user_session["uid"]) if user_session and "uid" in user_session else None
    if user_session and not current_user:
        session.pop("user", None)
    return await render_template(template, current_user=current_user, **kwargs)

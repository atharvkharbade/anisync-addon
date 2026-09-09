"""Authentication routes subpackage for AniSync.

Handles OAuth workflows for MyAnimeList, AniList, and Simkl,
as well as user session management.
"""

from app.routes.auth.blueprint import auth_bp
from app.routes.auth.session import logout
from app.routes.auth.mal import (
    authorize_mal,
    mal_callback,
    refresh_mal,
    disconnect_mal,
)
from app.routes.auth.anilist import (
    authorize_anilist,
    anilist_callback,
    anilist_save,
    disconnect_anilist,
)
from app.routes.auth.simkl import (
    authorize_simkl,
    simkl_callback,
    disconnect_simkl,
)
from app.routes.auth.helpers import resolve_or_create_user

__all__ = [
    "auth_bp",
    "logout",
    "authorize_mal",
    "mal_callback",
    "refresh_mal",
    "disconnect_mal",
    "authorize_anilist",
    "anilist_callback",
    "anilist_save",
    "disconnect_anilist",
    "authorize_simkl",
    "simkl_callback",
    "disconnect_simkl",
    "resolve_or_create_user",
]

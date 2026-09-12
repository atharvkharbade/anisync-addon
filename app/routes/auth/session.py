from quart import flash, redirect, session, url_for

from app.routes.auth.blueprint import auth_bp
from app.routes.utils import rate_limit


@auth_bp.route("/logout", methods=["GET", "POST"])
@rate_limit(limit=10, period_seconds=60)
async def logout():
    session.clear()
    if hasattr(session, "rotate"):
        session.rotate()
    await flash("Logged out.", "info")
    return redirect(url_for("ui.index"))

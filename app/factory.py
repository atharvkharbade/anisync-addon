import logging
import os
import uuid

from app.app import App
from app.lib.mongo_session import MongoSessionInterface
from app.routes.auth import auth_bp
from app.routes.catalog import catalog_bp
from app.routes.manifest import manifest_bp
from app.routes.meta import meta_bp
from app.routes.poster import poster_bp
from app.routes.subtitles import subtitles_bp
from app.routes.ui import ui_bp
from app.services.http import close_client, correlation_id_var, init_client


def create_app() -> App:
    app_ = App(__name__, template_folder="../templates")
    app_.config.from_object("config.Config")

    # Set up custom MongoDB-backed session interface
    app_.session_interface = MongoSessionInterface()

    @app_.before_serving
    async def startup():
        # Pre-generate logo if missing
        base_dir = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(base_dir, "assets", "logo.png")
        if not os.path.exists(logo_path):
            try:
                os.makedirs(os.path.dirname(logo_path), exist_ok=True)
                from app.routes.manifest import save_logo_to_path

                save_logo_to_path(logo_path)
                logging.info("Pre-generated static logo.png asset at startup.")
            except Exception as e:
                logging.error("Failed to pre-generate logo on startup: %s", e)

        init_client()

        # Ensure Fribb mappings are populated and schema is up-to-date in the background
        import asyncio

        from app.lib.id_resolver import ensure_fribb_mappings
        from app.services.http import get_client

        asyncio.create_task(ensure_fribb_mappings(get_client()))

        # Start background task to update popular fallback anime covers automatically
        from app.services.recommendations import trigger_popular_fallbacks_update_background

        trigger_popular_fallbacks_update_background()

    @app_.after_serving
    async def shutdown():
        await close_client()

    # Request and response correlation ID hooks
    from quart import request

    @app_.before_request
    async def before_request_hook():
        req_id = request.headers.get("X-Correlation-Id") or request.headers.get("X-Request-Id") or str(uuid.uuid4())
        correlation_id_var.set(req_id)

    @app_.after_request
    async def after_request_hook(response):
        corr_id = correlation_id_var.get()
        if corr_id:
            response.headers["X-Correlation-Id"] = corr_id
        return response

    app_.register_blueprint(auth_bp)
    app_.register_blueprint(manifest_bp)
    app_.register_blueprint(subtitles_bp)
    app_.register_blueprint(ui_bp)
    app_.register_blueprint(catalog_bp)
    app_.register_blueprint(meta_bp)
    app_.register_blueprint(poster_bp)

    return app_

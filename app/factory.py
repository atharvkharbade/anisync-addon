from app.app import App
from app.routes.auth import auth_bp
from app.routes.manifest import manifest_bp
from app.routes.subtitles import subtitles_bp
from app.routes.ui import ui_bp
from app.routes.catalog import catalog_bp
from app.routes.meta import meta_bp
from app.routes.poster import poster_bp


def create_app() -> App:
    app_ = App(__name__, template_folder="../templates")
    app_.config.from_object("config.Config")
    app_.register_blueprint(auth_bp)
    app_.register_blueprint(manifest_bp)
    app_.register_blueprint(subtitles_bp)
    app_.register_blueprint(ui_bp)
    app_.register_blueprint(catalog_bp)
    app_.register_blueprint(meta_bp)
    app_.register_blueprint(poster_bp)
    return app_


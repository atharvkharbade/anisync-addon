from config import Config
from .catalogs import CATALOGS, filter_user_catalogs

MANIFEST = {
    "id": "com.anisync.stremio",
    "version": "1.5.0",
    "name": "AniSync",
    "logo": f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/logo.png",
    "icon": f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/logo.png",
    "description": "An anime addon for Stremio and Nuvio offering tracking, rich metadata, recommendations, and much more!",
    "types": ["anime", "series", "movie"],
    "resources": ["subtitles", "catalog", "meta"],
    "idPrefixes": ["kitsu", "mal", "anilist", "simkl"],
    "catalogs": CATALOGS,
    "behaviorHints": {
        "configurable": True,
    },
}


def build_base_manifest() -> dict:
    """Returns unconfigured manifest forcing the user to configure."""
    unconfigured_manifest = MANIFEST.copy()
    unconfigured_manifest["catalogs"] = []
    unconfigured_manifest["resources"] = ["subtitles"]
    unconfigured_manifest["behaviorHints"] = {
        "configurable": True,
        "configurationRequired": True,
    }
    return unconfigured_manifest


def build_user_manifest(user_id: str, user: dict | None) -> dict:
    """Builds user-tailored manifest based on active integrations and catalog settings."""
    if not user:
        return build_base_manifest()

    if user.get("is_guest"):
        enable_catalogs = False
        enable_recommendations = False
    else:
        enable_catalogs = user.get("enable_catalogs", True)
        enable_recommendations = user.get("enable_recommendations", False)

    enable_search = user.get("enable_search", True)
    enable_discovery_catalogs = user.get("enable_discovery_catalogs", True if user.get("is_guest") else False)

    user_manifest_data = MANIFEST.copy()
    if not enable_catalogs and not enable_search and not enable_recommendations and not enable_discovery_catalogs:
        user_manifest_data["catalogs"] = []
        user_manifest_data["resources"] = ["subtitles"]
        return user_manifest_data

    user_manifest_data["resources"] = ["subtitles", "catalog", "meta"]

    # Trigger background recommendations update if enabled
    if enable_recommendations:
        from app.services.recommendations import trigger_recommendation_update_background

        trigger_recommendation_update_background(user_id)

    user_manifest_data["catalogs"] = filter_user_catalogs(user)
    return user_manifest_data

import os
from quart import Blueprint, Response, send_file

from app.routes.utils import respond_with, is_valid_user_id, rate_limit
from app.services.db import get_user
from config import Config

manifest_bp = Blueprint("manifest", __name__)


CATALOGS = [
    {
        "type": "anime",
        "id": "mal_watching",
        "name": "MAL: Watching",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "mal_plan_to_watch",
        "name": "MAL: Plan to Watch",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "mal_completed",
        "name": "MAL: Completed",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "mal_on_hold",
        "name": "MAL: On Hold",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "mal_dropped",
        "name": "MAL: Dropped",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anilist_watching",
        "name": "AniList: Watching",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anilist_planning",
        "name": "AniList: Planning",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anilist_completed",
        "name": "AniList: Completed",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anilist_paused",
        "name": "AniList: Paused",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anilist_dropped",
        "name": "AniList: Dropped",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anilist_repeating",
        "name": "AniList: Repeating",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "simkl_watching",
        "name": "Simkl: Watching",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "simkl_plantowatch",
        "name": "Simkl: Plan to Watch",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "simkl_completed",
        "name": "Simkl: Completed",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "simkl_hold",
        "name": "Simkl: On Hold",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "simkl_dropped",
        "name": "Simkl: Dropped",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "comb_watching",
        "name": "Watching",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "comb_plan_to_watch",
        "name": "Plan to Watch",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "comb_completed",
        "name": "Completed",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "comb_paused_on_hold",
        "name": "On Hold",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "comb_dropped",
        "name": "Dropped",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anisync_rec",
        "name": "Top Picks for You",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anisync_loved",
        "name": "Inspired by your Favorites",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anisync_liked",
        "name": "More from your Watchlist",
        "extra": [{"name": "skip"}],
    },
    {
        "type": "anime",
        "id": "anisync_search",
        "name": "Search",
        "extra": [{"name": "search", "isRequired": True}, {"name": "skip"}],
    },
]

MANIFEST = {
    "id": "com.anisync.stremio",
    "version": "1.3.0-beta",
    "name": "AniSync",
    "logo": f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/logo.png?v=7",
    "description": "Sync and update your anime watchlists on MyAnimeList, AniList, and Simkl in real-time. Easily track episodes, combine watchlists, skip fillers, and get personalized recommendations directly inside Stremio.",
    "types": ["anime", "series", "movie"],
    "resources": ["subtitles", "catalog", "meta"],
    "idPrefixes": ["kitsu", "mal", "anilist", "simkl"],
    "catalogs": CATALOGS,
    "behaviorHints": {
        "configurable": True,
    },
}


def save_logo_to_path(path: str):
    """Pillow-based logo drawing logic. Executed on startup to generate static asset."""
    from PIL import Image, ImageDraw
    scale = 256.0 / 24.0

    def transform(x, y):
        tx = 12.0 + (x - 12.0) * 1.375
        ty = 12.0 + (y - 10.0) * 1.375
        return tx * scale, ty * scale

    # 1. Generate grad-left (vertical gradient)
    gradient_left_1d = Image.new("RGBA", (1, 256))
    for y in range(256):
        r = int(0 + (2 - 0) * (y / 255.0))
        g = int(242 + (169 - 242) * (y / 255.0))
        b = int(254 + (255 - 254) * (y / 255.0))
        gradient_left_1d.putpixel((0, y), (r, g, b, 255))
    grad_left_img = gradient_left_1d.resize((256, 256))

    # 2. Generate grad-right (vertical gradient with 0.85 opacity)
    gradient_right_1d = Image.new("RGBA", (1, 256))
    for y in range(256):
        r = int(46 + (0 - 46) * (y / 255.0))
        g = int(196 + (242 - 196) * (y / 255.0))
        b = int(182 + (254 - 182) * (y / 255.0))
        gradient_right_1d.putpixel((0, y), (r, g, b, 216))
    grad_right_img = gradient_right_1d.resize((256, 256))

    # 3. Generate grad-sync (horizontal gradient)
    gradient_sync_1d = Image.new("RGBA", (256, 1))
    for x in range(256):
        r = int(2 + (46 - 2) * (x / 255.0))
        g = int(169 + (196 - 169) * (x / 255.0))
        b = int(255 + (182 - 255) * (x / 255.0))
        gradient_sync_1d.putpixel((x, 0), (r, g, b, 255))
    grad_sync_img = gradient_sync_1d.resize((256, 256))

    # 4. Draw Left Polygon Mask (A-frame)
    left_polygon = [
        transform(12, 2),
        transform(4, 18),
        transform(8, 18),
        transform(12, 10),
        transform(16, 18),
        transform(20, 18)
    ]
    mask_left = Image.new("L", (256, 256), 0)
    draw_left = ImageDraw.Draw(mask_left)
    draw_left.polygon(left_polygon, fill=255)

    # 5. Draw Right Polygon Mask
    right_polygon = [
        transform(12, 2),
        transform(16, 10),
        transform(8, 10)
    ]
    mask_right = Image.new("L", (256, 256), 0)
    draw_right = ImageDraw.Draw(mask_right)
    draw_right.polygon(right_polygon, fill=255)

    # 6. Draw Sync Bridge Mask (Bezier Curve + Arrows)
    mask_sync = Image.new("L", (256, 256), 0)
    draw_sync = ImageDraw.Draw(mask_sync)
    
    curve_points = []
    for i in range(101):
        t = i / 100.0
        x_val = ((1 - t)**3 * 6 + 3 * (1 - t)**2 * t * 8 + 3 * (1 - t) * t**2 * 16 + t**3 * 18)
        y_val = ((1 - t)**3 * 15 + 3 * (1 - t)**2 * t * 12.5 + 3 * (1 - t) * t**2 * 12.5 + t**3 * 15)
        curve_points.append(transform(x_val, y_val))
    
    draw_sync.line(curve_points, fill=255, width=29, joint="curve")
    
    draw_sync.line([transform(18, 15), transform(16.2, 13.5)], fill=255, width=26)
    draw_sync.line([transform(18, 15), transform(16.2, 16.5)], fill=255, width=26)
    
    draw_sync.line([transform(6, 15), transform(7.8, 16.5)], fill=255, width=26)
    draw_sync.line([transform(6, 15), transform(7.8, 13.5)], fill=255, width=26)
    
    r_cap = 13.0
    endpoints = [
        transform(16.2, 13.5),
        transform(16.2, 16.5),
        transform(7.8, 16.5),
        transform(7.8, 13.5)
    ]
    for pt in endpoints:
        draw_sync.ellipse([pt[0] - r_cap, pt[1] - r_cap, pt[0] + r_cap, pt[1] + r_cap], fill=255)

    # 7. Assemble final image with transparency
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    img.paste(grad_left_img, (0, 0), mask_left)
    img.paste(grad_right_img, (0, 0), mask_right)
    img.paste(grad_sync_img, (0, 0), mask_sync)
    
    # Save the file
    img.save(path, format="PNG")


@manifest_bp.route("/logo.png")
@rate_limit(limit=60, period_seconds=60)
async def logo_png():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logo_path = os.path.join(base_dir, "assets", "logo.png")
    
    if os.path.exists(logo_path):
        response = await send_file(logo_path, mimetype="image/png")
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response
        
    # On-demand generation fallback if missing
    try:
        os.makedirs(os.path.dirname(logo_path), exist_ok=True)
        save_logo_to_path(logo_path)
        response = await send_file(logo_path, mimetype="image/png")
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response
    except Exception:
        return "Logo not found", 404


@manifest_bp.route("/logo.svg")
@rate_limit(limit=60, period_seconds=60)
async def logo_svg():
    svg_content = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
    <defs>
        <linearGradient id="grad-left" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stop-color="#00f2fe" />
            <stop offset="100%" stop-color="#02a9ff" />
        </linearGradient>
        <linearGradient id="grad-right" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stop-color="#2ec4b6" />
            <stop offset="100%" stop-color="#00f2fe" />
        </linearGradient>
        <linearGradient id="grad-sync" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stop-color="#02a9ff" />
            <stop offset="100%" stop-color="#2ec4b6" />
        </linearGradient>
    </defs>
    <path d="M12 2L4 18H8L12 10L16 18H20L12 2Z" fill="url(#grad-left)" />
    <path d="M12 2L16 10H8L12 2Z" fill="url(#grad-right)" opacity="0.85" />
    <path d="M6 15C8 12.5 16 12.5 18 15" stroke="url(#grad-sync)" stroke-width="2" stroke-linecap="round" />
    <path d="M18 15L16.2 13.5M18 15L16.2 16.5" stroke="url(#grad-sync)" stroke-width="1.8" stroke-linecap="round" />
    <path d="M6 15L7.8 16.5M6 15L7.8 13.5" stroke="url(#grad-sync)" stroke-width="1.8" stroke-linecap="round" />
</svg>"""
    return Response(svg_content, mimetype="image/svg+xml")


@manifest_bp.route("/manifest.json")
@rate_limit(limit=60, period_seconds=60)
async def base_manifest():
    unconfigured_manifest = MANIFEST.copy()
    unconfigured_manifest["behaviorHints"] = {
        "configurable": True,
        "configurationRequired": True,
    }
    return await respond_with(unconfigured_manifest)


@manifest_bp.route("/<user_id>/manifest.json")
@rate_limit(limit=60, period_seconds=60)
async def user_manifest(user_id: str):
    if not is_valid_user_id(user_id):
        fallback = MANIFEST.copy()
        fallback["behaviorHints"] = {
            "configurable": True,
            "configurationRequired": True,
        }
        return await respond_with(fallback)

    user = get_user(user_id)
    if not user:
        # Return fallback configuration needed manifest
        fallback = MANIFEST.copy()
        fallback["behaviorHints"] = {
            "configurable": True,
            "configurationRequired": True,
        }
        return await respond_with(fallback)

    # Filter catalogs and resources based on active toggles
    enable_catalogs = user.get("enable_catalogs", True)
    enable_search = user.get("enable_search", True)
    enable_recommendations = user.get("enable_recommendations", True)

    user_manifest_data = MANIFEST.copy()
    if not enable_catalogs and not enable_search and not enable_recommendations:
        user_manifest_data["catalogs"] = []
        user_manifest_data["resources"] = ["subtitles"]
        return await respond_with(user_manifest_data)

    user_manifest_data["resources"] = ["subtitles", "catalog", "meta"]

    # Trigger background recommendations update if enabled
    if enable_recommendations:
        from app.services.recommendations import trigger_recommendation_update_background
        trigger_recommendation_update_background(user_id)

    # Filter catalogs based on active integrations and custom selections
    mal_enabled = user.get("mal_access_token") and user.get("mal_enabled", True)
    anilist_enabled = user.get("anilist_token") and user.get("anilist_enabled", True)
    simkl_enabled = user.get("simkl_access_token") and user.get("simkl_enabled", True)
    combine_enabled = user.get("combine_watchlists", False)
    user_catalogs = user.get("catalogs")
    if user_catalogs is not None:
        user_catalogs = [c if c != "anime_tracker_search" else "anisync_search" for c in user_catalogs]

    def get_configured_catalog(cat):
        return cat.copy()

    active_catalogs = []
    rec_catalog_ids = ["anisync_rec", "anisync_loved", "anisync_liked"]
    
    # 1. Add custom sorted catalogs first (if user has saved preferences)
    if user_catalogs is not None:
        for cat_id in user_catalogs:
            for cat in CATALOGS:
                if cat["id"] == cat_id:
                    # Apply visibility checks
                    if cat_id == "anisync_search":
                        if not enable_search:
                            continue
                    elif cat_id in rec_catalog_ids:
                        if not enable_recommendations:
                            continue
                    else:
                        if not enable_catalogs:
                            continue
                        if cat_id.startswith("mal_") and (combine_enabled or not mal_enabled):
                            continue
                        if cat_id.startswith("anilist_") and (combine_enabled or not anilist_enabled):
                            continue
                        if cat_id.startswith("simkl_") and (combine_enabled or not simkl_enabled):
                            continue
                        if cat_id.startswith("comb_") and (not combine_enabled or not (mal_enabled or anilist_enabled or simkl_enabled)):
                            continue
                    
                    configured_cat = get_configured_catalog(cat)
                    if configured_cat not in active_catalogs:
                        active_catalogs.append(configured_cat)
                
        # 2. Append any other CATALOGS that were not explicitly in the sorted user_catalogs (e.g. search catalogs)
        for cat in CATALOGS:
            cat_id = cat["id"]
            if cat_id == "anisync_search":
                if not enable_search:
                    continue
            elif cat_id in rec_catalog_ids:
                if not enable_recommendations:
                    continue
                if cat_id not in user_catalogs:
                    continue
            else:
                if not enable_catalogs:
                    continue
                if cat_id.startswith("mal_") and (combine_enabled or not mal_enabled):
                    continue
                if cat_id.startswith("anilist_") and (combine_enabled or not anilist_enabled):
                    continue
                if cat_id.startswith("simkl_") and (combine_enabled or not simkl_enabled):
                    continue
                if cat_id.startswith("comb_") and (not combine_enabled or not (mal_enabled or anilist_enabled or simkl_enabled)):
                    continue
                # Omit if the user explicitly unchecked it
                if cat_id not in user_catalogs:
                    continue
            
            configured_cat = get_configured_catalog(cat)
            if configured_cat not in active_catalogs:
                active_catalogs.append(configured_cat)
    else:
        # Fallback to default catalog order if user has not customized them
        for cat in CATALOGS:
            cat_id = cat["id"]
            if cat_id == "anisync_search":
                if not enable_search:
                    continue
            elif cat_id in rec_catalog_ids:
                if not enable_recommendations:
                    continue
            else:
                if not enable_catalogs:
                    continue
                if cat_id.startswith("mal_") and (combine_enabled or not mal_enabled):
                    continue
                if cat_id.startswith("anilist_") and (combine_enabled or not anilist_enabled):
                    continue
                if cat_id.startswith("simkl_") and (combine_enabled or not simkl_enabled):
                    continue
                if cat_id.startswith("comb_") and (not combine_enabled or not (mal_enabled or anilist_enabled or simkl_enabled)):
                    continue
            
            configured_cat = get_configured_catalog(cat)
            if configured_cat not in active_catalogs:
                active_catalogs.append(configured_cat)

    user_manifest_data["catalogs"] = active_catalogs
    return await respond_with(user_manifest_data)

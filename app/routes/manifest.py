from quart import Blueprint, Response

from app.routes.utils import respond_with
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
        "id": "anime_tracker_search",
        "name": "Search",
        "extra": [{"name": "search", "isRequired": True}, {"name": "skip"}],
    },
]

MANIFEST = {
    "id": "com.anisync.stremio",
    "version": "1.2.2",
    "name": "AniSync",
    "logo": f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/logo.png?v=7",
    "description": "Browse and sync your anime progress dynamically to MyAnimeList and AniList as you stream.",
    "types": ["anime", "series", "movie"],
    "resources": ["subtitles", "catalog", "meta"],
    "idPrefixes": ["kitsu", "mal", "anilist"],
    "catalogs": CATALOGS,
    "behaviorHints": {
        "configurable": True,
    },
}


@manifest_bp.route("/logo.png")
async def logo_png():
    from PIL import Image, ImageDraw
    import io
    
    scale = 256.0 / 24.0

    # Transformation function to scale up by 1.375 and center inside the viewport
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
    
    # Cubic Bezier: M6 15C8 12.5 16 12.5 18 15
    curve_points = []
    for i in range(101):
        t = i / 100.0
        x_val = ((1 - t)**3 * 6 + 3 * (1 - t)**2 * t * 8 + 3 * (1 - t) * t**2 * 16 + t**3 * 18)
        y_val = ((1 - t)**3 * 15 + 3 * (1 - t)**2 * t * 12.5 + 3 * (1 - t) * t**2 * 12.5 + t**3 * 15)
        curve_points.append(transform(x_val, y_val))
    
    # Line width scaled up to 29 (originally 21)
    draw_sync.line(curve_points, fill=255, width=29, joint="curve")
    
    # Arrow lines width scaled up to 26 (originally 19)
    # Right arrow: M18 15L16.2 13.5 and M18 15L16.2 16.5
    draw_sync.line([transform(18, 15), transform(16.2, 13.5)], fill=255, width=26)
    draw_sync.line([transform(18, 15), transform(16.2, 16.5)], fill=255, width=26)
    
    # Left arrow: M6 15L7.8 16.5 and M6 15L7.8 13.5
    draw_sync.line([transform(6, 15), transform(7.8, 16.5)], fill=255, width=26)
    draw_sync.line([transform(6, 15), transform(7.8, 13.5)], fill=255, width=26)
    
    # Round caps for arrows endpoints scaled up to 13 (originally 9.5)
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
    
    output = io.BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    
    response = Response(output.read(), mimetype="image/png")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@manifest_bp.route("/logo.svg")
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
async def base_manifest():
    unconfigured_manifest = MANIFEST.copy()
    unconfigured_manifest["behaviorHints"] = {
        "configurable": True,
        "configurationRequired": True,
    }
    return await respond_with(unconfigured_manifest)


@manifest_bp.route("/<user_id>/manifest.json")
async def user_manifest(user_id: str):
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

    user_manifest_data = MANIFEST.copy()
    if not enable_catalogs and not enable_search:
        user_manifest_data["catalogs"] = []
        user_manifest_data["resources"] = ["subtitles"]
        return await respond_with(user_manifest_data)

    user_manifest_data["resources"] = ["subtitles", "catalog", "meta"]

    # Filter catalogs based on active integrations and custom selections
    mal_enabled = user.get("mal_access_token") and user.get("mal_enabled", True)
    anilist_enabled = user.get("anilist_token") and user.get("anilist_enabled", True)
    user_catalogs = user.get("catalogs")

    active_catalogs = []
    
    # 1. Add custom sorted catalogs first (if user has saved preferences)
    if user_catalogs is not None:
        for cat_id in user_catalogs:
            for cat in CATALOGS:
                if cat["id"] == cat_id:
                    # Apply visibility checks
                    if cat_id == "anime_tracker_search":
                        if not enable_search:
                            continue
                    else:
                        if not enable_catalogs:
                            continue
                        if cat_id.startswith("mal_") and not mal_enabled:
                            continue
                        if cat_id.startswith("anilist_") and not anilist_enabled:
                            continue
                    if cat not in active_catalogs:
                        active_catalogs.append(cat)
                
        # 2. Append any other CATALOGS that were not explicitly in the sorted user_catalogs (e.g. search catalogs)
        for cat in CATALOGS:
            if cat not in active_catalogs:
                cat_id = cat["id"]
                if cat_id == "anime_tracker_search":
                    if not enable_search:
                        continue
                else:
                    if not enable_catalogs:
                        continue
                    if cat_id.startswith("mal_") and not mal_enabled:
                        continue
                    if cat_id.startswith("anilist_") and not anilist_enabled:
                        continue
                    # Omit if the user explicitly unchecked it
                    if cat_id not in user_catalogs:
                        continue
                active_catalogs.append(cat)
    else:
        # Fallback to default catalog order if user has not customized them
        for cat in CATALOGS:
            cat_id = cat["id"]
            if cat_id == "anime_tracker_search":
                if not enable_search:
                    continue
            else:
                if not enable_catalogs:
                    continue
                if cat_id.startswith("mal_") and not mal_enabled:
                    continue
                if cat_id.startswith("anilist_") and not anilist_enabled:
                    continue
            active_catalogs.append(cat)

    user_manifest_data["catalogs"] = active_catalogs
    return await respond_with(user_manifest_data)




import os
from quart import Response, send_file


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
        transform(20, 18),
    ]
    mask_left = Image.new("L", (256, 256), 0)
    draw_left = ImageDraw.Draw(mask_left)
    draw_left.polygon(left_polygon, fill=255)

    # 5. Draw Right Polygon Mask
    right_polygon = [transform(12, 2), transform(16, 10), transform(8, 10)]
    mask_right = Image.new("L", (256, 256), 0)
    draw_right = ImageDraw.Draw(mask_right)
    draw_right.polygon(right_polygon, fill=255)

    # 6. Draw Sync Bridge Mask (Bezier Curve + Arrows)
    mask_sync = Image.new("L", (256, 256), 0)
    draw_sync = ImageDraw.Draw(mask_sync)

    curve_points = []
    for i in range(101):
        t = i / 100.0
        x_val = (1 - t) ** 3 * 6 + 3 * (1 - t) ** 2 * t * 8 + 3 * (1 - t) * t**2 * 16 + t**3 * 18
        y_val = (1 - t) ** 3 * 15 + 3 * (1 - t) ** 2 * t * 12.5 + 3 * (1 - t) * t**2 * 12.5 + t**3 * 15
        curve_points.append(transform(x_val, y_val))

    draw_sync.line(curve_points, fill=255, width=29, joint="curve")

    draw_sync.line([transform(18, 15), transform(16.2, 13.5)], fill=255, width=26)
    draw_sync.line([transform(18, 15), transform(16.2, 16.5)], fill=255, width=26)

    draw_sync.line([transform(6, 15), transform(7.8, 16.5)], fill=255, width=26)
    draw_sync.line([transform(6, 15), transform(7.8, 13.5)], fill=255, width=26)

    r_cap = 13.0
    endpoints = [transform(16.2, 13.5), transform(16.2, 16.5), transform(7.8, 16.5), transform(7.8, 13.5)]
    for pt in endpoints:
        draw_sync.ellipse([pt[0] - r_cap, pt[1] - r_cap, pt[0] + r_cap, pt[1] + r_cap], fill=255)

    # 7. Assemble final image with transparency
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    img.paste(grad_left_img, (0, 0), mask_left)
    img.paste(grad_right_img, (0, 0), mask_right)
    img.paste(grad_sync_img, (0, 0), mask_sync)

    # Save the file
    img.save(path, format="PNG")


async def handle_logo_png():
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    # Check project assets directory (located at src-repo/assets/logo.png)
    base_dir = os.path.dirname(os.path.dirname(curr_dir))
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


async def handle_serve_asset(filename: str):
    # Sanitize filename: prevent path traversal attacks (BUG #64)
    safe_filename = os.path.basename(filename)
    if safe_filename != filename or ".." in filename or "/" in filename or "\\" in filename:
        return "Asset not found", 404

    curr_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(curr_dir, "..", "assets", safe_filename),
        os.path.join(curr_dir, "..", "..", "assets", safe_filename),
        os.path.join(curr_dir, "..", "..", "..", "assets", safe_filename),
    ]
    for asset_path in candidates:
        abs_path = os.path.abspath(asset_path)
        # Verify the resolved path stays within an assets directory
        if os.path.exists(abs_path) and os.path.basename(os.path.dirname(abs_path)) == "assets":
            mimetype = (
                "image/svg+xml"
                if safe_filename.endswith(".svg")
                else ("image/png" if safe_filename.endswith(".png") else "image/jpeg")
            )
            response = await send_file(abs_path, mimetype=mimetype)
            response.headers["Cache-Control"] = "public, max-age=86400"
            return response
    return "Asset not found", 404


async def handle_logo_svg():
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

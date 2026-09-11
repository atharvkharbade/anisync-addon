import os
from quart import Response, send_file


def save_logo_to_path(path: str):
    """Ensure logo asset exists at path by copying master asset if available."""
    import shutil

    curr_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(curr_dir, "..", "..", "docs", "images", "logo.png"),
        os.path.join(curr_dir, "..", "assets", "logo.png"),
    ]
    for cand in candidates:
        cand_abs = os.path.abspath(cand)
        if os.path.exists(cand_abs) and cand_abs != os.path.abspath(path):
            shutil.copyfile(cand_abs, path)
            return



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


async def handle_favicon_ico():
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(os.path.dirname(curr_dir))
    ico_path = os.path.join(base_dir, "assets", "favicon.ico")
    if os.path.exists(ico_path):
        response = await send_file(ico_path, mimetype="image/x-icon")
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response
    return await handle_logo_png()


async def handle_apple_touch_icon():
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(os.path.dirname(curr_dir))
    icon_path = os.path.join(base_dir, "assets", "apple-touch-icon.png")
    if os.path.exists(icon_path):
        response = await send_file(icon_path, mimetype="image/png")
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response
    return await handle_favicon_ico()


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
    svg_content = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" fill="none">
  <defs>
    <!-- Cyan to Electric Blue Brand Gradient -->
    <linearGradient id="brand-grad-transparent" x1="256" y1="36" x2="256" y2="476" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#00f5ff"/>
      <stop offset="30%" stop-color="#22d3ee"/>
      <stop offset="65%" stop-color="#02a9ff"/>
      <stop offset="100%" stop-color="#0055ff"/>
    </linearGradient>

    <!--
      Scaled cut mask matching the exact -64° diagonal slice trajectory from IMAGE_3
      Transparent background: no container rect, only pure transparent negative-space cut
    -->
    <mask id="cut-transparent-mask">
      <rect width="512" height="512" fill="#ffffff"/>
      <g transform="translate(242, 274) rotate(-64)">
        <rect x="-400" y="-10" width="800" height="20" fill="#000000"/>
      </g>
    </mask>
  </defs>

  <!-- Big 'A' Mark with Transparent Background (identical geometry and scale to IMAGE_3) -->
  <g transform="translate(256, 264) scale(1.25) translate(-256, -264)" mask="url(#cut-transparent-mask)">
    <path d="M256 84 L424 424 H336 L298 338 H214 L176 424 H88 L256 84 Z M256 206 L224 278 H288 L256 206 Z"
          fill="url(#brand-grad-transparent)"
          fill-rule="evenodd"/>
  </g>
</svg>"""
    return Response(svg_content, mimetype="image/svg+xml")


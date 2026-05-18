import logging
import io
import urllib.parse
import httpx
from quart import Blueprint, abort, redirect, request, Response
from PIL import Image, ImageDraw

poster_bp = Blueprint("poster", __name__)


@poster_bp.route("/<user_id>/poster/<string:media_id>.jpg")
async def serve_modified_poster(user_id: str, media_id: str):
    """
    Serve a modified poster with a solid white indicator bar if a new episode has aired.
    """
    original_url = request.args.get("url")
    if not original_url:
        return abort(400)

    badge = request.args.get("badge")
    if badge != "new":
        return redirect(original_url)

    try:
        # Fetch the original poster image
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(original_url)
            if resp.status_code != 200:
                logging.warning("Failed to fetch original poster from CDN: %s (status %s)", original_url, resp.status_code)
                return redirect(original_url)

        # Load image into Pillow
        img = Image.open(io.BytesIO(resp.content))
        
        # Resize to standard Stremio catalog poster dimensions for perfect uniformity
        resample_filter = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        img = img.resize((225, 350), resample_filter)
        
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        # Draw a solid white bar covering the bottom 15% of the poster (from y=298 to y=350)
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)
        draw_overlay.rectangle([(0, 298), (225, 350)], fill=(255, 255, 255, 255))

        # Composite overlay onto the original image and convert back to RGB
        combined = Image.alpha_composite(img, overlay)
        final_img = combined.convert("RGB")

        # Output the modified image as JPEG
        output = io.BytesIO()
        final_img.save(output, format="JPEG", quality=85)
        output.seek(0)

        response = Response(output.read(), mimetype="image/jpeg")
        # Aggressive caching to minimize server workload (1 week cache)
        response.headers["Cache-Control"] = "public, max-age=604800"
        return response

    except Exception as e:
        logging.error("Pillow poster overlay failed for media_id %s: %s", media_id, e)
        return redirect(original_url)

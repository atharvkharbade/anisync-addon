import logging
import io
import urllib.parse
import os
from quart import Blueprint, abort, redirect, request, Response
from PIL import Image, ImageDraw, ImageFont

from app.routes.utils import is_valid_user_id, rate_limit
from app.services.http import get_client

poster_bp = Blueprint("poster", __name__)


@poster_bp.route("/<user_id>/poster/<string:media_id>.jpg")
@rate_limit(limit=120, period_seconds=60)
async def serve_modified_poster(user_id: str, media_id: str):
    """
    Serve a modified poster with a Premium Accent Overlay indicator if a new episode has aired.
    """
    if not is_valid_user_id(user_id):
        return "Invalid user ID", 400

    original_url = request.args.get("url")
    if not original_url:
        return abort(400)

    badge = request.args.get("badge")
    if badge != "new":
        # Redirect directly if not flagging a new episode to bypass processing completely
        return redirect(original_url)

    try:
        # Fetch the original poster image using pooled client
        client = get_client()
        resp = await client.get(original_url, timeout=8)
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

        w, h = img.size # w=225, h=350

        tracker = request.args.get("tracker", "").lower()

        # Create overlay image for transparent drawing
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Draw translucent black bar covering the bottom 10% (35px height, from y=315 to 350)
        bar_h = 35
        bar_y = h - bar_h # y=315
        draw.rectangle([(0, bar_y), (w, h)], fill=(0, 0, 0, 255)) # Solid black

        try:
            # Setup fonts
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            try:
                font = ImageFont.truetype(font_path, 12)
            except Exception:
                font = ImageFont.load_default()

            # Measure text "NEW EPISODE"
            text = "NEW EPISODE"
            try:
                left, top, right, bottom = font.getbbox(text)
                text_w = right - left
                text_h = bottom - top
            except Exception:
                text_w, text_h = 90, 10
                left, top = 0, 0

            # Setup tracker logo properties
            logo_w, logo_h = 16, 16
            logo_gap = 4
            text_gap = 6
            
            # Parse tracker parameter
            trackers = tracker.split("+")
            draw_mal = "mal" in trackers or "both" in trackers or "mal+anilist" in trackers
            draw_al = "anilist" in trackers or "both" in trackers or "mal+anilist" in trackers
            draw_simkl = "simkl" in trackers

            assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
            resample_filter = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS

            logos = []
            
            if draw_mal:
                logo_path = os.path.join(assets_dir, "mal_logo.png")
                if os.path.exists(logo_path):
                    try:
                        mal_logo_img = Image.open(logo_path).convert("RGBA").resize((logo_w, logo_h), resample_filter)
                        logos.append(mal_logo_img)
                    except Exception as e:
                        logging.error("Failed to load MAL logo image: %s", e)
            
            if draw_al:
                logo_path = os.path.join(assets_dir, "anilist_logo.png")
                if os.path.exists(logo_path):
                    try:
                        al_logo_img = Image.open(logo_path).convert("RGBA").resize((logo_w, logo_h), resample_filter)
                        logos.append(al_logo_img)
                    except Exception as e:
                        logging.error("Failed to load AniList logo image: %s", e)

            if draw_simkl:
                logo_path = os.path.join(assets_dir, "simkl_logo.png")
                if os.path.exists(logo_path):
                    try:
                        simkl_logo_img = Image.open(logo_path).convert("RGBA").resize((logo_w, logo_h), resample_filter)
                        logos.append(simkl_logo_img)
                    except Exception as e:
                        logging.error("Failed to load Simkl logo image: %s", e)

            # Calculate total width
            total_logos_w = len(logos) * logo_w + (len(logos) - 1) * logo_gap if logos else 0
            total_w = total_logos_w + text_gap + text_w if total_logos_w > 0 else text_w
            block_x = (w - total_w) / 2
            bar_center_y = bar_y + bar_h / 2

            # Paste logo(s)
            curr_x = block_x
            for logo in logos:
                overlay.paste(logo, (int(curr_x), int(bar_center_y - logo_h / 2)), logo)
                curr_x += logo_w + logo_gap
                
            text_x = block_x + total_logos_w + text_gap if total_logos_w > 0 else block_x

            # Draw text "NEW EPISODE"
            tx = text_x - left
            ty = bar_center_y - text_h / 2 - top
            draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))

            # Composite and convert
            combined = Image.alpha_composite(img, overlay)
            final_img = combined.convert("RGB")

        except Exception as ex:
            logging.error("Failed to dynamically draw overlay: %s. Falling back to solid white bar.", ex)
            # Fallback to drawing a solid, high-contrast white bar covering the bottom 10%
            draw.rectangle([(0, 315), (225, 350)], fill=(255, 255, 255, 255))
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

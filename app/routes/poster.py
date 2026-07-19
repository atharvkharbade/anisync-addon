import io
import logging
import os
import urllib.parse

from PIL import Image, ImageDraw, ImageFont
from quart import Blueprint, Response, abort, redirect, request

from app.routes.utils import is_valid_user_id, rate_limit
from app.services.http import get_client

poster_bp = Blueprint("poster", __name__)


def is_trusted_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        host = host.lower()
        trusted_domains = (
            "myanimelist.net",
            "anilist.co",
            "simkl.in",
            "simkl.com",
            "kitsu.io",
            "metahub.space",
            "ratingposterdb.com",
            "topposters.com",
            "tmdb.org",
            "thetvdb.com",
        )
        for domain in trusted_domains:
            if host == domain or host.endswith("." + domain):
                return True
        return False
    except Exception:
        return False


@poster_bp.route("/<user_id>/poster/<string:media_id>.jpg")
@rate_limit(limit=120, period_seconds=60)
async def serve_modified_poster(user_id: str, media_id: str):
    """
    Serve a modified poster with a Premium Accent Overlay indicator if a new episode has aired.
    """
    if not is_valid_user_id(user_id):
        return "Invalid user ID", 400

    original_url = request.args.get("url")
    if not original_url or not is_trusted_url(original_url):
        return abort(400)

    badge = request.args.get("badge")
    if badge != "new":
        # Redirect directly if not flagging a new episode to bypass processing completely
        return redirect(original_url)

    try:
        # Fetch the original poster image using pooled client
        client = get_client()
        resp = await client.get(original_url, timeout=8, follow_redirects=True)
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

        w, h = img.size  # w=225, h=350

        tracker = request.args.get("tracker", "").lower()
        badge_style = request.args.get("style", "classic").lower()

        # Create overlay image for transparent drawing
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        try:
            # Setup fonts
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            try:
                font = ImageFont.truetype(font_path, 12)
                font_small = ImageFont.truetype(font_path, 10)
            except Exception:
                font = ImageFont.load_default()
                font_small = font

            # Parse tracker parameter
            tracker_clean = tracker.replace(" ", "+").replace(",", "+")
            trackers = [t.strip() for t in tracker_clean.split("+") if t.strip()]
            draw_mal = "mal" in trackers or "both" in trackers
            draw_al = "anilist" in trackers or "both" in trackers
            draw_simkl = "simkl" in trackers

            assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
            resample_filter = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS

            logo_w, logo_h = 16, 16
            logo_gap = 4

            if badge_style == "modern":
                # --- MODERN DESIGN: Top Adaptive Pill + Bottom Tracker Indicators ---
                # 1. Color extraction from top 25% of image
                try:
                    top_crop = img.crop((0, 0, w, int(h * 0.25)))
                    quantized = top_crop.quantize(colors=3)
                    palette = quantized.getpalette()
                    if palette and len(palette) >= 3:
                        r, g, b = palette[0], palette[1], palette[2]
                        luminance = 0.299 * r + 0.587 * g + 0.114 * b
                        if luminance < 45:
                            r, g, b = max(r, 35), max(g, 55), max(b, 80)
                        elif luminance > 200:
                            r, g, b = int(r * 0.65), int(g * 0.65), int(b * 0.65)
                        pill_rgb = (r, g, b)
                    else:
                        pill_rgb = (30, 55, 80)
                except Exception:
                    pill_rgb = (30, 55, 80)

                pill_fill = (pill_rgb[0], pill_rgb[1], pill_rgb[2], 225)
                pill_outline = (255, 255, 255, 70)

                # Draw top rounded pill badge
                text_top = "NEW EPISODE"
                try:
                    left_t, top_t, right_t, bottom_t = font.getbbox(text_top)
                    tw = right_t - left_t
                    th = bottom_t - top_t
                except Exception:
                    tw, th = 85, 10
                    left_t, top_t = 0, 0

                pill_w = tw + 22
                pill_h = th + 12
                pill_x1 = (w - pill_w) / 2
                pill_y1 = 12
                pill_x2 = pill_x1 + pill_w
                pill_y2 = pill_y1 + pill_h

                if hasattr(draw, "rounded_rectangle"):
                    draw.rounded_rectangle([(pill_x1, pill_y1), (pill_x2, pill_y2)], radius=10, fill=pill_fill, outline=pill_outline)
                else:
                    draw.rectangle([(pill_x1, pill_y1), (pill_x2, pill_y2)], fill=pill_fill)

                tx = (pill_x1 + (pill_w - tw) / 2) - left_t
                ty = (pill_y1 + (pill_h - th) / 2) - top_t
                draw.text((tx, ty), text_top, font=font, fill=(255, 255, 255, 255))

                # 2. Bottom tracker indicators bar
                bar_h = 36
                bar_y = h - bar_h
                draw.rectangle([(0, bar_y), (w, h)], fill=(0, 0, 0, 215))

                logos = []
                tracker_names = []
                if draw_mal:
                    p = os.path.join(assets_dir, "mal_logo.png")
                    if os.path.exists(p):
                        logos.append(Image.open(p).convert("RGBA").resize((logo_w, logo_h), resample_filter))
                        tracker_names.append("MAL")
                if draw_al:
                    p = os.path.join(assets_dir, "anilist_logo.png")
                    if os.path.exists(p):
                        logos.append(Image.open(p).convert("RGBA").resize((logo_w, logo_h), resample_filter))
                        tracker_names.append("AniList")
                if draw_simkl:
                    p = os.path.join(assets_dir, "simkl_logo.png")
                    if os.path.exists(p):
                        logos.append(Image.open(p).convert("RGBA").resize((logo_w, logo_h), resample_filter))
                        tracker_names.append("Simkl")

                bot_text = " • ".join(tracker_names) if tracker_names else "NEW"
                try:
                    left_b, top_b, right_b, bottom_b = font_small.getbbox(bot_text)
                    btw = right_b - left_b
                    bth = bottom_b - top_b
                except Exception:
                    btw, bth = 50, 10
                    left_b, top_b = 0, 0

                total_logos_w = len(logos) * logo_w + (len(logos) - 1) * logo_gap if logos else 0
                total_bot_w = total_logos_w + 6 + btw if total_logos_w > 0 else btw

                bot_block_x = (w - total_bot_w) / 2
                bot_center_y = bar_y + bar_h / 2

                curr_x = bot_block_x
                for logo in logos:
                    overlay.paste(logo, (int(curr_x), int(bot_center_y - logo_h / 2)), logo)
                    curr_x += logo_w + logo_gap

                btx = (bot_block_x + total_logos_w + 6 if total_logos_w > 0 else bot_block_x) - left_b
                bty = bot_center_y - bth / 2 - top_b
                draw.text((btx, bty), bot_text, font=font_small, fill=(240, 240, 240, 255))

            else:
                # --- CLASSIC DESIGN: Solid Bottom Bar ---
                bar_h = 35
                bar_y = h - bar_h
                draw.rectangle([(0, bar_y), (w, h)], fill=(0, 0, 0, 255))

                text = "NEW EPISODE"
                try:
                    left, top, right, bottom = font.getbbox(text)
                    text_w = right - left
                    text_h = bottom - top
                except Exception:
                    text_w, text_h = 90, 10
                    left, top = 0, 0

                text_gap = 6
                logos = []

                if draw_mal:
                    p = os.path.join(assets_dir, "mal_logo.png")
                    if os.path.exists(p):
                        logos.append(Image.open(p).convert("RGBA").resize((logo_w, logo_h), resample_filter))
                if draw_al:
                    p = os.path.join(assets_dir, "anilist_logo.png")
                    if os.path.exists(p):
                        logos.append(Image.open(p).convert("RGBA").resize((logo_w, logo_h), resample_filter))
                if draw_simkl:
                    p = os.path.join(assets_dir, "simkl_logo.png")
                    if os.path.exists(p):
                        logos.append(Image.open(p).convert("RGBA").resize((logo_w, logo_h), resample_filter))

                total_logos_w = len(logos) * logo_w + (len(logos) - 1) * logo_gap if logos else 0
                total_w = total_logos_w + text_gap + text_w if total_logos_w > 0 else text_w
                block_x = (w - total_w) / 2
                bar_center_y = bar_y + bar_h / 2

                curr_x = block_x
                for logo in logos:
                    overlay.paste(logo, (int(curr_x), int(bar_center_y - logo_h / 2)), logo)
                    curr_x += logo_w + logo_gap

                text_x = block_x + total_logos_w + text_gap if total_logos_w > 0 else block_x
                tx = text_x - left
                ty = bar_center_y - text_h / 2 - top
                draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))

            # Composite and convert
            combined = Image.alpha_composite(img, overlay)
            final_img = combined.convert("RGB")

        except Exception as ex:
            logging.error("Failed to dynamically draw overlay: %s. Falling back to solid white bar.", ex)
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

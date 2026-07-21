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
        badge_style = request.args.get("style", "modern").lower()

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
                # --- MODERN DESIGN: Unified Poster Theme for Top & Bottom Adaptive Boxes ---
                # Primary color extraction from top 25% of image for poster theme
                try:
                    top_crop = img.crop((0, 0, w, int(h * 0.25)))
                    quantized = top_crop.quantize(colors=3)
                    palette = quantized.getpalette()
                    if palette and len(palette) >= 3:
                        r, g, b = palette[0], palette[1], palette[2]
                        box_rgb = (r, g, b)
                    else:
                        box_rgb = (30, 55, 80)
                except Exception:
                    box_rgb = (30, 55, 80)

                lum = 0.299 * box_rgb[0] + 0.587 * box_rgb[1] + 0.114 * box_rgb[2]
                if lum > 140:
                    text_color = (15, 15, 15, 255)
                    box_fill = (box_rgb[0], box_rgb[1], box_rgb[2], 240)
                    box_outline = (0, 0, 0, 90)
                else:
                    text_color = (255, 255, 255, 255)
                    box_fill = (max(box_rgb[0], 25), max(box_rgb[1], 40), max(box_rgb[2], 65), 235)
                    box_outline = (255, 255, 255, 90)

                # Setup enlarged font for top badge
                try:
                    font_top = ImageFont.truetype(font_path, 16)
                except Exception:
                    font_top = font

                # Draw top rounded box badge
                text_top = "NEW EPISODE"
                try:
                    left_t, top_t, right_t, bottom_t = font_top.getbbox(text_top)
                    tw = right_t - left_t
                    th = bottom_t - top_t
                except Exception:
                    tw, th = 115, 14
                    left_t, top_t = 0, 0

                box_w = tw + 28
                box_h = th + 14
                box_x1 = (w - box_w) / 2
                box_y1 = 10
                box_x2 = box_x1 + box_w
                box_y2 = box_y1 + box_h

                if hasattr(draw, "rounded_rectangle"):
                    draw.rounded_rectangle([(box_x1, box_y1), (box_x2, box_y2)], radius=6, fill=box_fill, outline=box_outline)
                else:
                    draw.rectangle([(box_x1, box_y1), (box_x2, box_y2)], fill=box_fill)

                tx = (box_x1 + (box_w - tw) / 2) - left_t
                ty = (box_y1 + (box_h - th) / 2) - top_t
                draw.text((tx, ty), text_top, font=font_top, fill=text_color)

                # 2. Bottom Tracker Names inside matching adaptive rounded box container
                try:
                    font_bottom = ImageFont.truetype(font_path, 15)
                except Exception:
                    font_bottom = font

                tracker_names = []
                if draw_mal:
                    tracker_names.append("MAL")
                if draw_al:
                    tracker_names.append("AL")
                if draw_simkl:
                    tracker_names.append("Simkl")

                bot_text = " • ".join(tracker_names) if tracker_names else "NEW EPISODE"

                try:
                    left_b, top_b, right_b, bottom_b = font_bottom.getbbox(bot_text)
                    btw = right_b - left_b
                    bth = bottom_b - top_b
                except Exception:
                    btw, bth = 70, 14
                    left_b, top_b = 0, 0

                bot_box_w = btw + 26
                bot_box_h = bth + 14
                bot_box_x1 = (w - bot_box_w) / 2
                bot_box_y1 = h - bot_box_h - 18
                bot_box_x2 = bot_box_x1 + bot_box_w
                bot_box_y2 = bot_box_y1 + bot_box_h

                if hasattr(draw, "rounded_rectangle"):
                    draw.rounded_rectangle([(bot_box_x1, bot_box_y1), (bot_box_x2, bot_box_y2)], radius=6, fill=box_fill, outline=box_outline)
                else:
                    draw.rectangle([(bot_box_x1, bot_box_y1), (bot_box_x2, bot_box_y2)], fill=box_fill)

                btx = (bot_box_x1 + (bot_box_w - btw) / 2) - left_b
                bty = (bot_box_y1 + (bot_box_h - bth) / 2) - top_b
                draw.text((btx, bty), bot_text, font=font_bottom, fill=text_color)

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

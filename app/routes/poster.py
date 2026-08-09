import io
import logging
import os
import urllib.parse

from PIL import Image, ImageDraw, ImageFilter, ImageFont
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
                # --- MODERN DESIGN: Liquid Glass Blur (Glassmorphism) & Symmetric Margins ---
                glass_fill = (15, 23, 42, 160)
                glass_outline = (255, 255, 255, 65)
                text_color = (255, 255, 255, 255)

                # Setup font for top badge
                try:
                    font_top = ImageFont.truetype(font_path, 15)
                except Exception:
                    font_top = font

                # 1. Draw top rounded liquid glass badge ("NEW EPISODE")
                text_top = "NEW EPISODE"
                try:
                    left_t, top_t, right_t, bottom_t = font_top.getbbox(text_top)
                    tw = right_t - left_t
                    th = bottom_t - top_t
                except Exception:
                    tw, th = 110, 14
                    left_t, top_t = 0, 0

                box_w = tw + 28
                box_h = th + 14
                box_x1 = int((w - box_w) / 2)
                box_y1 = 12
                box_x2 = int(box_x1 + box_w)
                box_y2 = int(box_y1 + box_h)
                radius = 8

                # Apply Gaussian Blur to poster background slice under top badge
                try:
                    crop_top = img.crop((box_x1, box_y1, box_x2, box_y2))
                    blur_top = crop_top.filter(ImageFilter.GaussianBlur(radius=10))
                    mask_top = Image.new("L", (box_w, box_h), 0)
                    mask_draw_t = ImageDraw.Draw(mask_top)
                    if hasattr(mask_draw_t, "rounded_rectangle"):
                        mask_draw_t.rounded_rectangle([(0, 0), (box_w, box_h)], radius=radius, fill=255)
                    else:
                        mask_draw_t.rectangle([(0, 0), (box_w, box_h)], fill=255)
                    img.paste(blur_top, (box_x1, box_y1), mask_top)
                except Exception:
                    pass

                # Draw liquid glass overlay and text
                if hasattr(draw, "rounded_rectangle"):
                    draw.rounded_rectangle([(box_x1, box_y1), (box_x2, box_y2)], radius=radius, fill=glass_fill, outline=glass_outline)
                else:
                    draw.rectangle([(box_x1, box_y1), (box_x2, box_y2)], fill=glass_fill)

                tx = (box_x1 + (box_w - tw) / 2) - left_t
                ty = (box_y1 + (box_h - th) / 2) - top_t
                draw.text((tx, ty), text_top, font=font_top, fill=text_color)

                # 2. Bottom Tracker Badge inside matching liquid glass container
                try:
                    font_bottom = ImageFont.truetype(font_path, 14)
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
                    btw, bth = 65, 14
                    left_b, top_b = 0, 0

                bot_box_w = btw + 26
                bot_box_h = bth + 14
                bot_box_x1 = int((w - bot_box_w) / 2)
                bot_box_y1 = int(h - bot_box_h - 12)
                bot_box_x2 = int(bot_box_x1 + bot_box_w)
                bot_box_y2 = int(bot_box_y1 + bot_box_h)

                # Apply Gaussian Blur to poster background slice under bottom badge
                try:
                    crop_bot = img.crop((bot_box_x1, bot_box_y1, bot_box_x2, bot_box_y2))
                    blur_bot = crop_bot.filter(ImageFilter.GaussianBlur(radius=10))
                    mask_bot = Image.new("L", (bot_box_w, bot_box_h), 0)
                    mask_draw_b = ImageDraw.Draw(mask_bot)
                    if hasattr(mask_draw_b, "rounded_rectangle"):
                        mask_draw_b.rounded_rectangle([(0, 0), (bot_box_w, bot_box_h)], radius=radius, fill=255)
                    else:
                        mask_draw_b.rectangle([(0, 0), (bot_box_w, bot_box_h)], fill=255)
                    img.paste(blur_bot, (bot_box_x1, bot_box_y1), mask_bot)
                except Exception:
                    pass

                # Draw liquid glass overlay and text
                if hasattr(draw, "rounded_rectangle"):
                    draw.rounded_rectangle([(bot_box_x1, bot_box_y1), (bot_box_x2, bot_box_y2)], radius=radius, fill=glass_fill, outline=glass_outline)
                else:
                    draw.rectangle([(bot_box_x1, bot_box_y1), (bot_box_x2, bot_box_y2)], fill=glass_fill)

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
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    except Exception as e:
        logging.error("Pillow poster overlay failed for media_id %s: %s", media_id, e)
        return redirect(original_url)


@poster_bp.route("/<user_id>/background/<string:media_id>.jpg")
@rate_limit(limit=120, period_seconds=60)
async def serve_framed_background(user_id: str, media_id: str):
    """
    Process wide header banners (e.g. from AniList) into a native 16:9 1920x1080 canvas.
    Center-frames the banner and fills top/bottom with a blurred background palette
    so no characters or details get cut off by client-side cropping.
    """
    if not is_valid_user_id(user_id):
        return "Invalid user ID", 400

    original_url = request.args.get("url")
    if not original_url or not is_trusted_url(original_url):
        return abort(400)

    try:
        from PIL import ImageFilter

        client = get_client()
        resp = await client.get(original_url, timeout=10)
        if resp.status_code != 200:
            return redirect(original_url)

        banner_img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        bw, bh = banner_img.size
        aspect_ratio = bw / float(bh)

        # If already a standard landscape image (aspect ratio <= 2.0), serve directly
        if aspect_ratio <= 2.0:
            return redirect(original_url)

        target_w, target_h = 1920, 1080

        # Scale height to 1080 and crop middle 1920 width to form a full-bleed 16:9 background
        scale = target_h / float(bh)
        new_w = int(bw * scale)
        new_h = target_h

        scaled_banner = banner_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        crop_x = (new_w - target_w) // 2
        final_img = scaled_banner.crop((crop_x, 0, crop_x + target_w, target_h))

        # Export JPEG
        output = io.BytesIO()
        final_img.save(output, format="JPEG", quality=90)
        output.seek(0)

        response = Response(output.read(), mimetype="image/jpeg")
        response.headers["Cache-Control"] = "public, max-age=604800"
        return response

    except Exception as e:
        logging.error("Framed background processing failed for %s: %s", media_id, e)
        return redirect(original_url)


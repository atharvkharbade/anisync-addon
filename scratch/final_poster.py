import logging
import io
import urllib.parse
import httpx
import asyncio
from quart import Blueprint, abort, redirect, request, Response
from PIL import Image, ImageDraw, ImageFont

poster_bp = Blueprint("poster", __name__)


@poster_bp.route("/<user_id>/poster/<string:media_id>.jpg")
async def serve_modified_poster(user_id: str, media_id: str):
    """
    Serve a modified poster with a Premium Accent Overlay indicator if a new episode has aired.
    """
    original_url = request.args.get("url")
    if not original_url:
        return abort(400)

    badge = request.args.get("badge")
    if badge != "new":
        # Redirect directly if not flagging a new episode to bypass processing completely
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

        w, h = img.size # w=225, h=350

        # Open and apply our custom starry banner overlay at the bottom 15% (exactly 52px high)
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        overlay_path = os.path.join(base_dir, "assets", "new_episode_overlay.png")
        
        try:
            overlay_img = Image.open(overlay_path)
            resample_filter = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            
            overlay_w = 225
            overlay_h = 52
            overlay_y = 350 - overlay_h # y=298
            
            # 1. Crop and stretch a text-free starry background (from x=0 to x=200)
            # to act as a seamless background covering the full width of the poster
            bg_crop = overlay_img.crop((0, 0, 200, 122))
            bg_resized = bg_crop.resize((overlay_w, overlay_h), resample_filter)
            
            # 2. Crop the text 'New Episode' (from x=225 to x=794, width 569, height 122)
            # to scale it proportionally and prevent any horizontal stretching or squishing
            text_crop = overlay_img.crop((225, 0, 794, 122))
            src_text_w = 569
            src_text_h = 122
            
            # 3. Scale text proportionally to fit inside a bounding box (max_w=209, max_h=46)
            # This prevents the text from overflowing the poster horizontally while maintaining natural proportions
            max_text_w = overlay_w - 16 # 209px (8px padding left/right)
            max_text_h = overlay_h - 6  # 46px (3px padding top/bottom)
            
            scale_factor = min(max_text_w / src_text_w, max_text_h / src_text_h)
            text_w = int(src_text_w * scale_factor) # ~209px
            text_h = int(src_text_h * scale_factor) # ~44px
            
            text_resized = text_crop.resize((text_w, text_h), resample_filter)
            
            # 4. Paste text centered horizontally and vertically on the starry background bar
            paste_x = (overlay_w - text_w) // 2 # (225 - 209) // 2 = 8px
            paste_y = (overlay_h - text_h) // 2 # (52 - 44) // 2 = 4px
            bg_resized.paste(text_resized, (paste_x, paste_y), text_resized if text_resized.mode == "RGBA" else None)
            
            # Paste the composite starry overlay onto the bottom of the 225x350 poster (starting at y=298)
            img.paste(bg_resized, (0, overlay_y), bg_resized if bg_resized.mode == "RGBA" else None)
            final_img = img.convert("RGB")
            
        except Exception as ex:
            logging.error("Failed to apply custom overlay image: %s. Falling back to solid white bar.", ex)
            # Fallback to drawing a solid, high-contrast white bar covering the bottom 15% (Option 1)
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw_overlay = ImageDraw.Draw(overlay)
            draw_overlay.rectangle([(0, 298), (225, 350)], fill=(255, 255, 255, 255))
            
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

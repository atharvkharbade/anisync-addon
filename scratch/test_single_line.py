import io
import httpx
from PIL import Image

def generate_single_line_poster():
    # 1. Fetch original poster
    poster_url = "https://cdn.myanimelist.net/images/anime/1726/155542l.jpg"
    resp = httpx.get(poster_url)
    poster = Image.open(io.BytesIO(resp.content))
    poster = poster.resize((225, 350), Image.Resampling.LANCZOS)
    
    # 2. Open overlay asset
    overlay_path = "/app/app/assets/new_episode_overlay.png"
    overlay_img = Image.open(overlay_path)
    
    overlay_w = 225
    overlay_h = 52
    overlay_y = 350 - overlay_h # y=298
    
    # --- STEP 1: Crop and stretch a text-free starry background (from x=0 to x=200, full height 122) ---
    bg_crop = overlay_img.crop((0, 0, 200, 122))
    bg_resized = bg_crop.resize((overlay_w, overlay_h), Image.Resampling.LANCZOS)
    
    # --- STEP 2: Crop the text 'New Episode' starting from y=15 to completely exclude the top white line ---
    # Text region goes from x=225 to x=794 (width 569), and y=15 to y=122 (height 107)
    text_crop = overlay_img.crop((225, 15, 794, 122))
    src_text_w = 569
    src_text_h = 107
    
    # --- STEP 3: Scale text proportionally to fit inside a bounding box (max_w=209, max_h=42) ---
    max_text_w = overlay_w - 16 # 209px (8px padding left/right)
    max_text_h = overlay_h - 10 # 42px (5px padding top/bottom)
    
    scale_factor = min(max_text_w / src_text_w, max_text_h / src_text_h)
    text_w = int(src_text_w * scale_factor) # ~209px
    text_h = int(src_text_h * scale_factor) # ~39px
    
    text_resized = text_crop.resize((text_w, text_h), Image.Resampling.LANCZOS)
    
    # --- STEP 4: Paste centered horizontally and vertically on the starry background bar ---
    paste_x = (overlay_w - text_w) // 2 # (225 - 209) // 2 = 8px
    # Since text_crop starts from y=15, we center it vertically inside the 52px high bar
    paste_y = (overlay_h - text_h) // 2 # (52 - 39) // 2 = 6px
    bg_resized.paste(text_resized, (paste_x, paste_y), text_resized if text_resized.mode == "RGBA" else None)
    
    # --- STEP 5: Paste onto bottom of the poster ---
    poster.paste(bg_resized, (0, overlay_y), bg_resized if bg_resized.mode == "RGBA" else None)
    
    poster.save("/app/scratch_poster_single_line.png", format="PNG")
    print(f"Single line poster generated. Text: {text_w}x{text_h}, Pasted at: ({paste_x}, {paste_y})")

if __name__ == "__main__":
    generate_single_line_poster()

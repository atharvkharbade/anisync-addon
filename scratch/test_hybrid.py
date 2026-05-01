import io
import httpx
from PIL import Image

def generate_hybrid_poster():
    # 1. Fetch the original poster
    poster_url = "https://cdn.myanimelist.net/images/anime/1726/155542l.jpg"
    resp = httpx.get(poster_url)
    poster = Image.open(io.BytesIO(resp.content))
    poster = poster.resize((225, 350), Image.Resampling.LANCZOS)
    
    # 2. Open the custom overlay image
    overlay_path = "/app/app/assets/new_episode_overlay.png"
    overlay_img = Image.open(overlay_path)
    
    # --- STEP 1: Crop and stretch a text-free starry background (from x=0 to x=200) ---
    bg_crop = overlay_img.crop((0, 0, 200, 122))
    bg_resized = bg_crop.resize((225, 35), Image.Resampling.LANCZOS)
    
    # --- STEP 2: Crop the text 'New Episode' (from x=225 to x=794) ---
    text_crop = overlay_img.crop((225, 0, 794, 122))
    
    # --- STEP 3: Scale text proportionally to height 35px ---
    text_aspect = 569 / 122
    text_w = int(35 * text_aspect) # 35 * 4.6639 = 163px
    text_resized = text_crop.resize((text_w, 35), Image.Resampling.LANCZOS)
    
    # --- STEP 4: Paste centered horizontally onto the backdrop ---
    paste_x = (225 - text_w) // 2 # (225 - 163) // 2 = 31px
    bg_resized.paste(text_resized, (paste_x, 0), text_resized if text_resized.mode == "RGBA" else None)
    
    # --- STEP 5: Paste onto bottom of the poster (starting at y=315) ---
    poster.paste(bg_resized, (0, 315), bg_resized if bg_resized.mode == "RGBA" else None)
    
    poster.save("/app/scratch_poster_hybrid.png", format="PNG")
    print("Hybrid unstretched poster generated successfully.")

if __name__ == "__main__":
    generate_hybrid_poster()

import io
import httpx
from PIL import Image

def generate_unstretched_poster():
    poster_url = "https://cdn.myanimelist.net/images/anime/1726/155542l.jpg"
    resp = httpx.get(poster_url)
    poster = Image.open(io.BytesIO(resp.content))
    poster = poster.resize((225, 350), Image.Resampling.LANCZOS)
    
    overlay_path = "/app/app/assets/new_episode_overlay.png"
    overlay_img = Image.open(overlay_path)
    
    # Crop symmetric text region (590x122)
    crop_w = 590
    crop_h = 122
    text_center_x = 510
    
    left = text_center_x - crop_w // 2
    top = 0
    right = left + crop_w
    bottom = crop_h
    
    cropped_overlay = overlay_img.crop((left, top, right, bottom))
    
    # Calculate aspect-correct dimensions to fit height 35px exactly
    aspect = crop_w / crop_h
    new_h = 35
    new_w = int(new_h * aspect) # ~169px
    
    overlay_resized = cropped_overlay.resize((new_w, new_h), Image.Resampling.LANCZOS)
    
    # Create the transparent canvas of size 225x35
    canvas = Image.new("RGBA", (225, 35), (0, 0, 0, 0))
    
    # Paste centered horizontally
    paste_x = (225 - new_w) // 2
    canvas.paste(overlay_resized, (paste_x, 0), overlay_resized if overlay_resized.mode == "RGBA" else None)
    
    # Paste onto bottom of poster (starting at y=315)
    poster.paste(canvas, (0, 315), canvas)
    
    poster.save("/app/scratch_poster_unstretched.png", format="PNG")
    print("Unstretched poster generated successfully.")

if __name__ == "__main__":
    generate_unstretched_poster()

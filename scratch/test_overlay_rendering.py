import io
import httpx
from PIL import Image

def generate_test_overlays():
    # 1. Fetch the original poster
    poster_url = "https://cdn.myanimelist.net/images/anime/1726/155542l.jpg"
    resp = httpx.get(poster_url)
    poster = Image.open(io.BytesIO(resp.content))
    poster = poster.resize((225, 350), Image.Resampling.LANCZOS)
    
    # 2. Open the custom overlay image
    overlay_path = "/app/app/assets/new_episode_overlay.png"
    overlay_img = Image.open(overlay_path)
    
    # --- METHOD 1: Direct Stretch ---
    poster_stretched = poster.copy()
    overlay_stretched = overlay_img.resize((225, 70), Image.Resampling.LANCZOS)
    poster_stretched.paste(overlay_stretched, (0, 280), overlay_stretched if overlay_stretched.mode == "RGBA" else None)
    poster_stretched.save("/app/scratch_poster_stretched.png", format="PNG")
    print("Stretched poster generated.")
    
    # --- METHOD 2: Adaptive Centered Crop ---
    # We want to crop a portion from the center of the 1024x122 overlay that contains the text
    # Let's inspect the width of the text block by finding non-black/non-transparent pixels
    # Or we can crop a slightly wider box (e.g. 700x122) to ensure the text is fully preserved,
    # and pad/resize it.
    # Let's try cropping a 640x122 box centered horizontally (leaves 192px on left/right),
    # which is aspect ratio 5.24, and then resizing it to 225x70. This will have very minimal horizontal compression!
    poster_cropped = poster.copy()
    w_src, h_src = overlay_img.size # 1024x122
    crop_w = 640
    crop_h = 122
    
    left = (w_src - crop_w) // 2
    top = 0
    right = left + crop_w
    bottom = crop_h
    
    cropped_overlay = overlay_img.crop((left, top, right, bottom))
    overlay_resized = cropped_overlay.resize((225, 70), Image.Resampling.LANCZOS)
    poster_cropped.paste(overlay_resized, (0, 280), overlay_resized if overlay_resized.mode == "RGBA" else None)
    poster_cropped.save("/app/scratch_poster_cropped.png", format="PNG")
    print("Cropped poster generated.")

if __name__ == "__main__":
    generate_test_overlays()

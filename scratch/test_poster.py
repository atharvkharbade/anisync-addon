import io
import urllib.parse
import httpx
from PIL import Image, ImageDraw, ImageFont

def generate_test_poster():
    original_url = "https://cdn.myanimelist.net/images/anime/1726/155542l.jpg"
    resp = httpx.get(original_url)
    img = Image.open(io.BytesIO(resp.content))
    
    img = img.resize((225, 350), Image.Resampling.LANCZOS)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
        
    w, h = img.size
    overlay_h = 70
    overlay_y = 350 - overlay_h
    
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    
    # Draw dark semi-transparent bar
    draw_overlay.rectangle([(0, overlay_y), (w, h)], fill=(0, 0, 0, 185))
    
    # Draw thin glowing white accent border
    border_thickness = 3
    draw_overlay.rectangle([(0, overlay_y), (w, overlay_y + border_thickness - 1)], fill=(255, 255, 255, 255))
    
    combined = Image.alpha_composite(img, overlay)
    final_img = combined.convert("RGB")
    
    draw = ImageDraw.Draw(final_img)
    text = "NEW"
    font_size = 38
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
        
    center_x = w // 2
    center_y = overlay_y + border_thickness + (overlay_h - border_thickness) // 2
    
    draw.text((center_x, center_y), text, fill=(255, 255, 255), font=font, anchor="mm")
    
    final_img.save("/app/test_poster_out.png", format="PNG")
    print("Poster saved successfully.")

if __name__ == "__main__":
    generate_test_poster()

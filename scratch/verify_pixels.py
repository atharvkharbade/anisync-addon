from PIL import Image

def verify_pixels():
    img_path = "/app/test_poster_out.png"
    img = Image.open(img_path)
    w, h = img.size
    print(f"Image Dimensions: {w}x{h}")
    assert w == 225 and h == 350, f"Expected 225x350, got {w}x{h}"
    
    # Check pixels at y=280, 281, 282 (the white accent border)
    # The border color is solid white: (255, 255, 255)
    for y in [280, 281, 282]:
        for x in [10, 50, 100, 200]:
            pixel = img.getpixel((x, y))
            # Since JPEG compression is not active on PNG, it should be exactly (255, 255, 255)
            assert pixel == (255, 255, 255), f"Pixel at ({x}, {y}) was expected to be white, got {pixel}"
            
    print("Verification successful: The 20% overlay bar starts at y=280 with a perfect 3px white top accent!")

if __name__ == "__main__":
    verify_pixels()

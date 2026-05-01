from PIL import Image

def analyze_screenshot():
    img_path = "/app/screenshot.jpg"
    try:
        img = Image.open(img_path)
        w, h = img.size
        print(f"Screenshot dimensions: {w}x{h}")
    except Exception as e:
        print("Error opening image:", e)

if __name__ == "__main__":
    analyze_screenshot()

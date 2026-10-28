"""
Quick sample image generator for instant testing of GemmaTube.
Creates realistic color gradients and shapes in media/sample_photos/.
"""

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def create_sample_images():
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("[!] Pillow is not installed. Please run install.bat first.")
        return

    out_dir = Path("./media/sample_photos").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    samples = [
        {"name": "sunset_beach.jpg", "color1": (255, 94, 77), "color2": (255, 184, 140), "title": "Golden Sunset at the Beach"},
        {"name": "deep_ocean_waves.jpg", "color1": (33, 147, 176), "color2": (109, 213, 250), "title": "Deep Blue Ocean Waves"},
        {"name": "emerald_pine_forest.jpg", "color1": (19, 78, 94), "color2": (113, 178, 128), "title": "Emerald Green Pine Forest"},
        {"name": "neon_night_city.jpg", "color1": (131, 58, 180), "color2": (253, 29, 29), "title": "Neon Cyberpunk Night City"},
        {"name": "invoice_receipt_doc.png", "color1": (245, 245, 245), "color2": (230, 230, 230), "title": "Invoice Tax Receipt Document"},
        {"name": "red_sports_car.jpg", "color1": (186, 24, 27), "color2": (229, 56, 59), "title": "Glossy Red Sports Car"},
    ]

    print(f"[*] Generating {len(samples)} sample images in {out_dir}...")

    for item in samples:
        img = Image.new("RGB", (640, 360), color=item["color1"])
        draw = ImageDraw.Draw(img)

        # Draw diagonal gradient
        for y in range(360):
            ratio = y / 360.0
            r = int(item["color1"][0] * (1 - ratio) + item["color2"][0] * ratio)
            g = int(item["color1"][1] * (1 - ratio) + item["color2"][1] * ratio)
            b = int(item["color1"][2] * (1 - ratio) + item["color2"][2] * ratio)
            draw.line([(0, y), (640, y)], fill=(r, g, b))

        # Draw a decorative center circle or banner
        draw.ellipse([270, 130, 370, 230], outline=(255, 255, 255, 128), width=3)

        file_path = out_dir / item["name"]
        img.save(str(file_path), quality=90)
        print(f"    [+] Created: {item['name']}")

    print(f"\n[✓] Sample photos created successfully! Path to index:")
    print(f"    {out_dir}")

if __name__ == "__main__":
    create_sample_images()

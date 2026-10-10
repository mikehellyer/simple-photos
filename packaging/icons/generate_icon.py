"""One-off script: draws the Simple-Photos app icon and exports every
size/format the platform installers need. Not part of the app itself and
not run automatically — re-run manually if the icon design ever changes.

Usage: venv/bin/python packaging/icons/generate_icon.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = Path(__file__).parent
APP_RESOURCES = OUT_DIR.parent.parent / "src" / "simple_photos" / "gui" / "resources"
SIZE = 1024

# "Snapshot" — a slightly tilted instant print of a sunny hillside, on a
# deep indigo tile, so it sits beside Simple-Jukebox's plum and
# Simple-Ripper's teal.
INDIGO = (40, 52, 128, 255)
PAPER = (250, 248, 242, 255)
SKY_TOP = (110, 180, 236, 255)
SKY_BOTTOM = (178, 220, 248, 255)
SUN = (255, 196, 61, 255)
HILL_BACK = (88, 160, 92, 255)
HILL_FRONT = (52, 122, 70, 255)


def draw_photo(width: int, height: int) -> Image.Image:
    """The print itself, upright: white border, picture inside."""
    card = Image.new("RGBA", (width, height), PAPER)
    border = int(width * 0.07)
    pic_w, pic_h = width - 2 * border, int(height * 0.74) - border
    picture = Image.new("RGBA", (pic_w, pic_h))
    draw = ImageDraw.Draw(picture)
    for y in range(pic_h):
        t = y / pic_h
        colour = tuple(int(SKY_TOP[i] + (SKY_BOTTOM[i] - SKY_TOP[i]) * t) for i in range(3)) + (255,)
        draw.line([(0, y), (pic_w, y)], fill=colour)
    sun_r = pic_w * 0.12
    sx, sy = pic_w * 0.72, pic_h * 0.30
    draw.ellipse([sx - sun_r, sy - sun_r, sx + sun_r, sy + sun_r], fill=SUN)
    draw.ellipse([-pic_w * 0.35, pic_h * 0.55, pic_w * 0.85, pic_h * 1.6], fill=HILL_BACK)
    draw.ellipse([pic_w * 0.25, pic_h * 0.68, pic_w * 1.5, pic_h * 1.7], fill=HILL_FRONT)
    card.paste(picture, (border, border))
    return card


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = SIZE * 0.06
    draw.rounded_rectangle([margin, margin, SIZE - margin, SIZE - margin], radius=SIZE * 0.22, fill=INDIGO)

    card = draw_photo(int(SIZE * 0.56), int(SIZE * 0.64)).rotate(-8, resample=Image.BICUBIC, expand=True)
    # A soft shadow under the print.
    shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 110), mask=card.getchannel("A"))
    shadow = shadow.filter(ImageFilter.GaussianBlur(SIZE * 0.02))
    x = (SIZE - card.width) // 2
    y = (SIZE - card.height) // 2
    img.alpha_composite(shadow, (x + int(SIZE * 0.015), y + int(SIZE * 0.03)))
    img.alpha_composite(card, (x, y))
    return img


def main() -> None:
    icon = draw_icon()
    master_png = OUT_DIR / "icon.png"
    icon.save(master_png)
    print(f"wrote {master_png}")

    APP_RESOURCES.mkdir(parents=True, exist_ok=True)
    icon.resize((256, 256), Image.LANCZOS).save(APP_RESOURCES / "icon.png")
    print(f"wrote {APP_RESOURCES / 'icon.png'}")

    # Windows .ico (multi-resolution)
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    ico_path = OUT_DIR / "icon.ico"
    icon.save(ico_path, sizes=[(s, s) for s in ico_sizes])
    print(f"wrote {ico_path}")

    # Linux: a few common hicolor sizes as plain PNGs.
    for s in (16, 32, 48, 64, 128, 256, 512):
        icon.resize((s, s), Image.LANCZOS).save(OUT_DIR / f"icon_{s}.png")
    print("wrote icon_16..512.png")

    # macOS .icns: iconutil on a Mac; elsewhere Pillow's own ICNS writer is
    # good enough to commit, so CI on macOS always has a file to use.
    if sys.platform == "darwin" and shutil.which("iconutil"):
        iconset = OUT_DIR / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        for s in (16, 32, 128, 256, 512):
            icon.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
            icon.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT_DIR / "icon.icns")],
            check=True,
        )
        for f in iconset.iterdir():
            f.unlink()
        iconset.rmdir()
    else:
        icon.save(OUT_DIR / "icon.icns")
    print(f"wrote {OUT_DIR / 'icon.icns'}")


if __name__ == "__main__":
    main()

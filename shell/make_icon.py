"""Draws the app icon, a lightning bolt on a storm-blue square, as a macOS .iconset folder.

Run with the backend's Python, which has Pillow:  python shell/make_icon.py build/AppIcon.iconset
then `iconutil -c icns` turns the folder into AppIcon.icns (the Makefile does both).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# The bolt in a unit square; ui/src/brand.tsx and ui/public/favicon.svg draw the same shape.
BOLT = [(0.52, 0.06), (0.28, 0.55), (0.47, 0.55), (0.36, 0.95), (0.73, 0.43), (0.54, 0.43), (0.74, 0.06)]
SKY_TOP, SKY_BOTTOM = (40, 56, 120), (14, 20, 52)
BOLT_TOP, BOLT_BOTTOM = (255, 226, 102), (255, 170, 0)
# Pillow draws shapes without antialiasing, so the icon is drawn this many times larger and scaled down.
OVERSAMPLE = 4
SIZES = [16, 32, 128, 256, 512]


def gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    column = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / (size - 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    return column.resize((size, size))


def draw(px: int = 1024) -> Image.Image:
    s = px * OVERSAMPLE
    # Apple's icon grid: an 824-of-1024 body, centred, with corners about 22% of its side.
    inset = round(s * 100 / 1024)
    body = (inset, inset, s - inset, s - inset)
    radius = round((s - 2 * inset) * 0.2237)
    icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    drop = round(s * 0.012)
    ImageDraw.Draw(shadow).rounded_rectangle((body[0], body[1] + drop, body[2], body[3] + drop), radius, fill=(0, 0, 0, 110))
    icon.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(s * 0.012)))

    body_mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(body_mask).rounded_rectangle(body, radius, fill=255)
    icon.paste(gradient(s, SKY_TOP, SKY_BOTTOM), (0, 0), body_mask)

    side = (s - 2 * inset) * 0.66
    offset = (s - side) / 2
    bolt_mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(bolt_mask).polygon([(offset + x * side, offset + y * side) for x, y in BOLT], fill=255)
    # A still, soft halo behind the bolt.
    halo = Image.new("RGBA", (s, s), (255, 196, 60, 0))
    halo.putalpha(bolt_mask.filter(ImageFilter.GaussianBlur(s * 0.03)).point(lambda v: v * 0.45))
    icon.alpha_composite(halo)
    icon.paste(gradient(s, BOLT_TOP, BOLT_BOTTOM), (0, 0), bolt_mask)
    return icon.resize((px, px), Image.LANCZOS)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "AppIcon.iconset")
    out.mkdir(parents=True, exist_ok=True)
    master = draw()
    for size in SIZES:
        master.resize((size, size), Image.LANCZOS).save(out / f"icon_{size}x{size}.png")
        master.resize((size * 2, size * 2), Image.LANCZOS).save(out / f"icon_{size}x{size}@2x.png")


if __name__ == "__main__":
    main()

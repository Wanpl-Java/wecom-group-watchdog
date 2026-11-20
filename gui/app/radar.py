"""Decorative radar artwork for the duty dashboard."""
from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageColor, ImageDraw, ImageFilter


@lru_cache(maxsize=2)
def radar_art(dark: bool) -> Image.Image:
    size = (740, 280)
    base, glow, grid, orbit, signal = (
        ('#171440', '#5634A4', '#7764B7', '#9D85FF', '#65F6ED') if dark
        else ('#E2EAFF', '#99ADFC', '#8C9ACA', '#6046C5', '#087E94')
    )
    art = Image.new('RGBA', size, base)
    mask = Image.new('L', size)
    ImageDraw.Draw(mask).ellipse((390, -105, 770, 370), fill=165)
    haze = Image.new('RGBA', size, glow)
    haze.putalpha(mask.filter(ImageFilter.GaussianBlur(64)))
    art = Image.alpha_composite(art, haze)
    overlay = Image.new('RGBA', size)
    ink = ImageDraw.Draw(overlay)
    cx, cy = 522, 140

    def tint(color: str, alpha: int) -> tuple[int, int, int, int]:
        return (*ImageColor.getrgb(color), alpha)

    for x in range(6, size[0], 32):
        ink.line((x, 0, x, size[1]), fill=tint(grid, 38), width=1)
    for y in range(12, size[1], 32):
        ink.line((0, y, size[0], y), fill=tint(grid, 38), width=1)
    for radius in (42, 74, 106, 128):
        bounds = (cx - radius, cy - radius, cx + radius, cy + radius)
        ink.ellipse(bounds, outline=tint(orbit, 135 if radius == 128 else 88), width=2)
    ink.line((cx - 144, cy, cx + 144, cy), fill=tint(grid, 132))
    ink.line((cx, cy - 135, cx, cy + 135), fill=tint(grid, 132))
    for angle in range(195, 300):
        alpha = round((angle - 194) * .67)
        ink.pieslice((cx - 106, cy - 106, cx + 106, cy + 106), angle, angle + 1,
                     fill=tint(signal, alpha))
    for angle in range(0, 360, 10):
        a = math.radians(angle)
        inner = 119 if angle % 30 == 0 else 123
        ink.line((cx + inner * math.cos(a), cy + inner * math.sin(a),
                  cx + 128 * math.cos(a), cy + 128 * math.sin(a)), fill=tint(orbit, 178), width=2)
    for x, y in ((cx, cy), (470, 82), (591, 176), (556, 51)):
        ink.ellipse((x - 12, y - 12, x + 12, y + 12), outline=tint(signal, 80), width=2)
        ink.ellipse((x - 4, y - 4, x + 4, y + 4), fill=tint(signal, 255))
    for points in (((591, 176), (644, 206), (706, 206)), ((470, 82), (436, 56), (350, 56)),
                   ((556, 51), (604, 22), (690, 22))):
        ink.line(points, fill=tint(orbit, 130), width=2)
    return Image.alpha_composite(art, overlay).convert('RGB')

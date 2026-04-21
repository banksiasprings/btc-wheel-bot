#!/usr/bin/env python3
"""Generate PWA app icons for BTC Wheel Bot."""

import math
from PIL import Image, ImageDraw

def p(v):
    """Convert float coordinate to int."""
    if isinstance(v, (list, tuple)):
        return [int(x) for x in v]
    return int(v)

def draw_btc_symbol(draw, cx, cy, size, color):
    lw = max(2, int(size // 14))
    cx, cy = int(cx), int(cy)

    stem_x = int(cx - size * 0.18)
    top_y  = int(cy - size * 0.38)
    bot_y  = int(cy + size * 0.38)
    mid_y  = int(cy - size * 0.02)

    # Vertical stem
    draw.line([stem_x, top_y, stem_x, bot_y], fill=color, width=lw)

    # Upper bump
    br1 = int(cx + size * 0.20)
    draw.line([stem_x, top_y,           br1 - int(size*0.06), top_y],              fill=color, width=lw)
    draw.line([br1 - int(size*0.06), top_y, br1, top_y + int(size*0.08)],          fill=color, width=lw)
    draw.line([br1, top_y + int(size*0.08), br1, mid_y - int(size*0.08)],          fill=color, width=lw)
    draw.line([br1, mid_y - int(size*0.08), br1 - int(size*0.06), mid_y],          fill=color, width=lw)
    draw.line([br1 - int(size*0.06), mid_y, stem_x, mid_y],                        fill=color, width=lw)

    # Lower bump (wider)
    bot_y2 = int(cy + size * 0.38)
    br2 = int(cx + size * 0.26)
    draw.line([stem_x, mid_y,               br2 - int(size*0.06), mid_y],          fill=color, width=lw)
    draw.line([br2 - int(size*0.06), mid_y, br2, mid_y + int(size*0.08)],          fill=color, width=lw)
    draw.line([br2, mid_y + int(size*0.08), br2, bot_y2 - int(size*0.08)],         fill=color, width=lw)
    draw.line([br2, bot_y2 - int(size*0.08), br2 - int(size*0.06), bot_y2],        fill=color, width=lw)
    draw.line([br2 - int(size*0.06), bot_y2, stem_x, bot_y2],                      fill=color, width=lw)

    # Two vertical strokes through the B
    stroke_len = int(size * 0.50)
    stroke_w = max(1, lw - 1)
    for sx in [int(cx - size*0.05), int(cx + size*0.10)]:
        draw.line([sx, cy - stroke_len//2, sx, cy + stroke_len//2], fill=color, width=stroke_w)


def make_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    S = size
    cx, cy = S // 2, S // 2

    gold     = (212, 160, 44)
    gold_dim = (90, 65, 12)
    ring_w   = max(2, S // 80)

    # Background circle
    draw.ellipse([0, 0, S-1, S-1], fill=(10, 11, 15, 255))

    # Outer ring
    m = int(S * 0.04)
    draw.ellipse([m, m, S-1-m, S-1-m], outline=gold, width=ring_w)

    # Tick marks
    r_out = S * 0.455
    for i in range(32):
        angle = math.radians(i * (360/32) - 90)
        is_major = (i % 4 == 0)
        r_in = S * 0.41 if is_major else S * 0.435
        x1, y1 = cx + r_out * math.cos(angle), cy + r_out * math.sin(angle)
        x2, y2 = cx + r_in  * math.cos(angle), cy + r_in  * math.sin(angle)
        draw.line([int(x1), int(y1), int(x2), int(y2)],
                  fill=(gold if is_major else gold_dim),
                  width=(max(2, S//60) if is_major else max(1, S//130)))

    # Spokes
    for i in range(8):
        angle = math.radians(i * 45 - 90)
        x1 = cx + int(S * 0.22 * math.cos(angle))
        y1 = cy + int(S * 0.22 * math.sin(angle))
        x2 = cx + int(S * 0.40 * math.cos(angle))
        y2 = cy + int(S * 0.40 * math.sin(angle))
        draw.line([x1, y1, x2, y2], fill=(gold if i%2==0 else gold_dim), width=max(1, S//100))

    # Hub ring
    hr = int(S * 0.195)
    draw.ellipse([cx-hr, cy-hr, cx+hr, cy+hr], outline=gold, width=ring_w)

    # Bitcoin symbol
    draw_btc_symbol(draw, cx, cy, S * 0.28, gold)

    # Circular mask
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, S-1, S-1], fill=255)
    img.putalpha(mask)

    return img


for size in [192, 512]:
    make_icon(size).save(
        f"/sessions/keen-eloquent-cray/mnt/Documents/btc-wheel-bot/mobile-app/public/icon-{size}.png", "PNG"
    )
    print(f"Saved icon-{size}.png")

make_icon(512).save("/sessions/keen-eloquent-cray/mnt/Documents/icon-preview.png", "PNG")
print("Saved preview")

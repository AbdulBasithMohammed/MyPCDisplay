#!/usr/bin/env python
"""Generates background.png for every Deck* theme from one palette.

Run from the repo root:
    venv/Scripts/python.exe tools/make_deck_backgrounds.py

Card geometry here MUST match the coordinates in each theme.yaml.
"""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 480, 320
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEMES = os.path.join(ROOT, "res", "themes")
FONT_BOLD = os.path.join(ROOT, "res", "fonts", "malgun", "malgunbd.ttf")

# ---- palette (white + light blue) -------------------------------------------
BG_TOP      = (255, 255, 255)
BG_BOTTOM   = (223, 238, 251)
CARD        = (255, 255, 255)
CARD_EDGE   = (201, 227, 246)
SHADOW      = (120, 165, 205)
ACCENT      = (45, 164, 232)
TRACK       = (223, 240, 252)

CARD_A = (10, 8, 470, 98)
CARD_B = (10, 106, 470, 230)
CARD_C = (10, 238, 470, 312)
RADIUS = 14


def vertical_gradient(size, top, bottom):
    w, h = size
    grad = Image.new("RGB", (1, h))
    px = grad.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return grad.resize((w, h), Image.BILINEAR)


def base_canvas():
    img = vertical_gradient((W, H), BG_TOP, BG_BOTTOM).convert("RGB")
    shadow = Image.new("L", (W, H), 0)
    sdraw = ImageDraw.Draw(shadow)
    for x0, y0, x1, y1 in (CARD_A, CARD_B, CARD_C):
        sdraw.rounded_rectangle((x0, y0 + 3, x1, y1 + 4), RADIUS, fill=70)
    shadow = shadow.filter(ImageFilter.GaussianBlur(5))
    img.paste(Image.new("RGB", (W, H), SHADOW), (0, 0), shadow)
    draw = ImageDraw.Draw(img)
    for box in (CARD_A, CARD_B, CARD_C):
        draw.rounded_rectangle(box, RADIUS, fill=CARD, outline=CARD_EDGE, width=1)
    return img, draw


def track(draw, x0, y0, x1, y1):
    """Unfilled groove that a progress bar will fill over."""
    draw.rounded_rectangle((x0, y0, x1, y1), 2, fill=TRACK)


def build_deck():
    """DeckWhiteBlue: clock / CPU+GPU radials / now playing."""
    img, draw = base_canvas()
    track(draw, 28, 84, 452, 88)                      # seconds bar
    draw.line((240, 128, 240, 208), fill=CARD_EDGE, width=1)
    track(draw, 124, 194, 232, 198)                   # cpu heat bar
    track(draw, 354, 194, 462, 198)                   # gpu heat bar
    tile = (24, 250, 76, 302)                         # now-playing icon plate
    draw.rounded_rectangle(tile, 10, fill=ACCENT)
    try:
        f = ImageFont.truetype(FONT_BOLD, 30)
    except Exception:
        f = ImageFont.load_default()
    draw.text(((tile[0] + tile[2]) / 2, (tile[1] + tile[3]) / 2), "♪",
              font=f, fill=(255, 255, 255), anchor="mm")
    return img


def build_detail():
    """DeckDetail: memory / disk + network / clock + uptime + ping."""
    img, draw = base_canvas()
    track(draw, 28, 84, 452, 88)                      # memory bar
    draw.line((240, 128, 240, 208), fill=CARD_EDGE, width=1)
    track(draw, 28, 190, 218, 194)                    # disk bar
    track(draw, 28, 296, 238, 300)                    # vram bar
    return img


def build_agenda():
    """DeckAgenda: weather / next event / later today."""
    img, draw = base_canvas()
    # hairline between the weather readout and today's range
    draw.line((306, 26, 306, 80), fill=CARD_EDGE, width=1)
    # separator between the two "later" rows
    draw.line((28, 282, 452, 282), fill=CARD_EDGE, width=1)
    return img


def build_voice():
    """DeckVoice: channel header / who is speaking / roster."""
    img, draw = base_canvas()
    # divider between the channel name and the member count
    draw.line((330, 26, 330, 80), fill=CARD_EDGE, width=1)
    # the speaking card gets an accent bar so it reads as the focal point
    draw.rounded_rectangle((20, 124, 24, 212), 2, fill=ACCENT)
    draw.line((28, 282, 452, 282), fill=CARD_EDGE, width=1)
    return img


def build_otp():
    """DeckOTP: one centred notification card, deliberately unlike the others.

    This screen appears without being asked for, so it does not reuse the
    three-card grid every other screen shares - the different shape is the
    signal that something interrupted you, readable before you focus on it.
    """
    img = vertical_gradient((W, H), BG_TOP, BG_BOTTOM).convert("RGB")

    card = (24, 40, 456, 280)
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).rounded_rectangle(
        (card[0], card[1] + 4, card[2], card[3] + 6), 18, fill=90)
    shadow = shadow.filter(ImageFilter.GaussianBlur(7))
    img.paste(Image.new("RGB", (W, H), SHADOW), (0, 0), shadow)

    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(card, 18, fill=CARD, outline=CARD_EDGE, width=1)

    # Accent stripe along the top edge of the card, clipped to the corner
    # radius by redrawing the card's rounded outline over it.
    stripe = Image.new("RGB", (W, H), CARD)
    sdraw = ImageDraw.Draw(stripe)
    sdraw.rounded_rectangle(card, 18, fill=ACCENT)
    sdraw.rectangle((card[0], card[1] + 6, card[2], card[3]), fill=CARD)
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle(card, 18, fill=255)
    img.paste(stripe, (0, 0), mask)
    draw.rounded_rectangle(card, 18, outline=CARD_EDGE, width=1)

    # Divider above the countdown row.
    draw.line((48, 228, 432, 228), fill=CARD_EDGE, width=1)
    # Groove the countdown bar drains along.
    track(draw, 48, 244, 432, 250)
    return img


def main():
    for name, builder in (("DeckWhiteBlue", build_deck),
                          ("DeckDetail", build_detail),
                          ("DeckAgenda", build_agenda),
                          ("DeckVoice", build_voice),
                          ("DeckOTP", build_otp)):
        out_dir = os.path.join(THEMES, name)
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "background.png")
        builder().save(out)
        print("wrote", out)


if __name__ == "__main__":
    main()

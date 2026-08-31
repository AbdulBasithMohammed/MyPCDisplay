#!/usr/bin/env python
"""Composites the whole League screen as one image.

The theme engine can only drive text, bars and radials from a sensor, and this
screen is icon-led: champion portrait, summoner spells, runes, ability icons,
item icons. So instead of fighting the theme, the entire 480x320 frame is drawn
here with PIL and handed to the BITMAP element (see library/stats.py) as a path.

That also suits the panel. Pushing a full frame costs ~1.3s at ~229 KB/s, but
the content only changes when the *phase* changes, and the output filename
carries a hash of the state, so an unchanged screen is never redrawn.

Phases, and what each one answers:
    idle         how am I doing?          rank, LP, W/L, most-played champions
    meta         what should I pick?      op.gg tier list for my role
    champselect  what do I take?          summoner spells + runes
    early        what do I buy first?     starting items + boots + skill order
    build        what do I build?         the core builds, ranked
    late         what do I finish with?   4th / 5th / 6th items

idle and meta draw without the left rail: neither has a champion to put in it.

Every option carries its win rate and sample size, because "56% over 8,605
games" and "60% over 290 games" are not the same recommendation.

Layout is a left rail (who you are playing) plus a content pane, deliberately
not the three stacked cards the other screens use.
"""
import hashlib
import os
import threading

from PIL import Image, ImageDraw, ImageFont

from library.sensors.league import CACHE_DIR, DDRAGON, Static, _session

W, H = 480, 320
ICON_DIR = os.path.join(CACHE_DIR, "icons")
OUT_DIR = os.path.join(CACHE_DIR, "frames")

# op.gg hosts the rune art; Data Dragon does not serve perks at a simple path.
OPGG_IMG = "https://opgg-static.akamaized.net/meta/images/lol"

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
FONTS = os.path.join(ROOT, "res", "fonts")
F_BOLD = os.path.join(FONTS, "malgun", "malgunbd.ttf")
F_REG = os.path.join(FONTS, "malgun", "malgun.ttf")
F_MONO = os.path.join(FONTS, "jetbrains-mono", "JetBrainsMono-Bold.ttf")

BG_TOP = (255, 255, 255)
BG_BOTTOM = (223, 238, 251)
CARD = (255, 255, 255)
CARD_EDGE = (201, 227, 246)
ACCENT = (45, 164, 232)
NAVY = (18, 52, 80)
MUTED = (110, 144, 168)
TRACK = (223, 240, 252)
GOOD = (36, 160, 106)          # win rates above 52% read as good

RAIL_W = 116                   # narrow: the content pane needs the room
X0 = RAIL_W + 12               # left edge of content
XR = W - 12                    # right edge, stats are right-aligned to this

_font_cache = {}


def font(path, size):
    key = (path, size)
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(path, size)
        except Exception:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


# ------------------------------------------------------------------- icons
def _cached(path, url):
    """Download once, keep forever. None on any failure - a gap beats a crash."""
    if os.path.exists(path):
        return path
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        r = _session().get(url, timeout=20)
        if r.status_code != 200 or not r.content:
            return None
        with open(path, "wb") as f:
            f.write(r.content)
        return path
    except Exception:
        return None


def _load(path, size):
    if not path:
        return None
    try:
        return Image.open(path).convert("RGB").resize((size, size), Image.LANCZOS)
    except Exception:
        return None


def dd_icon(kind, name, size):
    """A Data Dragon icon: item, spell or champion."""
    if not name:
        return None
    safe = "".join(c for c in str(name) if c.isalnum() or c in "._-")
    path = os.path.join(ICON_DIR, "%s_%s_%s.png" % (kind, Static.patch, safe))
    url = "%s/cdn/%s/img/%s/%s.png" % (DDRAGON, Static.patch, kind, name)
    return _load(_cached(path, url), size)


def perk_icon(kind, perk_id, size):
    """A rune icon from op.gg's CDN. `kind` is "perk" or "perkStyle"."""
    if not perk_id:
        return None
    path = os.path.join(ICON_DIR, "%s_%s_%s.png" % (kind, Static.patch, perk_id))
    url = "%s/%s/%s/%s.png" % (OPGG_IMG, Static.patch, kind, perk_id)
    return _load(_cached(path, url), size)


def item_icon(item_id, size):
    return dd_icon("item", str(item_id), size)


def champ_icon(champion, size):
    return dd_icon("champion", Static.champ_key.get(champion) or champion, size)


def paste(img, icon, xy, size, radius=6, circle=False):
    """Paste an icon, or a placeholder box if it could not be fetched."""
    d = ImageDraw.Draw(img)
    if icon is None:
        d.rounded_rectangle((xy[0], xy[1], xy[0] + size, xy[1] + size),
                            radius, fill=TRACK, outline=CARD_EDGE)
        return
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    if circle:
        md.ellipse((0, 0, size - 1, size - 1), fill=255)
    else:
        md.rounded_rectangle((0, 0, size - 1, size - 1), radius, fill=255)
    img.paste(icon, xy, mask)


# ------------------------------------------------------------------ canvas
def _canvas(rail=True):
    grad = Image.new("RGB", (1, H))
    px = grad.load()
    for y in range(H):
        t = y / max(H - 1, 1)
        px[0, y] = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3))
    img = grad.resize((W, H), Image.BILINEAR).convert("RGB")
    d = ImageDraw.Draw(img)
    if rail:
        d.rounded_rectangle((6, 6, RAIL_W - 6, H - 6), 12, fill=CARD, outline=CARD_EDGE)
    return img, d


def _center(d, text, cx, y, f, fill):
    d.text((cx - d.textlength(text, font=f) / 2, y), text, font=f, fill=fill)


def _right(d, text, rx, y, f, fill):
    d.text((rx - d.textlength(text, font=f), y), text, font=f, fill=fill)


def _ellipsize(d, text, f, max_px):
    if d.textlength(text, font=f) <= max_px:
        return text
    while text and d.textlength(text + "..", font=f) > max_px:
        text = text[:-1]
    return text + ".."


def _rail(img, d, champion, role, phase):
    paste(img, champ_icon(champion, 68), (24, 22), 68, radius=12)
    cx = RAIL_W / 2
    d_name = _ellipsize(d, champion or "-", font(F_BOLD, 16), RAIL_W - 18)
    _center(d, d_name, cx, 100, font(F_BOLD, 16), NAVY)
    if role and role not in ("NONE", ""):
        _center(d, role, cx, 122, font(F_REG, 11), MUTED)

    steps = [("champselect", "PICK"), ("early", "START"),
             ("build", "CORE"), ("late", "FINISH")]
    y = 168
    for key, label in steps:
        on = key == phase
        d.rounded_rectangle((16, y, RAIL_W - 16, y + 26), 7,
                            fill=ACCENT if on else TRACK)
        _center(d, label, cx, y + 6, font(F_BOLD, 11),
                (255, 255, 255) if on else MUTED)
        y += 31


def _heading(d, text, y, note=""):
    d.text((X0, y), text, font=font(F_BOLD, 12), fill=MUTED)
    if note:
        _right(d, note, XR, y, font(F_REG, 10), MUTED)
    d.line((X0, y + 16, XR, y + 16), fill=CARD_EDGE, width=1)


def _stats(d, y, win, games, size=14):
    """Win rate and sample size, right-aligned. Colour flags a good win rate."""
    if win:
        try:
            good = float(win.rstrip("%")) >= 52.0
        except ValueError:
            good = False
        _right(d, win, XR, y, font(F_BOLD, size), GOOD if good else NAVY)
    if games:
        _right(d, games + " games", XR, y + size + 3, font(F_REG, 10), MUTED)


def _icon_run(img, d, icons, x, y, size, arrows=False, circle=False, gap=6):
    """A horizontal run of icons, optionally arrow-linked. Returns the end x."""
    for i, ic in enumerate(icons):
        paste(img, ic, (x, y), size, circle=circle)
        x += size
        if i < len(icons) - 1:
            if arrows:
                d.text((x + 1, y + size / 2 - 7), ">", font=font(F_BOLD, 13), fill=CARD_EDGE)
                x += 11
            else:
                x += gap
    return x


# ----------------------------------------------------------------- phases
def _champselect(img, d, b):
    """What to take into the game: summoner spells and runes."""
    _heading(d, "SUMMONER SPELLS", 12)
    y = 34
    for row in (b.get("spells") or [])[:2]:
        icons = [dd_icon("spell", s, 30) for s in row["icons"][:2]]
        _icon_run(img, d, icons, X0, y, 30)
        _stats(d, y + 1, row.get("win"), row.get("games"))
        y += 38

    _heading(d, "RUNES", y + 6)
    y += 28
    for row in (b.get("runes") or [])[:2]:
        x = X0
        paste(img, perk_icon("perk", row.get("keystone"), 34), (x, y), 34, circle=True)
        x += 40
        paste(img, perk_icon("perkStyle", row.get("style"), 22), (x, y + 6), 22, circle=True)
        x += 26
        paste(img, perk_icon("perkStyle", row.get("sub"), 22), (x, y + 6), 22, circle=True)
        x += 30
        names = row.get("names") or []
        if len(names) >= 2:
            d.text((x, y + 4), _ellipsize(d, names[1], font(F_BOLD, 12), XR - x - 74),
                   font=font(F_BOLD, 12), fill=NAVY)
        if names:
            d.text((x, y + 20), _ellipsize(d, names[0], font(F_REG, 10), XR - x - 74),
                   font=font(F_REG, 10), fill=MUTED)
        _stats(d, y + 3, row.get("win"), row.get("games"))
        y += 44

    if not b.get("spells") and not b.get("runes"):
        d.text((X0, 140), "no data", font=font(F_REG, 14), fill=MUTED)


def _early(img, d, b):
    """The first back: what to open with, and what to level."""
    _heading(d, "STARTING ITEMS", 10)
    y = 30
    for row in (b.get("starters") or [])[:2]:
        _icon_run(img, d, [item_icon(i, 30) for i in row["icons"][:3]], X0, y, 30)
        _stats(d, y, row.get("win"), row.get("games"), size=13)
        y += 36

    _heading(d, "BOOTS", y + 2)
    y += 22
    for row in (b.get("boots") or [])[:2]:
        _icon_run(img, d, [item_icon(i, 30) for i in row["icons"][:2]], X0, y, 30)
        _stats(d, y, row.get("win"), row.get("games"), size=13)
        y += 36

    _heading(d, "SKILL ORDER", y + 2)
    y += 24
    letters = b.get("skill_letters") or []
    x = X0
    for i, ic in enumerate((b.get("skill_icons") or [])[:3]):
        paste(img, dd_icon("spell", ic, 28), (x, y), 28)
        key = letters[i] if i < len(letters) else "?"
        d.rounded_rectangle((x, y, x + 14, y + 14), 4, fill=ACCENT)
        _center(d, key, x + 7, y, font(F_BOLD, 10), (255, 255, 255))
        x += 34
        if i < 2:
            d.text((x - 6, y + 6), ">", font=font(F_BOLD, 12), fill=CARD_EDGE)

    seq = b.get("skill_levels") or ""
    if seq:
        bx, bw = x + 6, 12
        for i, ch in enumerate(seq[:18]):
            sx = bx + i * (bw + 1)
            if sx + bw > XR:
                break
            hot = ch == "R"
            d.rounded_rectangle((sx, y + 6, sx + bw, y + 22), 3,
                                fill=ACCENT if hot else TRACK)
            _center(d, ch, sx + bw / 2, y + 7, font(F_MONO, 8),
                    (255, 255, 255) if hot else MUTED)


def _rows_of_items(img, d, rows, y, size, per_row, arrows, step):
    for row in rows:
        icons = [item_icon(i, size) for i in row["icons"][:per_row]]
        _icon_run(img, d, icons, X0, y, size, arrows=arrows)
        _stats(d, y + (size - 16) / 2, row.get("win"), row.get("games"), size=13)
        y += step
    return y


def _build(img, d, b):
    """The core builds, ranked - with the sample size, so 60% over 290 games
    can be read for what it is."""
    core = (b.get("core") or [])[:5]
    _heading(d, "CORE BUILDS", 10, note="win rate / games")
    if core:
        _rows_of_items(img, d, core, 32, 34, 3, True, 56)
    else:
        d.text((X0, 60), "no build data", font=font(F_REG, 14), fill=MUTED)


def _late(img, d, b):
    """Once the core is done: what the fourth, fifth and sixth slots want."""
    y = 10
    for label, key in (("FOURTH ITEM", "fourth"), ("FIFTH ITEM", "fifth"),
                       ("SIXTH ITEM", "sixth")):
        rows = (b.get(key) or [])[:2]
        _heading(d, label, y)
        y += 20
        x = X0
        for row in rows:
            paste(img, item_icon(row["icons"][0], 30), (x, y), 30)
            wr = row.get("win") or ""
            try:
                good = float(wr.rstrip("%")) >= 52.0
            except ValueError:
                good = False
            d.text((x + 36, y - 1), wr, font=font(F_BOLD, 13), fill=GOOD if good else NAVY)
            d.text((x + 36, y + 15), (row.get("games") or "") + " g",
                   font=font(F_REG, 9), fill=MUTED)
            x += 108
        y += 42


TIER_COLOR = {
    "Iron": (110, 110, 110), "Bronze": (156, 106, 70), "Silver": (140, 158, 170),
    "Gold": (206, 160, 60), "Platinum": (60, 170, 168), "Emerald": (48, 158, 108),
    "Diamond": (86, 128, 220), "Master": (150, 90, 200),
    "Grandmaster": (198, 78, 78), "Challenger": (72, 156, 214),
}


def _display_name(champ):
    """'XinZhao' -> 'Xin Zhao'. dpm.lol reports Riot's internal names.

    Data Dragon keys champions by that internal name and carries the pretty
    one, so the id lookup is only a fallback for anything it does not know.
    """
    internal = champ.get("champion") or "?"
    try:
        # champ_by_id carries the display name; Static.champs is keyed by the
        # internal name but holds only the stat block, so it is no use here.
        return Static.champ_by_id.get(str(champ.get("champion_id") or "")) or internal
    except Exception:
        return internal


def _pips(d, results, x, y, size=11, gap=4):
    """Recent results as W/L squares, newest first."""
    for won in results[:5]:
        d.rounded_rectangle((x, y, x + size, y + size), 3,
                            fill=GOOD if won else (208, 92, 92))
        _center(d, "W" if won else "L", x + size / 2 + 0.5, y - 1,
                font(F_BOLD, 8), (255, 255, 255))
        x += size + gap
    return x


def _idle(img, d, ready=True, profile=None):
    """Between games: the ranked profile, or a plain waiting card without one.

    Showing rank, LP and what you actually play is more use here than "No
    match" - this is the state the screen sits in most of the time.
    """
    profile = profile or {}
    if not profile.get("tier"):
        d.rounded_rectangle((8, 8, W - 8, H - 8), 14, fill=CARD, outline=CARD_EDGE)
        _center(d, "No match" if ready else "Starting up", W / 2, 128,
                font(F_BOLD, 30), NAVY)
        _center(d, "waiting for champ select" if ready else "loading item data",
                W / 2, 172, font(F_REG, 15), MUTED)
        d.rounded_rectangle((W / 2 - 40, 210, W / 2 + 40, 214), 2, fill=TRACK)
        return

    # ---- rank card -------------------------------------------------------
    d.rounded_rectangle((8, 8, W - 8, 116), 14, fill=CARD, outline=CARD_EDGE)
    tier = profile.get("tier") or ""
    colour = TIER_COLOR.get(tier, ACCENT)

    d.rounded_rectangle((20, 22, 26, 102), 3, fill=colour)
    rank_text = "%s %s" % (tier, profile.get("division") or "")
    d.text((38, 24), rank_text.strip(), font=font(F_BOLD, 26), fill=colour)
    lp = profile.get("lp")
    if lp is not None:
        d.text((38, 58), "%d LP" % lp, font=font(F_BOLD, 15), fill=NAVY)

    wins, losses = profile.get("wins") or 0, profile.get("losses") or 0
    wr = profile.get("winrate")
    if wins or losses:
        d.text((38, 80), "%dW %dL" % (wins, losses), font=font(F_REG, 12), fill=MUTED)
        if wr is not None:
            x = 38 + d.textlength("%dW %dL  " % (wins, losses), font=font(F_REG, 12))
            d.text((x, 80), "%d%%" % wr, font=font(F_BOLD, 12),
                   fill=GOOD if wr >= 52 else MUTED)

    # Ladder position, right-aligned so it never collides with the rank text.
    ladder, top = profile.get("ladder"), profile.get("ladder_top")
    if ladder:
        _right(d, "{:,}".format(int(ladder)), XR, 26, font(F_BOLD, 17), NAVY)
        _right(d, "LADDER RANK", XR, 47, font(F_REG, 9), MUTED)
    if top:
        _right(d, "top %.1f%%" % float(top), XR, 64, font(F_BOLD, 12), ACCENT)
    if profile.get("recent"):
        _right(d, "RECENT", XR, 84, font(F_REG, 9), MUTED)
        _pips(d, profile["recent"], XR - 79, 96)

    # ---- most played -----------------------------------------------------
    d.rounded_rectangle((8, 124, W - 8, H - 8), 14, fill=CARD, outline=CARD_EDGE)
    d.text((20, 136), "MOST PLAYED", font=font(F_BOLD, 12), fill=MUTED)
    name = profile.get("name") or ""
    if name:
        _right(d, "%s#%s" % (name, profile.get("tag") or ""), W - 20, 136,
               font(F_REG, 10), MUTED)
    d.line((20, 154, W - 20, 154), fill=CARD_EDGE, width=1)

    y = 164
    for c in (profile.get("champions") or [])[:3]:
        paste(img, champ_icon(c.get("champion"), 34), (20, y), 34, radius=8)
        d.text((62, y + 1), _ellipsize(d, _display_name(c), font(F_BOLD, 14), 96),
               font=font(F_BOLD, 14), fill=NAVY)
        d.text((62, y + 19), "%d games" % (c.get("games") or 0),
               font=font(F_REG, 10), fill=MUTED)

        wr_c = c.get("winrate") or 0
        d.text((186, y + 1), "%d%%" % wr_c, font=font(F_BOLD, 14),
               fill=GOOD if wr_c >= 52 else NAVY)
        d.text((186, y + 19), "win rate", font=font(F_REG, 9), fill=MUTED)

        d.text((260, y + 1), "%.1f" % (c.get("kda") or 0),
               font=font(F_BOLD, 14), fill=NAVY)
        d.text((260, y + 19), "KDA", font=font(F_REG, 9), fill=MUTED)

        _right(d, "%.1f / %.1f / %.1f" % (c.get("kills") or 0, c.get("deaths") or 0,
                                          c.get("assists") or 0),
               XR, y + 1, font(F_REG, 12), NAVY)
        _right(d, "%.1f cs/m" % (c.get("csm") or 0), XR, y + 19,
               font(F_REG, 9), MUTED)
        y += 44


def _meta(img, d, rows, role):
    """Champ select before you lock in: what is strong in your role.

    Until the pick is completed there is no champion to build for, so the old
    screen showed nothing at all. A tier list is the one thing that is useful
    at exactly that moment.
    """
    d.rounded_rectangle((8, 8, W - 8, H - 8), 14, fill=CARD, outline=CARD_EDGE)
    d.text((20, 20), "CHAMP SELECT", font=font(F_BOLD, 12), fill=ACCENT)
    _right(d, (role or "ALL").upper(), W - 20, 20, font(F_BOLD, 12), MUTED)
    d.text((20, 40), "Strongest picks right now", font=font(F_BOLD, 17), fill=NAVY)
    d.line((20, 68, W - 20, 68), fill=CARD_EDGE, width=1)

    if not rows:
        _center(d, "no meta data", W / 2, 150, font(F_REG, 15), MUTED)
        return

    _right(d, "WIN", W - 96, 76, font(F_REG, 9), MUTED)
    _right(d, "PICK", W - 20, 76, font(F_REG, 9), MUTED)

    y = 92
    for i, r in enumerate(rows[:5], start=1):
        paste(img, champ_icon(r.get("champion"), 30), (44, y), 30, radius=7)
        _center(d, str(i), 28, y + 7, font(F_BOLD, 13), MUTED)
        d.text((84, y + 6), _ellipsize(d, r.get("champion") or "?",
                                       font(F_BOLD, 15), 160),
               font=font(F_BOLD, 15), fill=NAVY)
        win = r.get("win") or ""
        try:
            good = float(win.rstrip("%")) >= 51.0
        except ValueError:
            good = False
        _right(d, win, W - 96, y + 6, font(F_BOLD, 14), GOOD if good else NAVY)
        _right(d, r.get("pick") or "", W - 20, y + 6, font(F_REG, 12), MUTED)
        y += 40


# ------------------------------------------------------------------ render
def _profile():
    """Ranked profile for the idle card, or {} if it is not available yet.

    Never raises and never blocks: the poller owns the network, this only
    reads what it has already published.
    """
    try:
        from library.sensors.dpm import Profile
        Profile.ensure_started()
        return Profile.data or {}
    except Exception:
        return {}


_meta_memo = {}
_meta_fetching = set()
_meta_lock = threading.Lock()


def _meta_rows(role):
    """Tier-list rows for a role. Never blocks; may return [] the first time.

    opgg.tierlist can spend 25s on a cold cache, and this is called from the
    render path, which is the single-threaded loop shared with drawing. So the
    fetch is pushed onto a daemon thread and the frame is drawn with whatever
    is already known - the next tick picks up the result. Same rule the
    pollers follow: sensors read state, threads fill it.
    """
    key = str(role or "")
    with _meta_lock:
        if key in _meta_memo:
            return _meta_memo[key]
        if key in _meta_fetching:
            return []
        _meta_fetching.add(key)

    def worker():
        rows = []
        try:
            from library.sensors import league, opgg
            cfg = league._cfg()
            rows = opgg.tierlist(role, CACHE_DIR,
                                 tier=str(cfg.get("meta_tier") or "emerald_plus"))
        except Exception:
            rows = []
        with _meta_lock:
            if rows:
                _meta_memo[key] = rows
            _meta_fetching.discard(key)

    threading.Thread(target=worker, name="league-meta", daemon=True).start()
    return []


def _sig(rows, *fields):
    return ";".join("|".join(str(r.get(f, "")) for f in fields) +
                    "," + ",".join(str(i) for i in (r.get("icons") or []))
                    for r in (rows or []))


def state_key(champion, role, phase, build):
    """Everything the picture depends on. Same key means no redraw.

    The patch is part of the key because it selects the icons - but not when
    idle, which draws no icons at all. Including it there made the panel redraw
    the identical idle frame the moment Data Dragon finished loading, costing a
    pointless ~1.3s full-screen blit on every cold start.
    """
    if phase == "idle":
        # The profile is part of the key so a new rank or a finished game
        # redraws, but nothing else about it does - an unchanged profile keeps
        # serving the cached frame instead of re-blitting every poll.
        p = _profile()
        return "|".join(["idle", str(Static.ready), str(p.get("tier")),
                         str(p.get("division")), str(p.get("lp")),
                         str(p.get("wins")), str(p.get("losses")),
                         str(p.get("ladder")),
                         ",".join("%s%s%s" % (c.get("champion"), c.get("games"),
                                              c.get("winrate"))
                                  for c in (p.get("champions") or [])),
                         ",".join("1" if r else "0" for r in (p.get("recent") or []))])
    if phase == "meta":
        rows = _meta_rows(role)
        return "meta|" + str(role) + "|" + ",".join(
            "%s%s%s" % (r.get("champion"), r.get("win"), r.get("pick")) for r in rows)
    return "|".join([
        str(champion), str(role), str(phase), str(Static.patch),
        _sig(build.get("spells"), "win", "games"),
        _sig(build.get("runes"), "win", "games", "keystone", "style", "sub"),
        _sig(build.get("starters"), "win", "games"),
        _sig(build.get("boots"), "win", "games"),
        _sig(build.get("core"), "win", "games"),
        _sig(build.get("fourth"), "win", "games"),
        _sig(build.get("fifth"), "win", "games"),
        _sig(build.get("sixth"), "win", "games"),
        ",".join(build.get("skill_icons") or []),
        build.get("skill_levels") or "",
    ])


def render(champion, role, phase, build):
    """Draw the frame for this state and return its path, reusing an old one."""
    digest = hashlib.sha1(state_key(champion, role, phase, build).encode("utf-8")).hexdigest()[:16]
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "league_%s.png" % digest)
    if os.path.exists(out):
        return out

    if phase == "idle":
        img, d = _canvas(rail=False)
        _idle(img, d, ready=Static.ready, profile=_profile())
    elif phase == "meta":
        img, d = _canvas(rail=False)
        _meta(img, d, _meta_rows(role), role)
    else:
        img, d = _canvas()
        _rail(img, d, champion, role, phase)
        {"champselect": _champselect, "early": _early,
         "build": _build, "late": _late}.get(phase, _build)(img, d, build or {})

    img.save(out)
    _prune()
    return out


def _prune(keep=32):
    """Frames are cheap but not free; keep the most recent handful."""
    try:
        files = [os.path.join(OUT_DIR, f) for f in os.listdir(OUT_DIR) if f.endswith(".png")]
        files.sort(key=os.path.getmtime, reverse=True)
        for f in files[keep:]:
            os.remove(f)
    except Exception:
        pass

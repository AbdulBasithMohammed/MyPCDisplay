#!/usr/bin/env python
"""Win-rate build data for a champion, read from op.gg.

WHY SCRAPING AND NOT AN API
    Riot publishes no build recommendations - Data Dragon's `recommended` block
    and Community Dragon's `recommendedItemDefaults` are both empty. Riot's
    Match API could derive them but needs a key and a lot of requests. The
    stats sites are where this data actually lives, and none of them offer a
    public API. op.gg renders its build pages server-side, so everything is
    already in the HTML - no browser, no JS, and no bot challenge to get past.
    (stats2.u.gg answers 403 behind Cloudflare; we do not go near it.)

    This is an unofficial source. It can change shape or start refusing us at
    any time, so every failure falls back to a stale cache and then to
    league_builds.yaml, and the screen keeps working.

HOW IT IS PARSED
    Each block on the page is a table whose rows pair a set of icons with
    "pick% / N Games / win%". So the parser slices the document by heading, then
    reads rows - that keeps every option's sample size and win rate, rather than
    flattening to a single "best" build.

BEING A GOOD CITIZEN
    One request per champion+role per CACHE_HOURS, cached on disk. A whole
    evening of League is a handful of requests.
"""
import json
import os
import re
import time

CACHE_HOURS = 24
# Bump when the shape of the cached payload changes. Without this, an upgrade
# keeps serving yesterday's cache in the old format and the screen breaks on a
# missing key - which is exactly what happened when spells and skills were added.
CACHE_VERSION = 3
TIMEOUT = 25
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# Headings in the order they appear. The parser walks them in sequence so a
# heading word appearing earlier in a script blob cannot pull the wrong slice.
SECTIONS = ("Summoner spells", "Skill order", "Starter items", "Boots",
            "Core builds", "Fourth Item", "Fifth Item", "Sixth Item")

# The page uses three different stat layouts, and assuming one silently drops
# whole sections:
#     core / starters / boots : "12.34 % 5,678 Games 56.7 %"   pick, games, win
#     summoner spells         : "95.24 12,267 Games 49.91 %"   pick has no %
#     fourth / fifth / sixth  : "63.07 % 48,917 Games"          win first, no pick
# So the trailing win rate is optional, and when it is absent the leading figure
# is the win rate rather than the pick rate.
NUM = r"\d{1,3}(?:\.\d{1,2})?"
STAT_RE = re.compile(r"(%s) ?%%? ([\d,]{2,}) Games(?: (%s) ?%%)?" % (NUM, NUM))


def _stats(text):
    """(pick, games, win) from a row, or None. pick may be empty."""
    m = STAT_RE.search(text)
    if not m:
        return None
    lead, games, trail = m.group(1), m.group(2), m.group(3)
    if trail:
        return lead + "%", games, trail + "%"
    return "", games, lead + "%"

ROLE_SLUG = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "MID": "mid",
             "BOTTOM": "adc", "BOT": "adc", "UTILITY": "support",
             "SUPPORT": "support"}

# Champions whose op.gg slug is not just their name stripped of punctuation.
SLUG_ALIASES = {
    "wukong": "monkeyking",         # Riot's internal name won here
    "monkeyking": "monkeyking",
    "nunuwillump": "nunu",
    "renataglasc": "renata",
}

# Consumables and trinkets. op.gg lists these among the starters, but they are
# not part of a build path - starting the card on "Health Potion" is useless.
NOT_A_BUILD_ITEM = {
    "2003", "2031", "2033", "2055",                  # potions, control ward
    "2138", "2139", "2140", "2150", "2151", "2152",  # elixirs
    "3340", "3363", "3364", "3330",                  # trinkets
}


def champion_slug(name):
    """'Kai'Sa' -> 'kaisa', 'Dr. Mundo' -> 'drmundo', 'Wukong' -> 'monkeyking'."""
    n = re.sub(r"[^a-z0-9]", "", str(name).strip().lower())
    return SLUG_ALIASES.get(n, n)


def _cache_file(cache_dir, champ, role):
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "opgg_%s_%s.json" % (champion_slug(champ), role or "auto"))


def _plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _slices(html):
    """Raw HTML per section heading, in page order."""
    found, cursor = [], 0
    for head in SECTIONS:
        at = html.find(head, cursor)
        if at == -1:
            continue
        found.append((head, at))
        cursor = at + len(head)
    out = {}
    for i, (head, start) in enumerate(found):
        end = found[i + 1][1] if i + 1 < len(found) else min(len(html), start + 20000)
        out[head] = html[start:end]
    return out


def _rows(seg, pattern, limit=5):
    """Rows of (icons, pick, games, win) from one section.

    `pattern` pulls the icons out of a row - item ids, or summoner spell names.
    Rows without both icons and a stat line are layout rows, and are dropped.
    """
    out = []
    for chunk in re.split(r"<tr", seg or "")[1:]:
        icons, seen = re.findall(pattern, chunk), []
        for i in icons:
            if i not in seen:
                seen.append(i)
        st = _stats(_plain(chunk))
        if not seen or not st:
            continue
        out.append({"icons": seen, "pick": st[0], "games": st[1], "win": st[2]})
        if len(out) >= limit:
            break
    return out


ITEM_PAT = r"item/(\d{4,5})\.png"
SPELL_PAT = r"spell/(Summoner[A-Za-z0-9]+)\.png"


def _items(seg, limit=5, drop_junk=True):
    rows = _rows(seg, ITEM_PAT, limit)
    if drop_junk:
        for r in rows:
            r["icons"] = [i for i in r["icons"] if i not in NOT_A_BUILD_ITEM]
    return [r for r in rows if r["icons"]]


def _runes(html, limit=2):
    """The most-played rune pages: primary tree, keystone, secondary tree.

    Rune blocks are not table rows, so they are found by clustering the
    perkStyle images (two per page - primary and secondary) and reading the
    stat line that follows the cluster.
    """
    pos = [m.start() for m in re.finditer(r"perkStyle/", html)]
    groups = []
    for p in pos:
        if groups and p - groups[-1][-1] < 2000:
            groups[-1].append(p)
        else:
            groups.append([p])

    out = []
    for g in groups:
        seg = html[max(0, g[0] - 1500):g[-1] + 3000]
        styles = re.findall(r"perkStyle/(\d+)\.png", seg)
        keys = re.findall(r"/perk/(\d+)\.png", seg)
        names = re.findall(r'alt="([^"]{2,30})"', seg)
        st = _stats(_plain(seg))
        if len(styles) < 2 or not keys or not st:
            continue
        out.append({"style": styles[0], "keystone": keys[0], "sub": styles[1],
                    "names": names[:3], "pick": st[0], "games": st[1], "win": st[2]})
        if len(out) >= limit:
            break
    return out


def _skill_order(seg):
    """Max priority (as ability image names) and the 18-level level-up string.

    The priority icons are image names like `KaynQ`, but plenty of champions
    have icons that do not encode the key at all (`AhriOrbofDeception`), so the
    letters are resolved later against Data Dragon's spell list rather than
    guessed from the filename.
    """
    if not seg:
        return [], ""
    icons, seen = re.findall(r"spell/([A-Za-z0-9'._-]+)\.png", seg), []
    for i in icons:
        if i not in seen:
            seen.append(i)
    text = _plain(seg)
    text = text[:text.find("%")] if "%" in text else text
    letters = re.findall(r"(?<![A-Za-z])([QWER])(?![A-Za-z])", text)
    return seen[:3], "".join(letters[:18])


def fetch(champion, role, cache_dir, force=False):
    """Everything the screen needs for one champion, or None."""
    slug = champion_slug(champion)
    if not slug:
        return None
    role_slug = ROLE_SLUG.get(str(role or "").upper(), "")
    path = _cache_file(cache_dir, champion, role_slug)

    if not force and os.path.exists(path):
        try:
            if time.time() - os.path.getmtime(path) < CACHE_HOURS * 3600:
                with open(path, encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("v") == CACHE_VERSION:
                    return cached
        except Exception:
            pass

    url = "https://op.gg/lol/champions/%s/build" % slug
    if role_slug:
        url += "/" + role_slug
    try:
        import requests
        r = requests.get(url, timeout=TIMEOUT,
                         headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        if r.status_code != 200 or len(r.text) < 50000:
            return _stale(path)
        html = r.text
    except Exception:
        return _stale(path)

    sec = _slices(html)
    core = _items(sec.get("Core builds"), limit=5)
    if not core:
        return _stale(path)

    starters = _items(sec.get("Starter items"), limit=3)
    boots = _items(sec.get("Boots"), limit=3)
    fourth = _items(sec.get("Fourth Item"), limit=4)
    fifth = _items(sec.get("Fifth Item"), limit=4)
    sixth = _items(sec.get("Sixth Item"), limit=4)
    skill_icons, skill_levels = _skill_order(sec.get("Skill order"))

    # A flat path is still handy for "what should I buy next" style logic.
    order = []
    for group in (starters[:1], boots[:1], core[:1], fourth[:1], fifth[:1], sixth[:1]):
        for row in group:
            for iid in row["icons"]:
                if iid not in order:
                    order.append(iid)

    data = {
        "v": CACHE_VERSION,
        "spells": _rows(sec.get("Summoner spells"), SPELL_PAT, limit=2),
        "runes": _runes(html),
        "starters": starters,
        "boots": boots,
        "core": core,
        "fourth": fourth,
        "fifth": fifth,
        "sixth": sixth,
        "skill_icons": skill_icons,
        "skill_levels": skill_levels,
        "order": order,
        # headline figures for the rail
        "winrate": core[0]["win"] if core else "",
        "games": core[0]["games"] if core else "",
        "source": "op.gg", "role": role_slug, "fetched": time.time(),
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass
    return data


def _stale(path):
    """A cached copy past its TTL still beats nothing when a fetch fails."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("v") != CACHE_VERSION:
            return None        # old format: better nothing than a missing key
        data["source"] = "op.gg (cached)"
        return data
    except Exception:
        return None

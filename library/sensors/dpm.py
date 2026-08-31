#!/usr/bin/env python
"""Ranked profile for the League screen's idle state, from dpm.lol.

WHY THIS SOURCE
    Riot's own API needs a key that expires every 24h, which is useless for an
    unattended desk panel. dpm.lol serves the profile as plain JSON with no key
    and no bot challenge - verified endpoints:

        /v1/players/search?gameName=&tagLine=   ->  puuid, ranks[]
        /v1/players/{puuid}/champions           ->  per-champion aggregates

    Its /v1/tierlist needs undocumented params and returned empty for every
    combination tried, so champion meta comes from op.gg instead (opgg.tierlist).

    Unofficial, so every failure falls back to the disk cache and then to an
    empty profile; the screen degrades to "no profile" rather than breaking.

BEING A GOOD CITIZEN
    Two requests per REFRESH_SECONDS while the screen is idle, cached on disk.
"""
import json
import os
import threading
import time

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SERVICES_FILE = os.path.join(APP_DIR, "services.yaml")
CACHE_DIR = os.path.join(APP_DIR, "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "dpm_profile.json")

BASE = "https://dpm.lol"
TIMEOUT = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# The idle screen is only looked at between games, so this does not need to be
# quick - and dpm.lol rate-limits a burst of requests hard.
REFRESH_SECONDS = 15 * 60
RETRY_SECONDS = 60

SOLO = "RANKED_SOLO_5x5"
FLEX = "RANKED_FLEX_SR"


def _cfg():
    try:
        import yaml
        with open(SERVICES_FILE, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("league") or {}
    except Exception:
        return {}


def riot_id():
    """('3mperor', 'bhsdk') from services.yaml, or (None, None)."""
    cfg = _cfg()
    raw = str(cfg.get("riot_id") or "").strip()
    if "#" in raw:
        name, _, tag = raw.partition("#")
        name, tag = name.strip(), tag.strip()
        if name and tag:
            return name, tag
    return None, None


def _get(session, path, **params):
    r = session.get(BASE + path, params=params or None, timeout=TIMEOUT,
                    headers={"User-Agent": UA, "Accept": "application/json"})
    if r.status_code != 200:
        raise ValueError("%s -> %d" % (path, r.status_code))
    return r.json()


def _rank_of(ranks, queue):
    for entry in ranks or []:
        if entry.get("queue") == queue:
            return entry
    return None


def fetch(name, tag):
    """The whole profile payload, or None. Network work happens here."""
    import requests

    s = requests.Session()
    player = _get(s, "/v1/players/search", gameName=name, tagLine=tag)
    puuid = player.get("puuid")
    if not puuid:
        return None

    solo = _rank_of(player.get("ranks"), SOLO) or {}
    wins, losses = int(solo.get("wins") or 0), int(solo.get("losses") or 0)
    total = wins + losses

    champions, recent = [], []
    try:
        rows = _get(s, "/v1/players/%s/champions" % puuid, queue=SOLO)
        # Ranked by games played: "what I actually play" is more use on a desk
        # panel than a 100% win rate over two games.
        rows = sorted(rows or [], key=lambda c: -(c.get("gamesPlayed") or 0))
        # There is no match-history endpoint, so the closest thing to recent
        # form is the most-played champion's own recent results (1 = win).
        if rows:
            recent = [bool(x) for x in (rows[0].get("recentResults") or [])[:5]]
        for c in rows[:3]:
            played = int(c.get("gamesPlayed") or 0)
            champions.append({
                # Internal name ("XinZhao"); the renderer resolves it to the
                # display name ("Xin Zhao") through Data Dragon, which this
                # module deliberately does not depend on.
                "champion": c.get("championName") or "?",
                "champion_id": str(c.get("championId") or ""),
                "games": played,
                "wins": int(c.get("win") or 0),
                "winrate": round(float(c.get("winrate") or 0)),
                "kda": round(float(c.get("kda") or 0), 1),
                "kills": round(float(c.get("kills") or 0), 1),
                "deaths": round(float(c.get("deaths") or 0), 1),
                "assists": round(float(c.get("assists") or 0), 1),
                "csm": round(float(c.get("csm") or 0), 1),
            })
    except Exception:
        pass

    return {
        "v": 1,
        "at": time.time(),
        "name": player.get("gameName") or name,
        "tag": player.get("tagLine") or tag,
        "level": player.get("summonerLevel") or 0,
        "tier": (solo.get("tier") or "").title(),
        "division": solo.get("rank") or "",
        "lp": solo.get("leaguePoints"),
        "wins": wins,
        "losses": losses,
        "winrate": round(wins / total * 100) if total else None,
        "ladder": solo.get("rankPosition"),
        "ladder_top": solo.get("rankTop"),
        "champions": champions,
        "recent": recent[:5],
    }


class Profile:
    """Background-refreshed ranked profile. Sensors read the class attributes.

    Follows the poller shape every sensor in this repo uses: state lives at
    class level because the render loop builds a fresh sensor object each tick,
    and the network call is on a daemon thread because that loop must not block.
    """

    _started = False
    _lock = threading.Lock()

    data = {}
    status = "starting"
    error = ""

    @classmethod
    def ensure_started(cls):
        with cls._lock:
            if cls._started:
                return
            cls._started = True
            cls._load_cache()
            threading.Thread(target=cls._loop, name="dpm-profile",
                             daemon=True).start()

    @classmethod
    def _load_cache(cls):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("v") == 1:
                cls.data = cached
                cls.status = "cached"
        except Exception:
            pass

    @classmethod
    def _save_cache(cls, payload):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = CACHE_FILE + ".new"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, CACHE_FILE)
        except Exception:
            pass

    @classmethod
    def _loop(cls):
        while True:
            name, tag = riot_id()
            if not name:
                cls.status = "no riot_id"
                cls.error = "set league.riot_id in services.yaml"
                time.sleep(REFRESH_SECONDS)
                continue
            try:
                payload = fetch(name, tag)
                if payload:
                    cls.data = payload
                    cls.status = "ok"
                    cls.error = ""
                    cls._save_cache(payload)
                    time.sleep(REFRESH_SECONDS)
                    continue
                cls.error = "no profile returned"
            except Exception as e:
                cls.error = str(e)[:80]
            # Keep serving whatever we already have rather than blanking it.
            cls.status = "stale" if cls.data else "error"
            time.sleep(RETRY_SECONDS)

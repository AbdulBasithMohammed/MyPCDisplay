#!/usr/bin/env python
"""Ranked profile for the League screen's idle state, from dpm.lol.

WHY THIS SOURCE
    Riot's own API needs a key that expires every 24h, which is useless for an
    unattended desk panel. dpm.lol serves the profile as plain JSON with no key
    and no bot challenge - verified endpoints:

        /v1/players/search?gameName=&tagLine=      puuid, ranks[]
        /v1/players/{puuid}/champions              per-champion aggregates
        /v1/players/{puuid}/match-history          real per-game results
        /v1/players/{puuid}/widgets/rank-history   daily LP/score points
        /v1/players/{puuid}/widgets/recent-performances   last-30 form

    The endpoint names came out of dpm.lol's own JS bundles rather than
    guesswork - grepping the chunks for "/v1/" is far quicker than probing,
    and probing gets rate-limited.

    Its /v1/tierlist needs undocumented params and returned empty for every
    combination tried, so champion meta comes from op.gg instead (opgg.tierlist).

    Unofficial, so every failure falls back to the disk cache and then to an
    empty profile; the screen degrades to "no profile" rather than breaking.

BEING A GOOD CITIZEN
    Five requests per REFRESH_SECONDS (15 min) while the screen is idle, all
    cached on disk. dpm.lol rate-limits bursts hard, so do not lower that.
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
QUEUE_SOLO_ID = 420          # ranked solo/duo; match-history also carries 400/440
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


def _delta(history, days):
    """Ladder-score change over the last `days`, or None if it cannot be known.

    Compares the newest point against the last one at or before the cutoff.
    Returns None rather than 0 when the history does not reach back that far,
    so the screen can stay silent instead of claiming a flat week.
    """
    if len(history) < 2:
        return None
    try:
        import datetime
        newest = history[-1]
        cutoff = (datetime.date.fromisoformat(newest["date"])
                  - datetime.timedelta(days=days))
        older = None
        for point in history[:-1]:
            if datetime.date.fromisoformat(point["date"]) <= cutoff:
                older = point
        if older is None:
            return None
        return newest["score"] - older["score"]
    except Exception:
        return None


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

    champions = []
    try:
        rows = _get(s, "/v1/players/%s/champions" % puuid, queue=SOLO)
        # Ranked by games played: "what I actually play" is more use on a desk
        # panel than a 100% win rate over two games.
        rows = sorted(rows or [], key=lambda c: -(c.get("gamesPlayed") or 0))
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

    # Real per-game results, newest first. The champion endpoint also carries a
    # recentResults list, but it is that ONE champion's history - using it made
    # the panel show W W L L L during a five-game win streak.
    recent = []
    try:
        matches = _get(s, "/v1/players/%s/match-history" % puuid).get("matches") or []
        for m in matches:
            if m.get("queueId") != QUEUE_SOLO_ID:
                continue
            for p in m.get("participants") or []:
                if p.get("puuid") == puuid:
                    recent.append(bool(p.get("win")))
                    break
            if len(recent) >= 5:
                break
    except Exception:
        pass

    # Daily ladder-score points for the LP graph. `score` is absolute and
    # continuous across tier boundaries, unlike leaguePoints which resets to 0
    # on every promotion - plotting LP alone would draw a cliff at each one.
    history, lp_30d, lp_7d = [], None, None
    try:
        hist = _get(s, "/v1/players/%s/widgets/rank-history" % puuid)
        for h in hist.get("histogram") or []:
            score = h.get("score")
            if score is None:
                continue
            history.append({"date": h.get("date") or "",
                            "score": int(score),
                            "tier": (h.get("tier") or "").title(),
                            "rank": h.get("rank") or "",
                            "lp": h.get("leaguePoints")})
        lp_30d = _delta(history, 30)
        lp_7d = _delta(history, 7)
    except Exception:
        pass

    # Last-30-games form, which is the headline dpm.lol leads with.
    recent_form = {}
    try:
        rp = _get(s, "/v1/players/%s/widgets/recent-performances" % puuid)
        games = rp.get("games") or {}
        played = int(games.get("games") or 0)
        if played:
            recent_form = {
                "games": played,
                "wins": int(games.get("wins") or 0),
                "losses": int(games.get("losses") or 0),
                "winrate": round(int(games.get("wins") or 0) / played * 100),
            }
    except Exception:
        pass

    return {
        "v": 2,
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
        "history": history,
        "lp_30d": lp_30d,
        "lp_7d": lp_7d,
        "form": recent_form,
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
            if cached.get("v") == 2:
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

#!/usr/bin/env python
"""Live League of Legends match state, turned into build guidance.

Three sources, all either local or a public CDN:

  Live Client Data API   https://127.0.0.1:2999/liveclientdata/allgamedata
      Served by the game itself while a match is running. No key, no auth and
      no rate limit - but a self-signed certificate, hence verify=False. The
      port simply refuses connections when no match is in progress, which is
      how we detect "in game".

  Data Dragon            https://ddragon.leagueoflegends.com
      Riot's static CDN: every item's cost, stats and build tree, and every
      champion's attack/magic rating. Cached on disk and refetched only when
      the patch number changes.

  league_builds.yaml
      The one piece of *opinion*. Riot stopped publishing recommended builds -
      ddragon's `recommended` block and Community Dragon's
      `recommendedItemDefaults` are both empty - and the sites that derive them
      from win rates (op.gg, u.gg, lolalytics) sit behind bot protection. So
      the build order is a local file you edit, not something scraped. Per
      champion overrides can also live in services.yaml under `league.builds`.

All state is class-level: the theme loop builds a fresh sensor object every
tick and must never block on IO.
"""
import json
import os
import threading
import time
from collections import Counter

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SERVICES_FILE = os.path.join(APP_DIR, "services.yaml")
BUILDS_FILE = os.path.join(APP_DIR, "league_builds.yaml")
CACHE_DIR = os.path.join(APP_DIR, "cache")

LIVE_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
GAMESTATS_URL = "https://127.0.0.1:2999/liveclientdata/gamestats"
DDRAGON = "https://ddragon.leagueoflegends.com"

POLL_LIVE = 2.0         # seconds between polls while a match is running
POLL_IDLE = 5.0         # ... and while it is not (a refused connect is instant)


def _cfg():
    try:
        import yaml
        with open(SERVICES_FILE, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("league") or {}
    except Exception:
        return {}


def _session():
    """A requests session that tolerates the game's self-signed certificate."""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    s = requests.Session()
    s.verify = False
    s.trust_env = False          # a system proxy must not intercept localhost
    return s


# --------------------------------------------------------------------- static
class Static:
    """Item and champion reference data from Data Dragon, cached per patch."""

    ready = False
    patch = ""
    items = {}          # id(str) -> {name, total, base, from, ad, ap, boots}
    by_name = {}        # lowercase name -> id
    champs = {}         # name -> {"attack": int, "magic": int}
    error = ""

    @classmethod
    def _cache_path(cls, patch):
        return os.path.join(CACHE_DIR, "ddragon_%s.json" % patch)

    @classmethod
    def load(cls):
        """Fetch (or read from cache) the static data. Safe to call repeatedly."""
        try:
            s = _session()
            patch = s.get(DDRAGON + "/api/versions.json", timeout=15).json()[0]
            cached = cls._cache_path(patch)
            if os.path.exists(cached):
                with open(cached, encoding="utf-8") as f:
                    blob = json.load(f)
            else:
                items = s.get("%s/cdn/%s/data/en_US/item.json" % (DDRAGON, patch),
                              timeout=30).json()["data"]
                champs = s.get("%s/cdn/%s/data/en_US/champion.json" % (DDRAGON, patch),
                               timeout=30).json()["data"]
                blob = {"items": items, "champs": champs}
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(cached, "w", encoding="utf-8") as f:
                    json.dump(blob, f)
            cls._index(blob, patch)
            cls.ready = True
            cls.error = ""
        except Exception as e:
            cls.error = type(e).__name__
        return cls.ready

    @classmethod
    def _index(cls, blob, patch):
        items, by_name = {}, {}
        for iid, it in blob["items"].items():
            gold = it.get("gold") or {}
            stats = it.get("stats") or {}
            tags = it.get("tags") or []
            items[iid] = {
                "name": it.get("name") or "?",
                "total": int(gold.get("total") or 0),
                "base": int(gold.get("base") or 0),
                "from": [str(c) for c in (it.get("from") or [])],
                "ad": float(stats.get("FlatPhysicalDamageMod") or 0),
                "ap": float(stats.get("FlatMagicDamageMod") or 0),
                "boots": "Boots" in tags,
            }
            # Name -> id needs care: several names appear twice because of Ornn
            # masterwork copies and out-of-store variants. Only real shop items
            # may claim a name.
            if (gold.get("purchasable") and it.get("inStore", True)
                    and not it.get("requiredAlly")
                    and (it.get("maps") or {}).get("11")):
                key = items[iid]["name"].strip().lower()
                prev = by_name.get(key)
                if prev is None or items[iid]["total"] > items[prev]["total"]:
                    by_name[key] = iid
        cls.items = items
        cls.by_name = by_name
        # Index champions under both the Data Dragon key and the display name:
        # the live game reports "Kai'Sa" and "Wukong" where ddragon keys them as
        # "Kaisa" and "MonkeyKing".
        cls.champs = {}
        cls.champ_key = {}
        for key, c in blob["champs"].items():
            info = {"attack": int((c.get("info") or {}).get("attack") or 0),
                    "magic": int((c.get("info") or {}).get("magic") or 0)}
            cls.champs[key] = info
            cls.champs[c.get("name") or key] = info
            cls.champ_key[key] = key
            cls.champ_key[c.get("name") or key] = key
            # champ select reports numeric ids, not names
            if c.get("key"):
                cls.champ_by_id[str(c["key"])] = c.get("name") or key
        cls.patch = patch

    champ_key = {}          # display name or ddragon key -> ddragon key
    champ_by_id = {}        # numeric champion id (str) -> display name
    _detail = {}            # ddragon key -> champion detail blob

    @classmethod
    def detail(cls, champion):
        """Per-champion data (spells, passive), fetched once and cached on disk.

        Needed to turn op.gg's ability icon names into Q/W/E: the icons are
        named freely (`AhriOrbofDeception`), but Data Dragon lists spells in
        Q, W, E, R order, so the index gives the key.
        """
        key = cls.champ_key.get(champion) or champion
        if key in cls._detail:
            return cls._detail[key]
        path = os.path.join(CACHE_DIR, "champ_%s_%s.json" % (cls.patch, key))
        blob = None
        try:
            with open(path, encoding="utf-8") as f:
                blob = json.load(f)
        except Exception:
            try:
                s = _session()
                blob = s.get("%s/cdn/%s/data/en_US/champion/%s.json"
                             % (DDRAGON, cls.patch, key), timeout=20).json()["data"][key]
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(blob, f)
            except Exception:
                blob = None
        cls._detail[key] = blob
        return blob

    @classmethod
    def spell_letters(cls, champion, icon_names):
        """Map op.gg ability icon names to Q/W/E/R for this champion."""
        d = cls.detail(champion) or {}
        order = ["Q", "W", "E", "R"]
        lookup = {}
        for i, sp in enumerate((d.get("spells") or [])[:4]):
            img = ((sp.get("image") or {}).get("full") or "").rsplit(".", 1)[0]
            if img:
                lookup[img.lower()] = order[i]
        return [lookup.get(str(n).lower(), "?") for n in icon_names]

    @classmethod
    def item_id(cls, name):
        return cls.by_name.get(str(name).strip().lower())

    @classmethod
    def name_of(cls, iid):
        return (cls.items.get(str(iid)) or {}).get("name", "")


# --------------------------------------------------------------------- builds
def load_build_order(champion):
    """Item names to build, in order. services.yaml overrides the shipped file."""
    def _read(path, key):
        try:
            import yaml
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return (data.get(key) if key else data) or {}
        except Exception:
            return {}

    override = (_cfg().get("builds") or {})
    shipped = _read(BUILDS_FILE, "builds")
    defaults = _read(BUILDS_FILE, "defaults")

    for table in (override, shipped):
        for key, order in table.items():
            if str(key).strip().lower() == str(champion).strip().lower():
                return list(order or [])

    # No entry for this champion: fall back to a generic order chosen from the
    # champion's own attack/magic rating, so a new or unlisted pick still shows
    # something sensible rather than a blank card.
    info = Static.champs.get(champion) or {}
    kind = "ap" if info.get("magic", 0) > info.get("attack", 0) else "ad"
    return list(defaults.get(kind) or [])


STARTER_COST = 500          # at or under this, an item is a first-back purchase
STARTER_GRACE = 8 * 60      # ... and pointless to recommend after this long


def next_target(build_ids, owned, game_time=0):
    """First item in the build order the player does not already own.

    Starter items are skipped once the game is past its opening: if you reach
    nineteen minutes without a Doran's Bow you are not going to buy one now,
    and telling you to would push the actually useful item off the card.
    """
    pool = Counter(str(o) for o in owned)
    for iid in build_ids:
        if pool[iid]:
            pool[iid] -= 1
            continue
        cost = (Static.items.get(str(iid)) or {}).get("total", 0)
        if game_time > STARTER_GRACE and cost <= STARTER_COST:
            continue
        return iid
    return None


def next_purchase(target, owned):
    """Walk down `target`'s build tree to the thing to actually buy next.

    Returns (item_id, gold_cost). Components already in the inventory are
    consumed, so a half-built item asks for the piece that is missing rather
    than starting again from the bottom.
    """
    pool = Counter(str(o) for o in owned)

    def walk(iid, depth=0):
        it = Static.items.get(str(iid))
        if it is None or depth > 6:
            return iid, (it or {}).get("total", 0)
        comps = it["from"]
        if not comps:
            return iid, it["total"]
        missing = []
        for c in comps:
            if pool[c]:
                pool[c] -= 1
            else:
                missing.append(c)
        if not missing:
            return iid, it["base"]      # components in hand: only the upgrade left
        # Buy the biggest missing piece first, the way you actually shop.
        biggest = max(missing, key=lambda c: (Static.items.get(c) or {}).get("total", 0))
        return walk(biggest, depth + 1)

    return walk(str(target))


def damage_split(enemies):
    """Rough AD / AP share of the enemy team, as (ad_pct, ap_pct).

    Blends each champion's innate attack/magic rating with the damage actually
    sitting in their inventory, so the read sharpens as the game goes on.
    """
    ad = ap = 0.0
    for e in enemies:
        info = Static.champs.get(e.get("champion")) or {}
        ad += float(info.get("attack", 5)) * 8
        ap += float(info.get("magic", 5)) * 8
        for iid in e.get("items", []):
            it = Static.items.get(str(iid))
            if it:
                ad += it["ad"] * 3
                ap += it["ap"] * 2      # AP numbers run larger than AD per item
    total = ad + ap
    if total <= 0:
        return 50, 50
    return int(round(ad / total * 100)), int(round(ap / total * 100))


# ------------------------------------------------------------------ champ select
# The Live Client API does not exist before the match loads, so champ select has
# to come from the League client itself (the "LCU"). It writes a lockfile
# holding the port and a per-session password:
#     LeagueClient:<pid>:<port>:<password>:https
# Auth is HTTP basic as user "riot". Certificate is self-signed, like the game's.
LOCKFILE_HINTS = [
    r"C:\Games\Riot Games\League of Legends\lockfile",
    r"C:\Riot Games\League of Legends\lockfile",
    r"C:\Program Files\Riot Games\League of Legends\lockfile",
    r"C:\Program Files (x86)\Riot Games\League of Legends\lockfile",
]


def _lockfile():
    """Path, port and password of the running client, or None."""
    paths = list(LOCKFILE_HINTS)
    # The install location is recorded here, so an unusual drive still works.
    try:
        with open(r"C:\ProgramData\Riot Games\RiotClientInstalls.json", encoding="utf-8") as f:
            for k in (json.load(f).get("associated_client") or {}):
                if "League of Legends" in k:
                    paths.insert(0, os.path.join(k.replace("/", "\\"), "lockfile"))
    except Exception:
        pass
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                parts = f.read().strip().split(":")
            if len(parts) >= 5:
                return {"port": parts[2], "password": parts[3], "proto": parts[4]}
        except Exception:
            continue
    return None


# The client's own view of where you are in the game lifecycle. This is the
# reliable signal: champ select and the live match are two separate APIs with a
# gap between them (the loading screen), and watching only those two makes the
# screen flip away exactly when the game is about to start.
ACTIVE_PHASES = ("ChampSelect", "GameStart", "InProgress", "Reconnect")
LOADING_PHASES = ("GameStart", "InProgress", "Reconnect")
DONE_PHASES = (None, "None", "Lobby", "Matchmaking", "ReadyCheck",
               "WaitingForStats", "PreEndOfGame", "EndOfGame", "TerminatedInError")


def gameflow_phase():
    """"ChampSelect" / "InProgress" / "None" ... or None if the client is closed."""
    lock = _lockfile()
    if not lock:
        return None
    try:
        s = _session()
        r = s.get("%s://127.0.0.1:%s/lol-gameflow/v1/gameflow-phase"
                  % (lock["proto"], lock["port"]),
                  auth=("riot", lock["password"]), timeout=3)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def league_busy():
    """True from lock-in until the match ends, loading screen included."""
    flow = gameflow_phase()
    if flow is not None:
        return flow in ACTIVE_PHASES
    # No client running: fall back to the game's own API, which only answers
    # during a match. Covers a game running without the client (rare).
    try:
        return _session().get(GAMESTATS_URL, timeout=3).status_code == 200
    except Exception:
        return False


def champ_select():
    """The champion you locked in, or None.

    Returns only once the pick is *completed* - hovering a champion also fills
    in championId, and showing a build for a champion you are still deciding on
    would be worse than showing nothing.
    """
    lock = _lockfile()
    if not lock:
        return None
    try:
        s = _session()
        url = "%s://127.0.0.1:%s/lol-champ-select/v1/session" % (lock["proto"], lock["port"])
        r = s.get(url, auth=("riot", lock["password"]), timeout=3)
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None

    me = data.get("localPlayerCellId")
    locked = False
    for group in (data.get("actions") or []):
        for act in group:
            if (act.get("actorCellId") == me and act.get("type") == "pick"
                    and act.get("completed")):
                locked = True
    if not locked:
        return None
    for p in (data.get("myTeam") or []):
        if p.get("cellId") == me:
            cid = str(p.get("championId") or 0)
            name = Static.champ_by_id.get(cid)
            if name:
                # assignedPosition is "" in blind pick and some queues
                return {"champion": name,
                        "position": (p.get("assignedPosition") or "").upper()}
    return None


def current_riot_id():
    """('3mperor', 'bhsdk') for whoever is signed into the client, or None.

    Lets the profile screen follow the account actually in use instead of a
    single name in services.yaml. Returns None whenever the client is closed,
    which is most of the time, so the caller must have a fallback.
    """
    lock = _lockfile()
    if not lock:
        return None
    try:
        s = _session()
        r = s.get("%s://127.0.0.1:%s/lol-summoner/v1/current-summoner"
                  % (lock["proto"], lock["port"]),
                  auth=("riot", lock["password"]), timeout=3)
        if r.status_code != 200:
            return None
        data = r.json()
        name = (data.get("gameName") or "").strip()
        tag = (data.get("tagLine") or "").strip()
        if name and tag:
            return name, tag
        # Older clients only expose "Name#TAG" as displayName.
        shown = (data.get("displayName") or "").strip()
        if "#" in shown:
            name, _, tag = shown.partition("#")
            if name.strip() and tag.strip():
                return name.strip(), tag.strip()
    except Exception:
        pass
    return None


def assigned_position():
    """Your lane in champ select, before any pick is locked in.

    Separate from champ_select(), which deliberately returns nothing until the
    pick is *completed*. The role is known much earlier than the champion, and
    it is all the meta list needs.
    """
    lock = _lockfile()
    if not lock:
        return ""
    try:
        s = _session()
        r = s.get("%s://127.0.0.1:%s/lol-champ-select/v1/session"
                  % (lock["proto"], lock["port"]),
                  auth=("riot", lock["password"]), timeout=3)
        if r.status_code != 200:
            return ""
        data = r.json()
        me = data.get("localPlayerCellId")
        for p in (data.get("myTeam") or []):
            if p.get("cellId") == me:
                return (p.get("assignedPosition") or "").upper()
    except Exception:
        pass
    return ""


# ----------------------------------------------------------------------- live
class League:
    """Cached match state, refreshed by a background poller."""

    _started = False
    _lock = threading.Lock()

    live = False                # a match is actually in progress
    status = "starting"
    champion = ""
    role = ""
    level = 0
    gold = 0.0
    kills = deaths = assists = 0
    cs = 0
    game_time = 0.0
    my_items = []               # item ids, in slot order
    enemies = []                # [{"champion": str, "items": [id]}]
    error = ""

    # derived, recomputed on each successful poll
    target_id = None
    buy_id = None
    buy_cost = 0
    build_source = ""           # "op.gg" / "op.gg (cached)" / "local file"
    build_winrate = ""
    build_games = ""
    build = {}                  # the whole op.gg payload for the current pick
    phase = "idle"              # idle | champselect | early | build
    _build_memo = {}            # (champion, role) -> payload dict

    EARLY_SECONDS = 90          # show starting items only this long; by 1:30 the
                                # opening is bought and the core build is the question
    CORE_DONE = 3               # completed items that count as "core finished"
    LEGENDARY_COST = 2200       # at or above this, and not boots, it is a real item

    @classmethod
    def legendary_count(cls):
        """How many finished items are in the inventory.

        Boots and components do not count - the point is to notice when the
        three core items are done and the fourth slot is the live question.
        """
        n = 0
        for iid in cls.my_items:
            it = Static.items.get(str(iid))
            if it and not it.get("boots") and it.get("total", 0) >= cls.LEGENDARY_COST:
                n += 1
        return n

    @classmethod
    def _build_order(cls, champion, position):
        """Item ids to build, preferring live win-rate data over the local file.

        Memoised per champion+role: this runs on every poll, and op.gg is only
        worth asking once a day (it caches to disk on top of this).
        """
        key = (champion, position or "")
        if key not in cls._build_memo:
            payload = {}
            if _cfg().get("use_opgg", True):
                try:
                    from library.sensors import opgg
                    data = opgg.fetch(champion, position, CACHE_DIR)
                    if data and data.get("order"):
                        data = dict(data)
                        data["order"] = [str(i) for i in data["order"] if str(i) in Static.items]
                        data["skill_letters"] = Static.spell_letters(
                            champion, data.get("skill_icons") or [])
                        payload = data
                except Exception:
                    payload = {}

            if not payload:
                order = [i for i in (Static.item_id(n) for n in load_build_order(champion)) if i]
                payload = {"order": order, "source": "local file", "winrate": "",
                           "games": "", "starters": order[:1], "core": order[1:4],
                           "spells": [], "skill_icons": [], "skill_letters": [],
                           "skill_levels": ""}
            cls._build_memo[key] = payload

        payload = cls._build_memo[key]
        cls.build = payload
        cls.build_source = payload.get("source") or "local file"
        cls.build_winrate = payload.get("winrate") or ""
        cls.build_games = payload.get("games") or ""
        return payload.get("order") or []

    @classmethod
    def ensure_started(cls):
        with cls._lock:
            if cls._started:
                return
            cls._started = True
            threading.Thread(target=cls._loop, name="league", daemon=True).start()

    @classmethod
    def _demo(cls):
        """A fabricated match, for previewing the screen outside a game.

        Enabled with TURING_LEAGUE_DEMO=1. Mid-game Jinx against a mostly
        physical team, part-way into her second item.
        """
        me = {"championName": "Jinx", "position": "BOTTOM", "team": "ORDER",
              "riotId": "Demo#EUW", "level": 11,
              "items": [{"itemID": int(i)} for i in
                        (Static.item_id("Doran's Bow"), Static.item_id("Berserker's Greaves"),
                         Static.item_id("B. F. Sword")) if i],
              "scores": {"kills": 7, "deaths": 2, "assists": 5, "creepScore": 184}}
        foes = [("Zed", []), ("Darius", ["Trinity Force"]), ("Caitlyn", ["Infinity Edge"]),
                ("Lee Sin", []), ("Thresh", [])]
        others = [{"championName": c, "team": "CHAOS", "riotId": c, "level": 11,
                   "items": [{"itemID": int(Static.item_id(n))} for n in items
                             if Static.item_id(n)],
                   "scores": {"kills": 1, "deaths": 2, "assists": 3, "creepScore": 90}}
                  for c, items in foes]
        return {"activePlayer": {"riotId": "Demo#EUW", "currentGold": 1180},
                "allPlayers": [me] + others,
                "gameData": {"gameTime": 1187, "gameMode": "CLASSIC"}}

    @classmethod
    def _loop(cls):
        s = None
        while True:
            if not Static.ready:
                cls.status = "loading item data"
                if not Static.load():
                    cls.status = "no item data (" + (Static.error or "offline") + ")"
                    time.sleep(30)
                    continue
            if os.environ.get("TURING_LEAGUE_DEMO"):
                try:
                    cls._ingest(cls._demo())
                except Exception as e:
                    cls.status = "demo failed: " + type(e).__name__
                time.sleep(POLL_LIVE)
                continue
            if s is None:
                s = _session()
            try:
                r = s.get(LIVE_URL, timeout=3)
                if r.status_code == 200:
                    cls._ingest(r.json())
                    time.sleep(POLL_LIVE)
                    continue
                cls._clear("no match")
            except Exception:
                # Connection refused is the normal "not in a game" answer.
                cls._clear("not in a game")
            time.sleep(POLL_IDLE)

    _last_pick = None       # survives the loading screen between the two APIs

    @classmethod
    def _clear(cls, status):
        """No match running - fall back to champ select, else go idle."""
        cls.live = False
        cls.my_items = []
        cls.enemies = []
        cls.target_id = cls.buy_id = None

        flow = gameflow_phase()
        pick = champ_select()
        if pick:
            cls._last_pick = pick
        elif flow in LOADING_PHASES and cls._last_pick:
            # Champ select has closed but the match has not opened its API yet.
            # Keep showing what was locked in rather than blanking to "No match"
            # through the whole loading screen.
            pick = cls._last_pick
        if flow in DONE_PHASES:
            cls._last_pick = None

        if pick:
            cls.champion = pick["champion"]
            cls.role = pick["position"]
            cls._build_order(cls.champion, cls.role)
            cls.phase = "champselect"
            cls.status = "locked in"
            return

        cls.champion = ""
        cls.role = ""
        cls.build = {}
        if flow == "ChampSelect":
            # In champ select but nothing locked in yet. There is no champion
            # to build for, so show what is strong in the assigned role
            # instead of an empty screen.
            cls.role = assigned_position() or ""
            cls.phase = "meta"
            cls.status = "champ select"
            return
        cls.phase = "idle"
        cls.status = status

    @classmethod
    def _ingest(cls, data):
        active = data.get("activePlayer") or {}
        players = data.get("allPlayers") or []
        game = data.get("gameData") or {}

        me_id = active.get("riotId") or active.get("summonerName") or ""
        me = None
        for p in players:
            if (p.get("riotId") or p.get("summonerName") or "") == me_id:
                me = p
                break
        if me is None and players:
            me = players[0]         # spectating, or a name mismatch
        if me is None:
            cls._clear("no players")
            return

        cls.champion = me.get("championName") or ""
        cls.level = int(me.get("level") or 0)
        cls.gold = float(active.get("currentGold") or 0)
        sc = me.get("scores") or {}
        cls.kills = int(sc.get("kills") or 0)
        cls.deaths = int(sc.get("deaths") or 0)
        cls.assists = int(sc.get("assists") or 0)
        cls.cs = int(sc.get("creepScore") or 0)
        cls.game_time = float(game.get("gameTime") or 0)
        cls.my_items = [str(i.get("itemID")) for i in (me.get("items") or [])
                        if i.get("itemID")]

        my_team = me.get("team")
        cls.enemies = [{"champion": p.get("championName") or "",
                        "items": [str(i.get("itemID")) for i in (p.get("items") or [])
                                  if i.get("itemID")]}
                       for p in players if p.get("team") != my_team]

        # Recompute the build guidance while we hold fresh data.
        cls.role = me.get("position") or ""
        order = cls._build_order(cls.champion, cls.role)
        tgt = next_target(order, cls.my_items, cls.game_time)
        cls.target_id = tgt
        if tgt:
            cls.buy_id, cls.buy_cost = next_purchase(tgt, cls.my_items)
        else:
            cls.buy_id, cls.buy_cost = None, 0

        cls.live = True
        cls.status = "in game"
        # Starting items are only useful while you can still act on them; once
        # the core is finished the question changes from "what do I build" to
        # "what do I finish with".
        if cls.game_time < cls.EARLY_SECONDS:
            cls.phase = "early"
        elif cls.legendary_count() >= cls.CORE_DONE:
            cls.phase = "late"
        else:
            cls.phase = "build"

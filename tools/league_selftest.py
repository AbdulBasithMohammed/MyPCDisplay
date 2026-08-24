#!/usr/bin/env python
"""Drives the League screen's logic with a fabricated match, no game required.

The Live Client API only exists while a match is running, so this feeds
League._ingest the same shape the game would and prints what the panel would
show. Run it after changing the build parser or the tree walk.

    venv/Scripts/python.exe tools/league_selftest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.sensors.league import League, Static, damage_split, next_purchase  # noqa: E402


def _id(x):
    """Accept an item name or a raw id, so tests read as shop names."""
    return x if str(x).isdigit() else (Static.item_id(x) or 0)


def payload(champion, position, gold, my_items, enemies, level=9, t=1140):
    """The subset of /liveclientdata/allgamedata that the screen reads."""
    me = {"championName": champion, "position": position, "team": "ORDER",
          "riotId": "Tester#EUW", "level": level,
          "items": [{"itemID": int(_id(i))} for i in my_items],
          "scores": {"kills": 7, "deaths": 2, "assists": 4, "creepScore": 168}}
    others = [{"championName": c, "team": "CHAOS", "riotId": c + "#X", "level": level,
               "items": [{"itemID": int(_id(i))} for i in items],
               "scores": {"kills": 1, "deaths": 1, "assists": 1, "creepScore": 100}}
              for c, items in enemies]
    return {"activePlayer": {"riotId": "Tester#EUW", "currentGold": gold},
            "allPlayers": [me] + others,
            "gameData": {"gameTime": t, "gameMode": "CLASSIC"}}


def show(label, data):
    League._ingest(data)
    n = Static.name_of
    print("--- %s" % label)
    print("    champ      : %s  lv%d  %s   phase=%s"
          % (League.champion, League.level, League.role, League.phase))
    print("    score      : %d/%d/%d   %d CS   %d:%02d"
          % (League.kills, League.deaths, League.assists, League.cs,
             int(League.game_time // 60), int(League.game_time % 60)))
    print("    inventory  : %s" % (", ".join(n(i) for i in League.my_items) or "empty"))
    print("    build src  : %s  %s  %s"
          % (League.build_source, League.build_winrate, League.build_games))
    print("    next item  : %s" % (n(League.target_id) or "build complete"))
    if League.buy_id:
        short = max(0, League.buy_cost - League.gold)
        print("    buy now    : %s  %dg   (have %dg%s)"
              % (n(League.buy_id), League.buy_cost, League.gold,
                 ", short %dg" % short if short else ", affordable"))
    b = League.build or {}
    sp = (b.get("spells") or [{}])[0]
    print("    spells     : %s  %s over %s games"
          % ("+".join(sp.get("icons") or []) or "-", sp.get("win", ""), sp.get("games", "")))
    print("    skills     : %s  %s"
          % (">".join(b.get("skill_letters") or []) or "-", b.get("skill_levels") or ""))
    for label in ("starters", "boots", "core", "fourth"):
        rows = (b.get(label) or [])[:2]
        for r in rows:
            print("    %-10s : %-42s %6s over %9s games"
                  % (label, " > ".join(n(i) for i in r["icons"]),
                     r.get("win", ""), r.get("games", "")))
    ad, ap = damage_split(League.enemies)
    print("    enemy dmg  : %d%% AD / %d%% AP -> %s"
          % (ad, ap, "build armor" if ad >= 60 else
             "build magic resist" if ap >= 60 else "mixed, build both"))
    print()


def main():
    if not Static.load():
        print("no Data Dragon:", Static.error)
        return 1
    print("patch", Static.patch)
    print()

    enemies_ad = [("Zed", []), ("Darius", ["Trinity Force"]), ("Caitlyn", ["Infinity Edge"]),
                  ("Lee Sin", []), ("Thresh", [])]
    enemies_ap = [("Lux", ["Luden's Echo"]), ("Veigar", ["Rabadon's Deathcap"]), ("Syndra", ["Rabadon's Deathcap"]),
                  ("Amumu", []), ("Nami", [])]

    # 1. Fresh out of the fountain: should name the very first purchase.
    show("Jinx, start of game, 500g",
         payload("Jinx", "BOTTOM", 500, [], enemies_ad, level=1, t=15))

    # 2. Mid game, part-way into an item: must ask for the missing piece only.
    show("Jinx, has Doran's Bow + boots, 1450g",
         payload("Jinx", "BOTTOM", 1450, ["Doran's Bow", "Berserker's Greaves"], enemies_ad))

    # 3. Enemy team is full AP: the recommendation must flip.
    show("Jinx vs an AP team",
         payload("Jinx", "BOTTOM", 1450, ["Doran's Bow", "Berserker's Greaves"], enemies_ap))

    # 4. A support, to prove role routing reaches a different op.gg page.
    show("Lux support",
         payload("Lux", "UTILITY", 900, ["World Atlas"], enemies_ad))

    # 5. Everything bought: the card must not crash on a finished build.
    order = (League._build_memo.get(("Jinx", "BOTTOM")) or {}).get("order", [])
    show("Jinx with the full build finished",
         payload("Jinx", "BOTTOM", 3000, order, enemies_ad))

    # 6. Phase boundaries: starting items until 3:00, core build after.
    print("--- phase boundary (EARLY_SECONDS = %ds)" % League.EARLY_SECONDS)
    for t in (30, 179, 181, 900):
        League._ingest(payload("Jinx", "BOTTOM", 500, [], enemies_ad, level=1, t=t))
        print("    t=%4ds, 0 items -> phase %s" % (t, League.phase))
    # Core finished: the question becomes 4th/5th/6th, not what to build.
    finished = ["Infinity Edge", "Bloodthirster", "Lord Dominik's Regards"]
    for owned, label in (([], "none"), (finished[:2], "2 items"), (finished, "3 items")):
        League._ingest(payload("Jinx", "BOTTOM", 3000, owned + ["Berserker's Greaves"],
                               enemies_ad, t=1500))
        print("    t=1500s, %-8s -> phase %-6s (legendary=%d)"
              % (label, League.phase, League.legendary_count()))
    print()

    # 7. The tree walk in isolation, so a wrong component is obvious.
    print("--- build tree walk for Infinity Edge")
    for owned in ([], ["1038"], ["1038", "1037"], ["1038", "1037", "1018"]):
        buy, cost = next_purchase("3031", owned)
        print("    owning %-34s -> buy %-18s %dg"
              % (", ".join(Static.name_of(o) for o in owned) or "nothing",
                 Static.name_of(buy), cost))
    return 0


if __name__ == "__main__":
    sys.exit(main())

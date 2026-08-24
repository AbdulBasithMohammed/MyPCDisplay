#!/usr/bin/env python
"""Validates every item name in league_builds.yaml against the current patch.

Names are typed by hand, and Riot renames and reworks items every patch, so a
build order silently rotting is the expected failure - this makes it loud.

    venv/Scripts/python.exe tools/check_league_builds.py
"""
import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.sensors.league import Static, BUILDS_FILE     # noqa: E402


def main():
    if not Static.load():
        print("could not load Data Dragon:", Static.error)
        return 1
    print("patch", Static.patch, "-", len(Static.by_name), "purchasable items on Summoner's Rift")
    print()

    import yaml
    with open(BUILDS_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    tables = []
    for kind, order in (data.get("defaults") or {}).items():
        tables.append(("defaults:" + kind, order))
    for champ, order in (data.get("builds") or {}).items():
        tables.append((champ, order))

    known = list(Static.by_name)
    bad, total = [], 0
    unknown_champs = []
    for label, order in tables:
        if not label.startswith("defaults:") and label not in Static.champs:
            unknown_champs.append(label)
        for name in order or []:
            total += 1
            if Static.item_id(name) is None:
                near = difflib.get_close_matches(str(name).lower(), known, n=2, cutoff=0.6)
                bad.append((label, name, [Static.items[Static.by_name[m]]["name"] for m in near]))

    if unknown_champs:
        print("champion names not on this patch:", ", ".join(unknown_champs))
        print()

    if bad:
        print("%d of %d item names do not resolve:" % (len(bad), total))
        width = max(len(b[1]) for b in bad)
        for label, name, near in bad:
            hint = ("  ->  did you mean: " + ", ".join(near)) if near else "  ->  no near match"
            print("  %-14s %-*s%s" % (label, width, name, hint))
    else:
        print("all %d item names resolve." % total)
    print()

    # A build order is only useful if the tree walk terminates on real items.
    from library.sensors.league import next_target, next_purchase
    sample = tables[0][1] if tables else []
    ids = [i for i in (Static.item_id(n) for n in sample) if i]
    if ids:
        tgt = next_target(ids, [])
        buy, cost = next_purchase(tgt, [])
        print("sanity: with an empty inventory, %s -> buy %s (%dg)"
              % (Static.name_of(tgt), Static.name_of(buy), cost))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

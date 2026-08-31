#!/usr/bin/env python
"""Exposes the League screen to the theme as a single composited image.

The screen is icon-led - champion portrait, summoner spells, ability icons,
item icons - and the theme engine can only drive text, bars and radials from a
sensor. So the whole frame is drawn in league_render.py and handed over as a
file path, which the BITMAP element blits (see library/stats.py).

What is shown depends on the phase, because what is useful changes:
    between games  -> ranked profile: rank, LP, W/L, most-played champions
    champ select   -> strongest champions in your role, until you lock in
    locked in      -> summoner spells and runes
    first 90s      -> starting items
    after that     -> core build

Every method must return a non-empty string and must never raise - DisplayText
asserts on empty text.
"""
from library.sensors.league import League
from library.sensors.sensors_custom import CustomDataSource

# Only the frame sensor is exported: sensors_custom does `from ... import *`
# and the theme looks names up in that namespace.
__all__ = ["LeagueFrame", "LeaguePhase"]


class LeagueFrame(CustomDataSource):
    """Returns the path of the frame to display."""

    _last = ""

    def as_numeric(self):
        pass

    def last_values(self):
        pass

    def as_string(self) -> str:
        League.ensure_started()
        try:
            from library.sensors import league_render
            path = league_render.render(League.champion, League.role,
                                        League.phase, League.build or {})
            if path:
                LeagueFrame._last = path
                return path
        except Exception:
            pass
        # Never return empty: keep the last good frame on screen rather than
        # blanking the panel because one render failed.
        return LeagueFrame._last or " "


class LeaguePhase(CustomDataSource):
    """Small text badge, handy while debugging what the screen thinks is going on."""

    def as_numeric(self):
        pass

    def last_values(self):
        pass

    def as_string(self) -> str:
        League.ensure_started()
        return League.status or League.phase or "idle"

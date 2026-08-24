#!/usr/bin/env python
"""CustomDataSource wrappers exposing Discord voice state to the theme.

The layout leads with *who is talking* rather than a roster: a busy channel can
hold a dozen people, and the dynamic information is the useful part.

Every method must return a non-empty string and must never raise - DisplayText
asserts on empty text.
"""
from library.sensors.discord_rpc import Discord
from library.sensors.sensors_custom import CustomDataSource, _marquee

DASH = "-"


class _TextOnly(CustomDataSource):
    def as_numeric(self):
        pass

    def last_values(self):
        pass


class DiscordChannel(_TextOnly):
    _offsets = {}

    def as_string(self):
        Discord.ensure_started()
        if not Discord.connected:
            return Discord.status or "connecting..."
        if not Discord.channel:
            return "Not in voice"
        return _marquee(Discord.channel, "chan", max_px=300, font_size=26,
                        offsets=self._offsets)


class DiscordGuild(_TextOnly):
    def as_string(self):
        Discord.ensure_started()
        return Discord.guild or DASH


class DiscordCount(_TextOnly):
    def as_string(self):
        Discord.ensure_started()
        if not Discord.connected or not Discord.channel:
            return DASH
        n = len(Discord.members)
        return str(n) + (" person" if n == 1 else " people")


SHOW_ACTIVITY_FOR = 45      # seconds a join/leave stays on the big card


class DiscordSpeaking(_TextOnly):
    """The focal line: who is talking, or failing that who just arrived.

    An idle channel would otherwise waste the largest card on the word "quiet",
    and arrivals are the next most interesting thing happening.
    """
    _offsets = {}

    def as_string(self):
        import time
        Discord.ensure_started()
        if not Discord.connected or not Discord.channel:
            return DASH

        talking = [m["name"] for m in Discord.members if m.get("speaking")]
        if talking:
            return _marquee(", ".join(talking), "speak", max_px=420, font_size=30,
                            offsets=self._offsets)

        fresh = [r for r in Discord.recent if time.time() - r["ts"] < SHOW_ACTIVITY_FOR]
        if fresh:
            joined = [r["name"] for r in fresh if r["action"] == "joined"]
            left = [r["name"] for r in fresh if r["action"] == "left"]
            if joined:
                line = ("+ " + ", ".join(dict.fromkeys(joined)) +
                        (" joined" if len(joined) == 1 else " joined"))
            else:
                line = "- " + ", ".join(dict.fromkeys(left)) + " left"
            return _marquee(line, "speak", max_px=420, font_size=30,
                            offsets=self._offsets)
        return "quiet"


class DiscordMembers(_TextOnly):
    """The full roster, scrolled if it overflows. Muted members get a dot."""
    _offsets = {}

    RECENT_FOR = 120        # a member counts as "new" for this long

    def as_string(self):
        import time
        Discord.ensure_started()
        if not Discord.connected or not Discord.channel:
            return DASH
        if not Discord.members:
            return "empty"

        now = time.time()
        # Newest arrivals first, so a busy channel still surfaces who just
        # showed up rather than burying them at the end of a scrolling line.
        ordered = sorted(Discord.members, key=lambda m: -(m.get("joined_at") or 0))
        names = []
        for m in ordered:
            fresh = (now - (m.get("joined_at") or 0)) < self.RECENT_FOR
            prefix = "+" if fresh else ("*" if m.get("muted") else "")
            names.append(prefix + m["name"])
        return _marquee("  ".join(names), "roster", max_px=420, font_size=15,
                        offsets=self._offsets)


class DiscordSelfState(_TextOnly):
    def as_string(self):
        Discord.ensure_started()
        if not Discord.connected or not Discord.channel:
            return DASH
        if Discord.self_deafened:
            return "DEAFENED"
        if Discord.self_muted:
            return "MUTED"
        return "LIVE"

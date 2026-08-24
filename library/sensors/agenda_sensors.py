#!/usr/bin/env python
"""CustomDataSource wrappers exposing Agenda data to the theme.

Kept separate from sensors_custom.py to stop that file sprawling; it star-imports
this module at the end, so `getattr(sensors_custom, "WeatherTemp")` still works.

Every method here must return a non-empty string and must never raise:
DisplayText asserts on empty text, and one raising sensor is logged and skipped.
"""
from library.sensors.agenda import Agenda
from library.sensors.sensors_custom import CustomDataSource, _marquee

DASH = "-"


def _deg(value, unit):
    if value is None:
        return DASH
    return format(round(value), "d") + "°" + unit


class _TextOnly(CustomDataSource):
    """Most agenda values are text; numeric/graph forms make no sense."""

    def as_numeric(self):
        pass

    def last_values(self):
        pass


# ------------------------------------------------------------------ weather
class WeatherTemp(_TextOnly):
    def as_string(self):
        Agenda.ensure_started()
        w = Agenda.weather
        if not w:
            return "--"
        return _deg(w.get("temp"), w.get("unit", "C"))


class WeatherDesc(_TextOnly):
    def as_string(self):
        Agenda.ensure_started()
        w = Agenda.weather
        if not w:
            return Agenda.weather_error or "loading..."
        return Agenda.describe(w.get("code"))


class WeatherFeels(_TextOnly):
    def as_string(self):
        Agenda.ensure_started()
        w = Agenda.weather
        if not w or w.get("feels") is None:
            return DASH
        return "feels " + _deg(w.get("feels"), "")     # unit shown on the main temp


class WeatherRange(_TextOnly):
    def as_string(self):
        Agenda.ensure_started()
        w = Agenda.weather
        if not w or w.get("high") is None:
            return DASH
        return _deg(w.get("high"), "") + " / " + _deg(w.get("low"), "")


class WeatherRain(_TextOnly):
    def as_string(self):
        Agenda.ensure_started()
        w = Agenda.weather
        if not w or w.get("rain") is None:
            return DASH
        return "rain " + str(int(w["rain"])) + "%"


# ----------------------------------------------------------------- calendar
class NextEventTitle(_TextOnly):
    _offsets = {}

    def as_string(self):
        ev = Agenda.upcoming(0)
        if ev is None:
            return Agenda.calendar_error or "Nothing scheduled"
        return _marquee(ev["title"], "ev0", max_px=390, font_size=28,
                        offsets=self._offsets)


class NextEventWhen(_TextOnly):
    def as_string(self):
        ev = Agenda.upcoming(0)
        return Agenda.when_text(ev) or DASH


class NextEventCountdown(_TextOnly):
    def as_string(self):
        ev = Agenda.upcoming(0)
        return Agenda.countdown_text(ev) or DASH


class _Later(_TextOnly):
    INDEX = 1
    _offsets = {}

    def as_string(self):
        ev = Agenda.upcoming(self.INDEX)
        if ev is None:
            return DASH
        line = Agenda.when_text(ev) + "  " + ev["title"]
        return _marquee(line, "later" + str(self.INDEX), max_px=420,
                        font_size=16, offsets=self._offsets)


class LaterEvent1(_Later):
    INDEX = 1


class LaterEvent2(_Later):
    INDEX = 2

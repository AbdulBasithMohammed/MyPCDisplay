#!/usr/bin/env python
"""Weather and calendar data for the Agenda screen.

Both sources are polled on a daemon thread and cached, because the theme loop
constructs a fresh sensor object every tick and calls it on the render path - a
blocking HTTP request there would stall the display.

Weather uses Open-Meteo: free, no API key, no account. Calendar reads a private
iCal (.ics) URL, which avoids the Google Cloud project and OAuth flow that the
Calendar API would otherwise require.
"""
import datetime
import json
import os
import threading
import time

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SERVICES_FILE = os.path.join(APP_DIR, "services.yaml")
GEOCODE_CACHE = os.path.join(APP_DIR, "geocode_cache.json")

# WMO weather interpretation codes, as returned by Open-Meteo.
WMO = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Violent showers",
    85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm, hail", 99: "Thunderstorm, hail",
}


def load_services():
    try:
        import yaml
        with open(SERVICES_FILE, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def geocode(place):
    """City name -> (lat, lon, resolved_name), cached on disk.

    Open-Meteo's geocoding endpoint is also key-free, so the user only ever has
    to type a city name.
    """
    try:
        with open(GEOCODE_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    if place in cache:
        c = cache[place]
        return c["lat"], c["lon"], c["name"]

    import requests
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": place, "count": 1}, timeout=10)
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        raise ValueError("could not geocode " + repr(place))
    g = results[0]
    name = g.get("name") or place
    cache[place] = {"lat": g["latitude"], "lon": g["longitude"], "name": name}
    try:
        with open(GEOCODE_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass
    return g["latitude"], g["longitude"], name


class Agenda:
    """Shared cache for the Agenda screen.

    All state is class level: the theme loop rebuilds sensor objects every tick,
    so instance attributes would not survive.
    """

    _started = False
    _lock = threading.Lock()

    WEATHER_EVERY = 600      # 10 min; weather does not move faster than this
    CALENDAR_EVERY = 300     # 5 min

    weather = {}
    events = []
    weather_error = ""
    calendar_error = ""

    # ---------------------------------------------------------------- weather
    @classmethod
    def _fetch_weather(cls):
        cfg = (load_services().get("weather") or {})
        place = cfg.get("location") or ""
        if not place:
            cls.weather_error = "no location set"
            return
        imperial = str(cfg.get("units", "metric")).lower().startswith("imp")
        lat, lon, name = geocode(place)

        import requests
        params = {
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,apparent_temperature,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto", "forecast_days": 1,
        }
        if imperial:
            params["temperature_unit"] = "fahrenheit"
        r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=12)
        r.raise_for_status()
        d = r.json()
        cur, day = d["current"], d["daily"]
        cls.weather = {
            "temp": cur.get("temperature_2m"),
            "feels": cur.get("apparent_temperature"),
            "code": cur.get("weather_code"),
            "high": (day.get("temperature_2m_max") or [None])[0],
            "low": (day.get("temperature_2m_min") or [None])[0],
            "rain": (day.get("precipitation_probability_max") or [None])[0],
            "place": name,
            "unit": "F" if imperial else "C",
        }
        cls.weather_error = ""

    # --------------------------------------------------------------- calendar
    @classmethod
    def _fetch_calendar(cls):
        cfg = (load_services().get("calendar") or {})
        url = (cfg.get("ics_url") or "").strip()
        if not url:
            cls.events = []
            cls.calendar_error = "no calendar URL set"
            return
        days = int(cfg.get("days_ahead", 7))

        import icalendar
        import recurring_ical_events

        # A local .ics path is accepted as well as a URL, which makes the screen
        # testable without publishing a real calendar.
        if os.path.exists(url):
            with open(url, "rb") as f:
                raw = f.read()
        else:
            import requests
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            raw = r.content
        cal = icalendar.Calendar.from_ical(raw)

        now = datetime.datetime.now(datetime.timezone.utc)
        found = recurring_ical_events.of(cal).between(now, now + datetime.timedelta(days=days))

        out = []
        for e in found:
            start = e["DTSTART"].dt
            all_day = not isinstance(start, datetime.datetime)
            if all_day:
                # Pin all-day events to local midnight so they sort with the rest.
                start_dt = datetime.datetime.combine(start, datetime.time.min).astimezone()
            else:
                start_dt = start if start.tzinfo else start.astimezone()
            title = str(e.get("SUMMARY") or "(no title)").strip()
            out.append({"start": start_dt, "title": title, "all_day": all_day})

        # An event already under way should not be announced as the next one.
        now_local = datetime.datetime.now(datetime.timezone.utc)
        out = [ev for ev in out if ev["all_day"] or ev["start"] >= now_local]
        out.sort(key=lambda ev: ev["start"])
        cls.events = out
        cls.calendar_error = ""

    # ------------------------------------------------------------------- loop
    @classmethod
    def ensure_started(cls):
        with cls._lock:
            if cls._started:
                return
            cls._started = True
            threading.Thread(target=cls._loop, name="agenda-poller", daemon=True).start()

    @classmethod
    def _loop(cls):
        next_weather = 0.0
        next_calendar = 0.0
        while True:
            now = time.time()
            if now >= next_weather:
                try:
                    cls._fetch_weather()
                    next_weather = now + cls.WEATHER_EVERY
                except Exception as e:
                    cls.weather_error = type(e).__name__
                    next_weather = now + 120        # back off rather than hammer
            if now >= next_calendar:
                try:
                    cls._fetch_calendar()
                    next_calendar = now + cls.CALENDAR_EVERY
                except Exception as e:
                    cls.calendar_error = type(e).__name__
                    next_calendar = now + 120
            time.sleep(5)

    # ---------------------------------------------------------------- helpers
    @classmethod
    def upcoming(cls, index):
        cls.ensure_started()
        try:
            return cls.events[index]
        except IndexError:
            return None

    @staticmethod
    def describe(code):
        return WMO.get(code, "-")

    @staticmethod
    def when_text(ev):
        """'09:30' for today, 'Mon 09:30' beyond, 'all day'/'Mon' for all-day."""
        if ev is None:
            return ""
        start = ev["start"].astimezone()
        today = datetime.datetime.now().astimezone().date()
        if ev["all_day"]:
            return "all day" if start.date() == today else start.strftime("%a")
        if start.date() == today:
            return start.strftime("%H:%M")
        return start.strftime("%a %H:%M")

    @staticmethod
    def countdown_text(ev):
        """'in 25m', 'in 3h 10m', 'in 2d'. Empty when not meaningful."""
        if ev is None or ev["all_day"]:
            return ""
        delta = ev["start"] - datetime.datetime.now(datetime.timezone.utc)
        secs = int(delta.total_seconds())
        if secs < 60:
            return "now"
        mins = secs // 60
        if mins < 60:
            return "in " + str(mins) + "m"
        hours, mins = divmod(mins, 60)
        if hours < 24:
            return "in " + str(hours) + "h " + format(mins, "02d") + "m"
        return "in " + str(hours // 24) + "d"

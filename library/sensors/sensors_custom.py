# SPDX-License-Identifier: GPL-3.0-or-later
#
# turing-smart-screen-python - a Python system monitor and library for USB-C displays like Turing Smart Screen or XuanFang
# https://github.com/mathoudebine/turing-smart-screen-python/
#
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# This file allows to add custom data source as sensors and display them in System Monitor themes
# There is no limitation on how much custom data source classes can be added to this file
# See CustomDataExample theme for the theme implementation part

import math
import platform
from abc import ABC, abstractmethod
from typing import List


# Custom data classes must be implemented in this file, inherit the CustomDataSource and implement its 2 methods
class CustomDataSource(ABC):
    @abstractmethod
    def as_numeric(self) -> float:
        # Numeric value will be used for graph and radial progress bars
        # If there is no numeric value, keep this function empty
        pass

    @abstractmethod
    def as_string(self) -> str:
        # Text value will be used for text display and radial progress bar inner text
        # Numeric value can be formatted here to be displayed as expected
        # It is also possible to return a text unrelated to the numeric value
        # If this function is empty, the numeric value will be used as string without formatting
        pass

    @abstractmethod
    def last_values(self) -> List[float]:
        # List of last numeric values will be used for plot graph
        # If you do not want to draw a line graph or if your custom data has no numeric values, keep this function empty
        pass


# Example for a custom data class that has numeric and text values
class ExampleCustomNumericData(CustomDataSource):
    # This list is used to store the last 10 values to display a line graph
    last_val = [math.nan] * 10  # By default, it is filed with math.nan values to indicate there is no data stored

    def as_numeric(self) -> float:
        # Numeric value will be used for graph and radial progress bars
        # Here a Python function from another module can be called to get data
        # Example: self.value = my_module.get_rgb_led_brightness() / audio.system_volume() ...
        self.value = 75.845

        # Store the value to the history list that will be used for line graph
        self.last_val.append(self.value)
        # Also remove the oldest value from history list
        self.last_val.pop(0)

        return self.value

    def as_string(self) -> str:
        # Text value will be used for text display and radial progress bar inner text.
        # Numeric value can be formatted here to be displayed as expected
        # It is also possible to return a text unrelated to the numeric value
        # If this function is empty, the numeric value will be used as string without formatting
        # Example here: format numeric value: add unit as a suffix, and keep 1 digit decimal precision
        return f'{self.value:>5.1f}%'
        # Important note! If your numeric value can vary in size, be sure to display it with a default size.
        # E.g. if your value can range from 0 to 9999, you need to display it with at least 4 characters every time.
        # --> return f'{self.as_numeric():>4}%'
        # Otherwise, part of the previous value can stay displayed ("ghosting") after a refresh

    def last_values(self) -> List[float]:
        # List of last numeric values will be used for plot graph
        return self.last_val


# Example for a custom data class that only has text values
class ExampleCustomTextOnlyData(CustomDataSource):
    def as_numeric(self) -> float:
        # If there is no numeric value, keep this function empty
        pass

    def as_string(self) -> str:
        # If a custom data class only has text values, it won't be possible to display graph or radial bars
        return "Python: " + platform.python_version()

    def last_values(self) -> List[float]:
        # If a custom data class only has text values, it won't be possible to display line graph
        pass


# ==============================================================================
# DeckWhiteBlue theme :: custom sensors
# ==============================================================================
import os
import re
import threading
import time
from datetime import datetime

from PIL import ImageFont

# Emoji / pictographs / variation selectors. No bundled font covers both these and
# Hangul, so they are stripped rather than rendered as tofu boxes.
_EMOJI_RE = re.compile(
    "[" 
    "\U0001F000-\U0001FAFF"  # pictographs, emoticons, transport, symbols
    "\U00002600-\U000027BF"  # misc symbols & dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U00002190-\U000021FF"  # arrows
    "\U00002B00-\U00002BFF"  # misc symbols and arrows
    "\U0000200D"             # zero-width joiner
    "]+",
    flags=re.UNICODE,
)

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "res", "fonts")
_MARQUEE_FONT = os.path.join(_FONT_DIR, "malgun", "malgun.ttf")


def _app_name(aumid: str) -> str:
    """Turn a source app user model id into something worth showing.

    Values range from a bare 'Brave' to 'Spotify.exe' to a full packaged AUMID
    like 'AppName_8wekyb3d8bbwe!App'.
    """
    if not aumid:
        return ""
    name = aumid.split("!")[0].split("\\")[-1]
    if "_" in name and name.count("_") == 1 and len(name.split("_")[1]) > 8:
        name = name.split("_")[0]          # strip packaged-app publisher hash
    if name.lower().endswith(".exe"):
        name = name[:-4]
    if "." in name:
        name = name.split(".")[-1]         # 'SpotifyAB.SpotifyMusic' -> 'SpotifyMusic'
    return name.strip()


def _clean(text: str) -> str:
    """Strip emoji and collapse whitespace so the panel never renders tofu."""
    if not text:
        return ""
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class _FontCache:
    _fonts = {}

    @classmethod
    def get(cls, size: int):
        if size not in cls._fonts:
            try:
                cls._fonts[size] = ImageFont.truetype(_MARQUEE_FONT, size)
            except Exception:
                cls._fonts[size] = ImageFont.load_default()
        return cls._fonts[size]


def _marquee(text: str, key: str, max_px: int, font_size: int, offsets: dict) -> str:
    """Return a pixel-width-bounded slice of text, scrolling it if it overflows.

    Uses real glyph metrics, so it behaves correctly for mixed Latin/Hangul/CJK
    where a character count would not.
    """
    font = _FontCache.get(font_size)
    try:
        if font.getlength(text) <= max_px:
            offsets[key] = 0
            return text
    except Exception:
        return text[:40]

    padded = text + "     -     "
    idx = offsets.get(key, 0) % len(padded)
    rotated = padded[idx:] + padded[:idx]

    # Trim to the widest slice that still fits the box.
    lo, hi = 0, len(rotated)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.getlength(rotated[:mid]) <= max_px:
            lo = mid
        else:
            hi = mid - 1
    offsets[key] = idx + 1
    return rotated[:lo] or text[:1]


class _MediaPoller:
    """Polls the Windows media session on a daemon thread.

    The theme loop constructs a fresh sensor object every tick, so all state is
    held at class level. Polling happens off the render thread because the WinRT
    calls are async and would otherwise stall the display refresh.
    """
    _started = False
    _lock = threading.Lock()
    title = ""
    artist = ""
    source = ""
    playing = False
    available = True

    @classmethod
    def ensure_started(cls):
        with cls._lock:
            if cls._started:
                return
            cls._started = True
            t = threading.Thread(target=cls._loop, name="media-poller", daemon=True)
            t.start()

    @classmethod
    def _loop(cls):
        try:
            import asyncio
            from winrt.windows.media.control import \
                GlobalSystemMediaTransportControlsSessionManager as MediaManager
        except Exception:
            cls.available = False
            return

        async def read_once():
            mgr = await MediaManager.request_async()
            session = mgr.get_current_session()
            if session is None:
                return "", "", "", False
            props = await session.try_get_media_properties_async()
            info = session.get_playback_info()
            # PlaybackStatus: 4 == PLAYING, 5 == PAUSED
            status = info.playback_status
            is_playing = int(status) == 4 if status is not None else False
            return (props.title or ""), (props.artist or ""),                    _app_name(session.source_app_user_model_id or ""), is_playing

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            try:
                title, artist, source, playing = loop.run_until_complete(read_once())
                cls.title = _clean(title)
                cls.artist = _clean(artist)
                cls.source = source
                cls.playing = playing
            except Exception:
                # Never propagate: a raise here would kill the whole custom loop.
                pass
            time.sleep(2)


class NowPlayingTitle(CustomDataSource):
    _offsets = {}

    def as_numeric(self) -> float:
        pass

    def as_string(self) -> str:
        _MediaPoller.ensure_started()
        if not _MediaPoller.available:
            return "Media API unavailable"
        title = _MediaPoller.title
        if not title:
            return "Nothing playing"          # never return "" - DisplayText asserts on it
        return _marquee(title, "title", max_px=356, font_size=20, offsets=self._offsets)

    def last_values(self):
        pass


class NowPlayingArtist(CustomDataSource):
    _offsets = {}

    def as_numeric(self) -> float:
        pass

    def as_string(self) -> str:
        _MediaPoller.ensure_started()
        if not _MediaPoller.available:
            return "-"
        if not _MediaPoller.title:
            return "-"
        # Videos and podcasts often report no artist, so fall back to the app.
        line = _MediaPoller.artist or _MediaPoller.source or "Unknown"
        if not _MediaPoller.playing:
            line = f"Paused - {line}"
        return _marquee(line, "artist", max_px=356, font_size=16, offsets=self._offsets)

    def last_values(self):
        pass


class ClockSeconds(CustomDataSource):
    """Drives the thin bar under the clock that fills once per minute."""

    def as_numeric(self) -> float:
        return float(datetime.now().second)

    def as_string(self) -> str:
        return f"{datetime.now().second:02d}"

    def last_values(self):
        pass


class _AmdTempPoller:
    """Reads CPU temperature via AMD's Ryzen Master SDK CLI, on a daemon thread.

    Why not LibreHardwareMonitor: LHM needs the ring0 driver WinRing0, which is
    on Microsoft's vulnerable-driver blocklist. With Memory Integrity (HVCI)
    enabled Windows refuses to load it, so LHM reports 0.0 for CPU temperature,
    package power and clocks. AMD's own AMDRyzenMasterDriver.sys is signed and
    loads fine, so the SDK CLI can still read the chip.

    Requires the monitor to run elevated - the CLI prints "User is not admin..."
    and exits otherwise. Polling happens off the render thread because each call
    spawns a process.
    """
    CLI = os.path.join(
        os.environ.get("ProgramFiles", "C:" + os.sep + "Program Files"),
        "AMD", "RyzenMasterSDK", "AMDRyzenMasterCLI", "bin-prebuilt",
        "AMDRyzenMasterCLI.exe",
    )
    POLL_SECONDS = 30   # each CLI call spawns a ~1.2s process; temps move slowly

    _started = False
    _lock = threading.Lock()
    value = float("nan")
    reason = ""

    @classmethod
    def ensure_started(cls):
        with cls._lock:
            if cls._started:
                return
            cls._started = True
            threading.Thread(target=cls._loop, name="amd-temp-poller", daemon=True).start()

    @classmethod
    def _read_once(cls) -> float:
        import subprocess
        out = subprocess.run(
            [cls.CLI, "--api", "GetPMTableData"],
            cwd=os.path.dirname(cls.CLI),
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        low = out.lower()
        if "not admin" in low:
            cls.reason = "needs admin"
            return float("nan")
        if "platform init failed" in low:
            # The SDK cannot reach its driver. The driver being "Running" is not
            # enough - it gets into this state and stays there until the service
            # is restarted or the machine reboots.
            cls.reason = "SDK: platform init failed (restart AMDRyzenMasterDriver service)"
            return float("nan")
        # e.g. "GetPMTableData ... cHTC Current Value: 41.783680 celsius"
        m = re.search(r"cHTC Current Value\s*:\s*([0-9]+(?:\.[0-9]+)?)", out)
        if not m:
            first = (out.strip().splitlines() or ["empty output"])[0][:60]
            cls.reason = "unexpected CLI output: " + first
            return float("nan")
        cls.reason = ""
        return float(m.group(1))

    @staticmethod
    def _is_admin() -> bool:
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    _logged = ""

    @classmethod
    def _report(cls):
        """Log the reason once, and again only if it changes."""
        if cls.reason and cls.reason != cls._logged:
            cls._logged = cls.reason
            try:
                from library.log import logger
                logger.warning("CPU temperature unavailable - %s", cls.reason)
            except Exception:
                pass

    @classmethod
    def _loop(cls):
        if not os.path.exists(cls.CLI):
            cls.reason = "Ryzen Master SDK not installed"
            cls._report()
            return
        # Check up front: without elevation the CLI does not fail fast, it hangs
        # until killed. Spawning a doomed process every few seconds forever is
        # worse than simply reporting n/a.
        if not cls._is_admin():
            cls.reason = "needs admin - run start-monitor.bat"
            cls._report()
            return
        failures = 0
        while True:
            try:
                v = cls._read_once()
                cls.value = v
                failures = 0 if v == v else failures + 1
            except Exception as e:
                cls.value = float("nan")
                cls.reason = type(e).__name__
                failures += 1
            cls._report()
            # Back off when it is not working, so a broken setup stays cheap.
            time.sleep(cls.POLL_SECONDS if failures < 3 else 60)


class CpuTemperature(CustomDataSource):
    """CPU temperature via the AMD Ryzen Master SDK, or 'n/a'.

    The built-in CPU/TEMPERATURE stat is disabled in this theme because both of
    its backends fail on this machine: LHM is locked out by the driver blocklist
    (returns 0.0), and psutil has no temperature support on Windows at all.
    NVIDIA GPU values are unaffected because they come from NVML, not ring0.
    """

    def as_numeric(self) -> float:
        _AmdTempPoller.ensure_started()
        return _AmdTempPoller.value

    def as_string(self) -> str:
        _AmdTempPoller.ensure_started()
        v = _AmdTempPoller.value
        if v != v:              # nan
            return "n/a"
        return f"{int(round(v))}°C"

    def last_values(self):
        pass


class MemoryUsage(CustomDataSource):
    """RAM as "15.0 / 31.7 GB". The built-in MEMORY stat renders raw megabytes
    ("15349 M"), which is accurate but unreadable at a glance."""

    def as_numeric(self) -> float:
        import psutil
        return float(psutil.virtual_memory().percent)

    def as_string(self) -> str:
        import psutil
        m = psutil.virtual_memory()
        gb = 1024 ** 3
        return f"{m.used / gb:.1f} / {m.total / gb:.1f} GB"

    def last_values(self):
        pass


class DiskUsage(CustomDataSource):
    """System drive as "643 / 999 GB"."""

    def as_numeric(self) -> float:
        import psutil
        try:
            return float(psutil.disk_usage(os.environ.get("SystemDrive", "C:") + os.sep).percent)
        except Exception:
            return float("nan")

    def as_string(self) -> str:
        import psutil
        gb = 1024 ** 3
        try:
            d = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + os.sep)
        except Exception:
            return "n/a"
        return f"{d.used / gb:.0f} / {d.total / gb:.0f} GB"

    def last_values(self):
        pass


class GpuVram(CustomDataSource):
    """NVIDIA VRAM as "1.8 / 16.3 GB", with percent used as the numeric value.

    GPUtil shells out to nvidia-smi, and the theme loop calls as_numeric() and
    as_string() separately on every tick, so results are cached briefly to avoid
    spawning that process twice per refresh.
    """
    _cache = (0.0, float("nan"), float("nan"))   # (timestamp, used_mb, total_mb)
    _TTL = 2.0

    @classmethod
    def _read(cls):
        now = time.time()
        ts, used, total = cls._cache
        if now - ts < cls._TTL:
            return used, total
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
            if gpus:
                used, total = float(gpus[0].memoryUsed), float(gpus[0].memoryTotal)
            else:
                used = total = float("nan")
        except Exception:
            used = total = float("nan")
        cls._cache = (now, used, total)
        return used, total

    def as_numeric(self) -> float:
        used, total = self._read()
        if used != used or total != total or not total:
            return float("nan")
        return used / total * 100.0

    def as_string(self) -> str:
        used, total = self._read()
        if used != used or total != total:
            return "n/a"
        return f"{used / 1024:.1f} / {total / 1024:.1f} GB"

    def last_values(self):
        pass


# Agenda screen sensors live in their own module to keep this file readable.
# Imported last so CustomDataSource above is already defined when they bind.
from library.sensors.agenda_sensors import *  # noqa: E402,F401

# Discord voice sensors, same pattern as the agenda ones.
from library.sensors.discord_sensors import *  # noqa: E402,F401
from library.sensors.league_sensors import *  # noqa: E402,F401

# OTP notification screen sensors, same pattern.
from library.sensors.otp_sensors import *  # noqa: E402,F401

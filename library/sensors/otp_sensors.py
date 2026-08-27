#!/usr/bin/env python
"""CustomDataSource wrappers exposing the current OTP to the DeckOTP theme.

Reads the state file the supervisor writes (library/sensors/otp.py). The child
process is spawned fresh for this screen, so there is nothing to poll here and
no thread: the file is already on disk before the switch happens.

Every method must return a non-empty string and must never raise: DisplayText
asserts on empty text, and one raising sensor is logged and skipped.
"""
import os
import time

from library.sensors import otp
from library.sensors.sensors_custom import CustomDataSource

# Only the sensor classes may reach the sensors_custom namespace: a bare
# wildcard would also export `otp`, `time` and the helpers below.
__all__ = ["OtpCode", "OtpSender", "OtpSource", "OtpCountdown"]

DASH = "-"

# The state file changes at most once per notification, but the render loop
# asks every tick. Cache briefly so a 1s theme interval is not 1 stat() per
# element per second.
_CACHE_SECONDS = 0.5
_cache = {"at": 0.0, "state": {}}


def _demo_state():
    """Fabricated notification for preview-theme.py, mirroring TURING_LEAGUE_DEMO."""
    now = time.time()
    return {"code": "419022", "sender": "GitHub", "source": "email",
            "at": now - 8, "expires_at": now + 22}


def _state():
    now = time.time()
    if now - _cache["at"] > _CACHE_SECONDS:
        state = otp.read_state() or {}
        if not state and os.environ.get("TURING_OTP_DEMO"):
            state = _demo_state()
        _cache["state"] = state
        _cache["at"] = now
    return _cache["state"]


class _TextOnly(CustomDataSource):
    def as_numeric(self):
        pass

    def last_values(self):
        pass


class OtpCode(_TextOnly):
    """The code itself, spaced so it can be read at a glance.

    Six digits at the size this screen uses are hard to read as one run; the
    thin space after every third character is the same trick banks use on
    card numbers, and costs nothing to render.
    """

    def as_string(self):
        code = (_state().get("code") or "").strip()
        if not code:
            return DASH
        if len(code) == 6:
            return code[:3] + " " + code[3:]
        if len(code) == 8:
            return code[:4] + " " + code[4:]
        return code


class OtpSender(_TextOnly):
    def as_string(self):
        return (_state().get("sender") or "").strip() or DASH


class OtpSource(_TextOnly):
    """Where the code arrived, plus which mailbox when several are watched.

    The label is only rendered when it is set, so a single-account setup still
    reads as a clean "EMAIL" rather than "EMAIL - ".
    """

    def as_string(self):
        state = _state()
        src = (state.get("source") or "").strip()
        head = {"email": "EMAIL", "notification": "NOTIFICATION"}.get(src, "OTP")
        label = (state.get("label") or "").strip()
        return "%s  -  %s" % (head, label.upper()) if label else head


class OtpCountdown(CustomDataSource):
    """Seconds left before the panel returns to the previous screen.

    The supervisor owns the actual timer; this only reports it, so the two can
    disagree by at most one render tick and nothing depends on them agreeing.
    """

    def as_numeric(self):
        state = _state()
        expires = state.get("expires_at")
        started = state.get("at")
        if not expires or not started or expires <= started:
            return float("nan")
        remaining = max(0.0, expires - time.time())
        return remaining / (expires - started) * 100.0

    def as_string(self):
        state = _state()
        expires = state.get("expires_at")
        if not expires:
            return DASH
        return "%ds" % max(0, int(round(expires - time.time())))

    def last_values(self):
        pass

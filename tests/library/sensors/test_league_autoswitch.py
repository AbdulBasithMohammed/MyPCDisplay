"""Auto-switch behaviour for the League screen.

Drives deck.league_watch with a stubbed probe so the switch, the return and
the "leave me where I put myself" rule can be checked without the League
client running.

    venv/Scripts/python.exe -m pytest tests/library/sensors/test_league_autoswitch.py -q
"""
import threading
import time

import pytest

import deck

LEAGUE_INDEX = 2


class FakeSup:
    def __init__(self, index=0):
        self.screens = [{"name": "Deck"}, {"name": "Agenda"}, {"name": "League"}]
        self.index = index
        self.switches = []
        self._stop = False

    def switch_to(self, index):
        self.index = index
        self.switches.append(self.screens[index]["name"])


def run_watch(sup, states, on_client=True, return_after=True, monkeypatch=None):
    """Feed `states` to the watcher one poll at a time, then stop it."""
    seq = list(states)
    calls = {"n": 0}

    def probe():
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        if calls["n"] >= len(seq):
            sup._stop = True
        return seq[i]

    monkeypatch.setattr(deck.time, "sleep", lambda _s: None)
    monkeypatch.setattr("library.sensors.league.client_running", probe)
    monkeypatch.setattr("library.sensors.league.league_busy", probe)
    deck.league_watch(sup, "League", return_after, None, on_client)


def test_switches_when_the_client_opens(monkeypatch):
    sup = FakeSup(index=0)
    run_watch(sup, [False, True, True], monkeypatch=monkeypatch)
    assert "League" in sup.switches
    assert sup.index == LEAGUE_INDEX


def test_returns_when_the_client_closes(monkeypatch):
    sup = FakeSup(index=1)
    # Two consecutive misses are required before leaving, so give it three.
    run_watch(sup, [True, False, False, False], monkeypatch=monkeypatch)
    assert sup.switches[0] == "League"
    assert sup.switches[-1] == "Agenda", "should land back where it started"


def test_a_single_dropped_poll_does_not_bounce_the_panel(monkeypatch):
    """One failed request to the client must not yank the screen away."""
    sup = FakeSup(index=1)
    run_watch(sup, [True, False, True, True], monkeypatch=monkeypatch)
    assert sup.switches == ["League"], "no return should have fired"
    assert sup.index == LEAGUE_INDEX


def test_manual_switch_away_is_respected(monkeypatch):
    """If you moved off the League screen yourself, stay moved."""
    sup = FakeSup(index=1)
    seq = [True, True, False, False, False]
    calls = {"n": 0}

    def probe():
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        if calls["n"] == 2:
            sup.index = 0        # user picks Deck from the tray
            sup.switches.clear()
        if calls["n"] >= len(seq):
            sup._stop = True
        return seq[i]

    monkeypatch.setattr(deck.time, "sleep", lambda _s: None)
    monkeypatch.setattr("library.sensors.league.client_running", probe)
    deck.league_watch(sup, "League", True, None, True)
    assert sup.switches == [], "the timer must not move a panel you moved"
    assert sup.index == 0


def test_return_after_false_leaves_the_screen_up(monkeypatch):
    sup = FakeSup(index=1)
    run_watch(sup, [True, False, False, False], return_after=False,
              monkeypatch=monkeypatch)
    assert sup.switches == ["League"]
    assert sup.index == LEAGUE_INDEX


def test_unknown_screen_name_disables_cleanly(monkeypatch):
    sup = FakeSup()
    monkeypatch.setattr(deck.time, "sleep", lambda _s: None)
    deck.league_watch(sup, "NoSuchScreen", True, None, True)
    assert sup.switches == []

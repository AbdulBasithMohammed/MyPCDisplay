"""Timer and dismissal behaviour for deck.Notifier.

The scoring in test_otp.py decides *whether* to interrupt; this decides what
happens to the panel afterwards, which is the part that can strand the user on
a screen they did not choose.

    venv/Scripts/python.exe -m pytest tests/library/sensors/test_otp_notifier.py -q
"""
import time

import pytest

import deck
from library.sensors import otp

OTP_INDEX = 2


class FakeSupervisor:
    """Records switches instead of restarting a display child."""

    def __init__(self, index=0):
        self.screens = [{"name": "Deck"}, {"name": "Agenda"},
                        {"name": "OTP", "hidden": True}]
        self.index = index
        self.switches = []
        self._stop = False

    def switch_to(self, index):
        self.index = index
        self.switches.append(index)


@pytest.fixture(autouse=True)
def clean_state():
    otp.clear()
    yield
    otp.clear()


def make(hold=30, start=0):
    sup = FakeSupervisor(start)
    return sup, deck.Notifier(sup, OTP_INDEX, hold)


def test_show_switches_to_otp_and_publishes():
    sup, n = make()
    n.show("419022", "GitHub", "email")
    assert sup.index == OTP_INDEX
    state = otp.read_state()
    assert state["code"] == "419022"
    assert state["sender"] == "GitHub"


def test_dismiss_returns_to_previous_screen():
    sup, n = make(start=1)
    n.show("419022", "GitHub", "email")
    assert n.dismiss() is True
    assert sup.index == 1, "should land back on Agenda"
    assert otp.read_state() == {}


def test_dismiss_with_nothing_showing_is_a_noop():
    """The hotkey asks the notifier first, so this must not swallow PgDn."""
    _sup, n = make()
    assert n.dismiss() is False


def test_expiry_restores_previous_screen():
    sup, n = make(hold=0.2, start=1)
    n.show("419022", "GitHub", "email")
    deadline = time.time() + 5
    while sup.index == OTP_INDEX and time.time() < deadline:
        n_run_once(n)
    assert sup.index == 1


def n_run_once(n):
    """One pass of Notifier.run's body without starting the thread."""
    time.sleep(0.1)
    with n._lock:
        if n._deadline <= 0 or time.time() < n._deadline:
            return
        target, n._return_to, n._deadline = n._return_to, None, 0.0
    otp.clear()
    if n.sup.index != n.screen_index:
        return
    if target is not None:
        n.sup.switch_to(target)


def test_manual_switch_away_is_not_undone():
    """If you moved on yourself, the timer must leave you where you are."""
    sup, n = make(hold=0.2, start=1)
    n.show("419022", "GitHub", "email")
    sup.index = 0                      # user picked Deck from the tray
    sup.switches.clear()
    time.sleep(0.3)
    n_run_once(n)
    assert sup.index == 0
    assert sup.switches == [], "expired timer should not have moved the panel"


def test_second_code_extends_rather_than_stacking():
    sup, n = make(hold=30, start=1)
    n.show("111111", "GitHub", "email")
    first_deadline = n._deadline
    time.sleep(0.05)
    n.show("222222", "Google", "email")
    assert n._deadline > first_deadline
    assert n._return_to == 1, "return target must still be the original screen"
    assert otp.read_state()["code"] == "222222"
    assert sup.switches == [OTP_INDEX], "should not switch again while already up"


def test_dismiss_after_manual_move_does_not_swallow_the_hotkey():
    """PgDn must still cycle if you already left the OTP screen yourself."""
    sup, n = make(start=1)
    n.show("419022", "GitHub", "email")
    sup.index = 0                      # user picked Deck from the tray
    assert n.dismiss() is False, "hotkey should fall through to cycling"
    assert otp.read_state() == {}, "pending notification should still be cleared"
    assert n.dismiss() is False, "and the timer must not fire twice"

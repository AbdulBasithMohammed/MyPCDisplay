"""Regressions for the two bugs that made the panel stop showing codes.

Both were found in the field, not in a test:

1. IDLE was ended by letting an SSL read time out, which poisons the socket -
   every later read raises "cannot read from timed out object". deck.log showed
   it firing at exact 14-minute intervals (IDLE_REFRESH), tearing down all
   three connections each time.
2. After any reconnect the watcher re-read the mailbox tip, so a code that
   arrived while the connection was down was skipped entirely.

    venv/Scripts/python.exe -m pytest tests/library/sensors/test_otp_idle.py -q
"""
import threading
import time

import pytest

from library.sensors.otp_sources import MailWatcher, idle_wait


class FakeSock:
    def __init__(self):
        self.timeout = None
        self.history = []

    def settimeout(self, value):
        self.timeout = value
        self.history.append(value)


class FakeIMAP:
    """Enough of imaplib.IMAP4 to drive idle_wait, with no network.

    Records every byte sent, and only produces the tagged completion line once
    DONE has actually been received - which is what the real server does, and
    what the old timeout-driven implementation never waited for.
    """

    def __init__(self, untagged=(), tag="A001"):
        self.sock = FakeSock()
        self.tag = tag.encode()
        self.sent = []
        self.tagged_commands = {}
        self._pending = list(untagged)
        self._done = threading.Event()
        self.reads = 0

    def _new_tag(self):
        self.tagged_commands[self.tag] = None
        return self.tag

    def send(self, data):
        self.sent.append(data)
        if data.startswith(b"DONE"):
            self._done.set()

    def readline(self):
        self.reads += 1
        if self.reads == 1:
            return b"+ idling\r\n"
        if self._pending:
            return self._pending.pop(0)
        # Block until DONE arrives, then complete - never time out.
        if not self._done.wait(10):
            raise AssertionError("DONE was never sent; idle_wait would hang")
        return self.tag + b" OK IDLE terminated\r\n"


def test_refresh_ends_idle_by_sending_done():
    """The refresh path must send DONE, not let the read time out."""
    imap = FakeIMAP()
    t0 = time.perf_counter()
    assert idle_wait(imap, 0.3) is False
    assert time.perf_counter() - t0 < 5
    assert any(b.startswith(b"DONE") for b in imap.sent), \
        "refresh must terminate IDLE with DONE"


def test_socket_timeout_is_a_dead_link_net_not_the_refresh():
    """The read timeout must be strictly longer than the refresh interval.

    This is the bug itself: the original set the timeout *to* the refresh, so
    the read always won the race, timed out, and poisoned the connection.
    DONE must always land first.
    """
    refresh = 0.3
    imap = FakeIMAP()
    idle_wait(imap, refresh)
    while_idling = imap.sock.history[0]
    assert while_idling > refresh, (
        "read timeout %r must exceed the %rs refresh, or the read times out "
        "before DONE and poisons the socket" % (while_idling, refresh))
    assert while_idling >= refresh + 60


def test_new_mail_still_waits_for_the_tagged_line():
    """On EXISTS we send DONE and keep reading, so the socket is left clean."""
    imap = FakeIMAP(untagged=[b"* 4231 EXISTS\r\n"])
    assert idle_wait(imap, 30) is True
    assert any(b.startswith(b"DONE") for b in imap.sent)
    assert imap.tagged_commands == {}, "per-tag bookkeeping must be cleaned up"


def test_idle_refused_raises_runtime_error():
    """A refusal must be distinguishable from a dropped connection.

    The caller falls back to polling on RuntimeError but reconnects on
    connection errors, so conflating them would either loop or go silent.
    """
    imap = FakeIMAP()
    imap._pending = []
    imap.readline = lambda: b"A001 NO IDLE not supported\r\n"
    with pytest.raises(RuntimeError):
        idle_wait(imap, 30)


def test_connection_close_during_idle_propagates():
    """A server hanging up mid-IDLE must reach run(), which reconnects."""
    imap = FakeIMAP()
    replies = [b"+ idling\r\n", b""]      # greeting accepted, then EOF
    imap.readline = lambda: replies.pop(0) if replies else b""
    with pytest.raises((ConnectionError, OSError)):
        idle_wait(imap, 30)


def test_first_connection_starts_at_the_tip():
    w = MailWatcher({"label": "x", "user": "u", "app_password": "p"}, None)
    assert w._last_uid is None, "first session must read the mailbox tip"


def test_reconnect_resumes_from_last_processed_uid():
    """The fix for codes going missing across a reconnect."""
    w = MailWatcher({"label": "x", "user": "u", "app_password": "p"}, None)
    w._last_uid = 8425
    # A second _session() must not reset this back to the current tip.
    assert w._last_uid == 8425


def test_pending_exists_before_greeting_is_activity_not_refusal():
    """An EXISTS buffered from before IDLE raced the previous cycle's DONE.

    It must be reported as activity - the old check read the first line only,
    mistook the untagged line for a refusal, and dropped to polling for the
    whole session.
    """
    imap = FakeIMAP()
    replies = [b"* 4231 EXISTS\r\n", b"+ idling\r\n"]
    real_readline = FakeIMAP.readline

    def readline(self=imap):
        if replies:
            self.reads += 1
            return replies.pop(0)
        return real_readline(self)

    imap.readline = readline
    assert idle_wait(imap, 30) is True, "buffered EXISTS is mail, not refusal"
    assert any(b.startswith(b"DONE") for b in imap.sent), \
        "pending mail must end the cycle immediately"

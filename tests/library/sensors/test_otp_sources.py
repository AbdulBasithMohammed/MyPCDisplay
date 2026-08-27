"""Multi-mailbox configuration and cross-source deduplication.

    venv/Scripts/python.exe -m pytest tests/library/sensors/test_otp_sources.py -q
"""
from library.sensors.otp_sources import Dispatcher, accounts


def test_flat_config_is_still_one_account():
    """A single mailbox must not need an accounts: list."""
    got = list(accounts({"enabled": True, "user": "a@x.com",
                         "app_password": "p", "host": "imap.x.com"}))
    assert len(got) == 1
    assert got[0]["user"] == "a@x.com"
    assert "enabled" not in got[0]


def test_no_account_configured_yields_nothing():
    assert list(accounts({"enabled": True})) == []


def test_accounts_inherit_shared_defaults():
    got = list(accounts({
        "enabled": True,
        "host": "imap.gmail.com", "port": 993, "max_age_seconds": 120,
        "accounts": [{"label": "personal", "user": "a@gmail.com", "app_password": "1"},
                     {"label": "work", "user": "b@corp.com",
                      "app_password": "2", "host": "outlook.office365.com"}],
    }))
    assert len(got) == 2
    assert got[0]["host"] == "imap.gmail.com", "should inherit the shared host"
    assert got[0]["max_age_seconds"] == 120
    assert got[1]["host"] == "outlook.office365.com", "per-account override wins"
    assert got[1]["port"] == 993, "unset keys still inherit"


def test_shared_defaults_are_not_shared_objects():
    """Each account gets its own dict, or one watcher could mutate another's."""
    got = list(accounts({"host": "h", "accounts": [{"user": "a"}, {"user": "b"}]}))
    got[0]["host"] = "changed"
    assert got[1]["host"] == "h"


def test_same_code_from_two_mailboxes_fires_once():
    """A code sent to two watched inboxes must interrupt the panel once."""
    seen = []
    d = Dispatcher(lambda code, sender, source, label="": seen.append((code, label)))
    assert d.offer("419022", "GitHub", "email", "personal") is True
    assert d.offer("419022", "GitHub", "email", "work") is False
    assert seen == [("419022", "personal")]


def test_different_codes_both_fire():
    seen = []
    d = Dispatcher(lambda code, sender, source, label="": seen.append(code))
    d.offer("111111", "GitHub", "email", "personal")
    d.offer("222222", "Google", "notification")
    assert seen == ["111111", "222222"]


def test_handler_failure_does_not_kill_the_watcher():
    """A raising callback must not take down the source thread."""
    def boom(*_args, **_kwargs):
        raise RuntimeError("panel is on fire")

    assert Dispatcher(boom).offer("419022", "GitHub", "email") is True

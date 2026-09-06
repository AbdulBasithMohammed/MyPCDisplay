"""CPU temperature reading, across every state the temp service can leave.

The bug these cover: the poller used to spawn the Ryzen Master CLI itself and
gave up permanently when the process was not elevated, so autostarting the deck
from a RunLevel Limited task made the panel read n/a forever. Reading a
published file instead has to degrade in a way that recovers by itself.

    venv/Scripts/python.exe -m pytest tests/library/sensors/test_cpu_temp.py -q
"""
import json
import time

import pytest

from library.sensors import sensors_custom as sc

Poller = sc._AmdTempPoller


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    """Class-level state survives between sensor ticks - and between tests."""
    monkeypatch.setattr(Poller, "STATE_FILE", str(tmp_path / "cpu_temp.json"))
    monkeypatch.setattr(Poller, "_checked_at", 0.0)
    monkeypatch.setattr(Poller, "_logged", "")
    monkeypatch.setattr(Poller, "value", float("nan"))
    monkeypatch.setattr(Poller, "reason", "")
    monkeypatch.setattr(Poller, "_direct_started", False)
    # Default to unelevated: the direct-CLI fallback must never fire in tests.
    monkeypatch.setattr(Poller, "_is_admin", staticmethod(lambda: False))
    yield


def write(state):
    with open(Poller.STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def test_a_fresh_reading_is_shown():
    write({"celsius": 41.78, "reason": "", "ts": time.time()})
    assert sc.CpuTemperature().as_string() == "42°C"
    assert Poller.reason == ""


def test_numeric_matches_the_published_value():
    write({"celsius": 55.2, "reason": "", "ts": time.time()})
    assert sc.CpuTemperature().as_numeric() == pytest.approx(55.2)


def test_a_missing_file_names_the_installer():
    got = sc.CpuTemperature().as_string()
    assert got == "n/a"
    assert "install_temp_service" in Poller.reason


def test_a_stale_reading_is_not_shown_as_current():
    """A temperature from ten minutes ago is worse than admitting n/a."""
    write({"celsius": 41.78, "reason": "", "ts": time.time() - 600})
    assert sc.CpuTemperature().as_string() == "n/a"
    assert "stale" in Poller.reason


def test_a_reading_just_inside_the_window_still_counts():
    write({"celsius": 41.78, "reason": "", "ts": time.time() - 45})
    assert sc.CpuTemperature().as_string() == "42°C"


def test_a_service_side_failure_is_surfaced_verbatim():
    write({"celsius": None, "reason": "SDK: platform init failed", "ts": time.time()})
    assert sc.CpuTemperature().as_string() == "n/a"
    assert Poller.reason == "SDK: platform init failed"


def test_a_corrupt_file_does_not_raise():
    with open(Poller.STATE_FILE, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert sc.CpuTemperature().as_string() == "n/a"
    assert "unreadable" in Poller.reason


def test_it_recovers_once_the_service_comes_back():
    """The old poller could not do this: it exited its thread and stayed dead."""
    assert sc.CpuTemperature().as_string() == "n/a"
    write({"celsius": 40.0, "reason": "", "ts": time.time()})
    Poller._checked_at = 0.0          # let the next tick past the read gate
    assert sc.CpuTemperature().as_string() == "40°C"


def test_reads_are_rate_limited_between_ticks(monkeypatch):
    """Sensors tick far faster than the service polls; do not stat every frame."""
    calls = {"n": 0}
    real = Poller._refresh.__func__

    def counting(cls):
        calls["n"] += 1
        return real(cls)

    monkeypatch.setattr(Poller, "_refresh", classmethod(counting))
    write({"celsius": 41.0, "reason": "", "ts": time.time()})
    for _ in range(20):
        sc.CpuTemperature().as_string()
    assert calls["n"] == 1, "should have read the file once, not once per tick"


def test_never_returns_an_empty_string():
    """Rule 3 of the sensor contract: DisplayText asserts on empty text."""
    for state in ({"celsius": None, "reason": "", "ts": time.time()},
                  {"celsius": None, "reason": "x", "ts": 0},
                  {}):
        write(state)
        Poller._checked_at = 0.0
        assert sc.CpuTemperature().as_string() != ""


# --- the elevated publisher ------------------------------------------------
#
# This is the only part of the deck that runs as admin, so its failure
# branches are worth pinning: each one has to produce a reason the panel can
# show rather than an exception or a silently absent file.

import importlib.util
import os

_SVC = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                    "tools", "amd_temp_service.py")
_spec = importlib.util.spec_from_file_location("amd_temp_service",
                                               os.path.abspath(_SVC))
svc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(svc)


class _Completed:
    def __init__(self, stdout):
        self.stdout = stdout


def stub_cli(monkeypatch, stdout):
    monkeypatch.setattr(svc.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(svc.subprocess, "run",
                        lambda *a, **k: _Completed(stdout))


def test_service_parses_a_real_reading(monkeypatch):
    stub_cli(monkeypatch, "GetPMTableData\ncHTC Current Value: 41.783680 celsius\n")
    celsius, reason = svc.read_once()
    assert celsius == pytest.approx(41.78368)
    assert reason == ""


def test_service_reports_missing_elevation(monkeypatch):
    """The exact string the CLI prints unelevated, observed on this machine."""
    stub_cli(monkeypatch, "User is not admin...\n")
    assert svc.read_once() == (None, "needs admin")


def test_service_reports_a_wedged_driver(monkeypatch):
    stub_cli(monkeypatch, "Platform Init Failed\n")
    celsius, reason = svc.read_once()
    assert celsius is None
    assert "platform init failed" in reason.lower()


def test_service_reports_unexpected_output(monkeypatch):
    stub_cli(monkeypatch, "something else entirely\n")
    celsius, reason = svc.read_once()
    assert celsius is None
    assert "something else" in reason


def test_service_survives_a_hung_cli(monkeypatch):
    monkeypatch.setattr(svc.os.path, "exists", lambda _p: True)

    def boom(*_a, **_k):
        raise svc.subprocess.TimeoutExpired(cmd="cli", timeout=20)

    monkeypatch.setattr(svc.subprocess, "run", boom)
    celsius, reason = svc.read_once()
    assert celsius is None
    assert "timed out" in reason


def test_service_reports_a_missing_sdk(monkeypatch):
    monkeypatch.setattr(svc.os.path, "exists", lambda _p: False)
    assert svc.read_once() == (None, "Ryzen Master SDK not installed")


def test_publish_writes_what_the_sensor_expects(tmp_path, monkeypatch):
    """The two halves agree on the schema, checked against the real reader."""
    state = tmp_path / "cpu_temp.json"
    monkeypatch.setattr(svc, "STATE_FILE", str(state))
    assert svc.publish(41.78, "") is True

    monkeypatch.setattr(Poller, "STATE_FILE", str(state))
    Poller._checked_at = 0.0
    assert sc.CpuTemperature().as_string() == "42°C"


def test_publish_leaves_no_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "STATE_FILE", str(tmp_path / "cpu_temp.json"))
    svc.publish(41.78, "")
    assert [f.name for f in tmp_path.iterdir()] == ["cpu_temp.json"], \
        "the .tmp scratch file must be renamed, not left behind"


# --- type confusion in the state file ---------------------------------------
#
# Found by security review. The read was guarded but the parsing was not, so a
# structurally valid file with wrong types raised out of as_string() and blanked
# the element on every tick - the silent-dead-sensor mode this design removes.

@pytest.mark.parametrize("state", [
    {"celsius": "hot", "reason": "", "ts": 1.0},
    {"celsius": 40, "reason": "", "ts": "soon"},
    {"celsius": [40], "reason": "", "ts": 1.0},
    {"celsius": {"c": 40}, "reason": "", "ts": 1.0},
    [1, 2],
    "just a string",
    42,
    None,
])
def test_wrong_types_degrade_instead_of_raising(state):
    write(state)
    Poller._checked_at = 0.0
    assert sc.CpuTemperature().as_string() == "n/a"
    assert Poller.reason, "a reason must be set, not left blank"


def test_a_numeric_string_is_still_accepted():
    """json.dump of a Decimal, say. Coercible is coercible."""
    write({"celsius": "41.78", "reason": "", "ts": time.time()})
    assert sc.CpuTemperature().as_string() == "42°C"

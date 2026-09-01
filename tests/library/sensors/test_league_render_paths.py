"""Path-safety regression for cached art filenames.

rank_emblem() takes its filename from dpm.lol's JSON `tier` field, which is
untrusted network input. It was originally lowercased and dropped straight
into a path, so a hostile or compromised response could escape cache/icons and
have _cached() write the downloaded bytes anywhere - "../../../../deck"
resolved to the repo root.

    venv/Scripts/python.exe -m pytest tests/library/sensors/test_league_render_paths.py -q
"""
import os

import pytest

from library.sensors import league_render as lr

TRAVERSALS = [
    "../../../../deck",
    "../../evil",
    "..\\..\\evil",
    "/etc/passwd",
    "C:\\Windows\\System32\\x",
    "diamond/../../../x",
    "diamond\x00",
]


@pytest.mark.parametrize("tier", TRAVERSALS)
def test_traversal_tier_never_fetches(tier, monkeypatch):
    """A path-shaped tier must be refused before any file is touched."""
    def explode(*_args, **_kwargs):
        raise AssertionError("_cached called with a hostile tier %r" % tier)

    monkeypatch.setattr(lr, "_cached", explode)
    assert lr.rank_emblem(tier, 32) is None


@pytest.mark.parametrize("tier", ["Diamond", "diamond", " EMERALD ", "Challenger"])
def test_real_tiers_stay_inside_the_icon_dir(tier, monkeypatch):
    seen = {}

    def capture(path, url):
        seen["path"] = path
        return None

    monkeypatch.setattr(lr, "_cached", capture)
    lr.rank_emblem(tier, 32)
    resolved = os.path.realpath(seen["path"])
    assert resolved.startswith(os.path.realpath(lr.ICON_DIR) + os.sep)


def test_every_known_tier_is_accepted():
    """The colour table and the allowed-filename set must not drift apart."""
    assert {t.lower() for t in lr.TIER_COLOR} == set(lr.TIER_SLUGS)

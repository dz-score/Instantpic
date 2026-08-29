"""Tallies the booth keeps for itself. See backend/counters.py."""
import json

import pytest

from backend.counters import Counters


@pytest.fixture
def counters(tmp_path):
    c = Counters(str(tmp_path / "state" / "counters.json"))
    c.load()
    return c


def test_an_unknown_tally_is_zero(counters):
    assert counters.get("prints_used") == 0


def test_increment_returns_the_new_value(counters):
    assert counters.increment("prints_used") == 1
    assert counters.increment("prints_used") == 2
    assert counters.get("prints_used") == 2


def test_a_tally_survives_a_restart(tmp_path):
    """The whole reason this is on disk: a booth rebooted mid-event must not
    hand back a print budget it has already spent."""
    path = str(tmp_path / "state" / "counters.json")

    first = Counters(path)
    first.load()
    first.increment("prints_used", by=41)

    second = Counters(path)
    second.load()

    assert second.get("prints_used") == 41


def test_the_directory_is_created_on_first_write(tmp_path):
    path = tmp_path / "state" / "counters.json"
    c = Counters(str(path))
    c.load()

    c.increment("prints_used")

    assert json.loads(path.read_text(encoding="utf-8")) == {"prints_used": 1}


def test_set_replaces_rather_than_adds(counters):
    counters.increment("prints_used", by=12)
    assert counters.set("prints_used", 0) == 0
    assert counters.get("prints_used") == 0


def test_an_unreadable_file_starts_from_zero_rather_than_raising(tmp_path):
    """Same rule as settings: a booth that will not boot is worse than one that
    has lost a tally."""
    path = tmp_path / "state" / "counters.json"
    path.parent.mkdir()
    path.write_text("{ this is not json", encoding="utf-8")

    c = Counters(str(path))
    c.load()

    assert c.get("prints_used") == 0
    assert c.increment("prints_used") == 1     # and it recovers on the next write


def test_junk_values_are_ignored_not_fatal(tmp_path):
    path = tmp_path / "state" / "counters.json"
    path.parent.mkdir()
    path.write_text('{"prints_used": 7, "nonsense": "abc"}', encoding="utf-8")

    c = Counters(str(path))
    c.load()

    assert c.get("prints_used") == 7
    assert c.get("nonsense") == 0


def test_a_failed_write_does_not_raise(tmp_path, mocker):
    """A tally that cannot be persisted must not fail the print that was
    reporting it — losing the count matters much less than losing the photo."""
    mocker.patch("backend.counters.log")
    c = Counters(str(tmp_path / "state" / "counters.json"))
    c.load()
    mocker.patch("backend.counters.os.replace", side_effect=OSError("read-only"))

    assert c.increment("prints_used") == 1     # in memory, even so

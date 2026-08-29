import glob
import os
import json
import pytest
from backend.settings import (
    AppSettings,
    SettingsService,
    read_settings,
    write_settings,
)


def svc(path) -> SettingsService:
    """A loaded SettingsService on `path` — what the composition root builds."""
    s = SettingsService(path)
    s.load()
    return s


# ── Reading from disk ──

def test_read_settings_missing_file(temp_config):
    """If config file doesn't exist, it should return default settings."""
    os.remove(temp_config)
    settings = read_settings(temp_config)
    # It shouldn't crash, it should return defaults
    assert settings.admin_pin == "123456"
    assert settings.countdown_duration == 3

def test_read_settings_empty_file(temp_config):
    """If config file is empty JSON, it should return default settings without KeyError."""
    settings = read_settings(temp_config)
    assert settings.admin_pin == "123456"
    assert settings.countdown_duration == 3

def test_update_partial(temp_config):
    """Updating a single setting should not overwrite or delete others."""
    settings_svc = svc(temp_config)
    assert settings_svc.get().admin_pin == "123456"

    updated = settings_svc.update({"countdown_duration": 10})

    assert updated.countdown_duration == 10
    assert updated.admin_pin == "123456"  # Did not get wiped out

    # Verify it actually saved to disk
    with open(temp_config, "r") as f:
        assert json.load(f)["countdown_duration"] == 10

    # And that a fresh read of the file agrees
    reloaded = read_settings(temp_config)
    assert reloaded.countdown_duration == 10
    assert reloaded.admin_pin == "123456"


# ── Durability ──
# A booth that won't boot is worse than a booth with the wrong PIN, so an
# unreadable config.json must degrade to defaults rather than raise — this runs
# during startup.

def test_read_settings_truncated_file(temp_config):
    """A half-written config (crash mid-save) yields defaults, not JSONDecodeError."""
    with open(temp_config, "w") as f:
        f.write('{"admin_pin":')

    settings = read_settings(temp_config)

    assert settings.admin_pin == "123456"
    assert settings.countdown_duration == 3
    # The bad file is moved aside so the next boot doesn't trip over it again.
    assert not os.path.exists(temp_config)
    backups = glob.glob(os.path.join(os.path.dirname(temp_config), "config.corrupt-*.json"))
    assert len(backups) == 1

def test_read_settings_invalid_type(temp_config):
    """Valid JSON that fails schema validation also degrades to defaults."""
    with open(temp_config, "w") as f:
        json.dump({"countdown_duration": "not-a-number"}, f)

    assert read_settings(temp_config).countdown_duration == 3

def test_write_settings_leaves_no_temp_file(temp_config):
    write_settings(temp_config, AppSettings(countdown_duration=7))

    assert glob.glob(f"{temp_config}.tmp") == []
    with open(temp_config) as f:
        assert json.load(f)["countdown_duration"] == 7

def test_write_settings_failure_preserves_original(temp_config, monkeypatch):
    """If the swap fails, the previous config survives intact — that's the whole point.

    Under the old truncate-then-write, a failure here left a destroyed file.
    """
    write_settings(temp_config, AppSettings(countdown_duration=7))
    with open(temp_config, "rb") as f:
        before = f.read()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        write_settings(temp_config, AppSettings(countdown_duration=9))

    with open(temp_config, "rb") as f:
        assert f.read() == before
    assert glob.glob(f"{temp_config}.tmp") == []


# ── SettingsService ──

def test_get_before_load_raises(temp_config):
    """An unloaded service fails loudly rather than quietly reading the file.

    A lazy fallback here would be a second wiring mechanism (Rule 19), and it is
    exactly what used to let tests silently read the developer's real config.json.
    """
    with pytest.raises(RuntimeError, match="load\\(\\) was never called"):
        SettingsService(temp_config).get()

def test_get_reads_from_memory_not_disk(temp_config):
    """Once loaded, an out-of-band edit to config.json is not picked up.

    This is the documented trade: memory is the source of truth, so hand-editing
    config.json on a running booth requires a restart.
    """
    settings_svc = svc(temp_config)
    assert settings_svc.get().countdown_duration == 3

    with open(temp_config, "w") as f:
        json.dump({"countdown_duration": 99}, f)

    assert settings_svc.get().countdown_duration == 3

def test_update_rebinds_rather_than_mutating(temp_config):
    """A settings object already handed out must not change under its holder.

    The FSM receives an AppSettings snapshot and holds it across an entire capture
    sequence. If update() mutated the cached instance in place, an admin edit would
    reach into a session already in flight and change the pacing of shots
    mid-sequence. Rebinding keeps each holder on the snapshot it started with.
    """
    settings_svc = svc(temp_config)
    held = settings_svc.get()
    assert held.countdown_duration == 3

    updated = settings_svc.update({"countdown_duration": 10})

    assert held.countdown_duration == 3, "in-flight snapshot was mutated"
    assert updated.countdown_duration == 10
    assert settings_svc.get().countdown_duration == 10
    assert settings_svc.get() is not held

def test_two_services_are_independent(temp_config, tmp_path):
    """Settings are per-instance, not process-wide — no module global left to leak."""
    other_path = str(tmp_path / "other.json")
    write_settings(other_path, AppSettings(countdown_duration=8))

    a = svc(temp_config)
    b = svc(other_path)

    assert a.get().countdown_duration == 3
    assert b.get().countdown_duration == 8


def test_non_ascii_survives_a_round_trip_unescaped(tmp_path):
    r"""An operator hand-editing config.json should see the characters they typed,
    not \u escapes — and the file must read back as what was written."""
    from backend.settings import AppSettings, read_settings, write_settings

    path = str(tmp_path / "config.json")
    write_settings(path, AppSettings(couple_names="Zoé & Mikaël",
                                     default_text="Zoé & Mikaël · 14 juin"))

    raw = open(path, encoding="utf-8").read()
    assert "Zoé & Mikaël · 14 juin" in raw
    assert "\\u00b7" not in raw

    assert read_settings(path).default_text == "Zoé & Mikaël · 14 juin"


def test_a_config_that_is_not_utf8_is_quarantined_not_fatal(tmp_path):
    """Reading MUST NOT raise — a booth that will not boot is worse than one on
    defaults (and a latin-1 file is what a hand-edit on the wrong editor makes)."""
    from backend.settings import read_settings

    path = tmp_path / "config.json"
    path.write_bytes('{"couple_names": "Zo\xe9"}'.encode("latin-1"))

    settings = read_settings(str(path))

    assert settings.couple_names == AppSettings().couple_names
    assert not path.exists()          # quarantined out of the way

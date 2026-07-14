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

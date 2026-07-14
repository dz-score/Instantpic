import glob
import os
import json
import pytest
from backend.config import (
    AppSettings,
    get_settings,
    _read_from_disk,
    save_settings,
    update_settings,
)

def test_read_from_disk_missing_file(temp_config):
    """If config file doesn't exist, it should return default settings."""
    os.remove(temp_config)
    settings = _read_from_disk()
    # It shouldn't crash, it should return defaults
    assert settings.admin_pin == "123456"
    assert settings.countdown_duration == 3

def test_read_from_disk_empty_file(temp_config):
    """If config file is empty JSON, it should return default settings without KeyError."""
    settings = _read_from_disk()
    assert settings.admin_pin == "123456"
    assert settings.countdown_duration == 3

def test_update_settings_partial(temp_config):
    """Updating a single setting should not overwrite or delete others."""
    # First, make sure we have defaults
    settings = _read_from_disk()
    assert settings.admin_pin == "123456"
    
    # Update just the countdown
    updated = update_settings({"countdown_duration": 10})
    
    # Verify the object returned
    assert updated.countdown_duration == 10
    assert updated.admin_pin == "123456"  # Did not get wiped out
    
    # Verify it actually saved to disk
    with open(temp_config, "r") as f:
        data = json.load(f)
        assert data.get("countdown_duration") == 10
        
    # Reload from disk to verify
    reloaded = _read_from_disk()
    assert reloaded.countdown_duration == 10
    assert reloaded.admin_pin == "123456"


# ── Durability ──
# A booth that won't boot is worse than a booth with the wrong PIN, so an
# unreadable config.json must degrade to defaults rather than raise: camera_provider
# calls _read_from_disk() at import time.

def test_read_from_disk_truncated_file(temp_config):
    """A half-written config (crash mid-save) yields defaults, not JSONDecodeError."""
    with open(temp_config, "w") as f:
        f.write('{"admin_pin":')

    settings = _read_from_disk()

    assert settings.admin_pin == "123456"
    assert settings.countdown_duration == 3
    # The bad file is moved aside so the next boot doesn't trip over it again.
    assert not os.path.exists(temp_config)
    backups = glob.glob(os.path.join(os.path.dirname(temp_config), "config.corrupt-*.json"))
    assert len(backups) == 1

def test_read_from_disk_invalid_type(temp_config):
    """Valid JSON that fails schema validation also degrades to defaults."""
    with open(temp_config, "w") as f:
        json.dump({"countdown_duration": "not-a-number"}, f)

    settings = _read_from_disk()

    assert settings.countdown_duration == 3

def test_save_settings_leaves_no_temp_file(temp_config):
    save_settings(AppSettings(countdown_duration=7))

    assert glob.glob(f"{temp_config}.tmp") == []
    with open(temp_config) as f:
        assert json.load(f)["countdown_duration"] == 7

def test_save_settings_failure_preserves_original(temp_config, monkeypatch):
    """If the swap fails, the previous config survives intact — that's the whole point.

    Under the old truncate-then-write, a failure here left a destroyed file.
    """
    save_settings(AppSettings(countdown_duration=7))
    with open(temp_config, "rb") as f:
        before = f.read()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        save_settings(AppSettings(countdown_duration=9))

    with open(temp_config, "rb") as f:
        assert f.read() == before
    assert glob.glob(f"{temp_config}.tmp") == []


# ── In-memory cache ──

def test_get_settings_reads_from_memory_not_disk(temp_config):
    """Once cached, an out-of-band edit to config.json is not picked up.

    This is the documented trade: memory is the source of truth, so hand-editing
    config.json on a running booth requires a restart.
    """
    assert get_settings().countdown_duration == 3

    with open(temp_config, "w") as f:
        json.dump({"countdown_duration": 99}, f)

    assert get_settings().countdown_duration == 3

def test_update_settings_rebinds_rather_than_mutating(temp_config):
    """A settings object already handed out must not change under its holder.

    The FSM receives settings as a parameter and holds that object across an entire
    capture sequence. If update_settings mutated the cached instance in place, an
    admin edit would reach into a session already in flight and change the pacing of
    shots mid-sequence. Rebinding keeps each holder on the snapshot it started with.
    """
    held = get_settings()
    assert held.countdown_duration == 3

    updated = update_settings({"countdown_duration": 10})

    assert held.countdown_duration == 3, "in-flight snapshot was mutated"
    assert updated.countdown_duration == 10
    assert get_settings().countdown_duration == 10
    assert get_settings() is not held

def test_update_settings_persists_to_disk(temp_config):
    """The cache is authoritative, but it must still survive a restart."""
    update_settings({"countdown_duration": 10})

    with open(temp_config) as f:
        assert json.load(f)["countdown_duration"] == 10

import os
import json
from backend.config import load_settings, update_settings, AppSettings

def test_load_settings_missing_file(temp_config):
    """If config file doesn't exist, it should return default settings."""
    os.remove(temp_config)
    settings = load_settings()
    # It shouldn't crash, it should return defaults
    assert settings.admin_pin == "123456"
    assert settings.countdown_duration == 3

def test_load_settings_empty_file(temp_config):
    """If config file is empty JSON, it should return default settings without KeyError."""
    settings = load_settings()
    assert settings.admin_pin == "123456"
    assert settings.countdown_duration == 3

def test_update_settings_partial(temp_config):
    """Updating a single setting should not overwrite or delete others."""
    # First, make sure we have defaults
    settings = load_settings()
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
    reloaded = load_settings()
    assert reloaded.countdown_duration == 10
    assert reloaded.admin_pin == "123456"

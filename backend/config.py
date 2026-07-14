import os
import json
from pydantic import BaseModel
from typing import List, Dict, Literal

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

class OverlayConfig(BaseModel):
    id: str
    name: str
    filename: str

class AppSettings(BaseModel):
    camera_backend: Literal["gphoto2", "mock"] = "gphoto2"
    admin_pin: str = "123456"
    welcome_message: str = "Create a Beautiful Memory"
    thank_you_message: str = "Thank you for celebrating with us!"
    # How many numbers the guest sees counted down (5 => "5,4,3,2,1").
    countdown_duration: int = 3
    # How fast those numbers tick, as a multiplier. The guest still sees all
    # countdown_duration numbers — they just run quicker, and the ring video's
    # playbackRate is scaled to match.
    #
    #     effective countdown (s) = countdown_duration / countdown_speed
    countdown_speed: float = 1.0
    # Pacing between shots in a multi-shot layout; owned by backend per Rule 14.
    # A "get ready" beat after the shutter so the guest can change pose before the
    # next count starts. Pure UX, and free — there is no shot-spacing constraint.
    shot_interval_ms: int = 500
    flash_enabled: bool = True
    max_photos_per_session: int = 3
    session_timeout: int = 120
    # Floor for a stalled capture sequence: if the browser or camera dies
    # mid-session, COUNTDOWN would strand forever. After this many seconds
    # with no shot progress the FSM resets to ATTRACT. Sized well above
    # countdown + capture + retry-once + shot interval; recovery is
    # backend-owned per Rule 14. (Ordinary completion never depends on the
    # browser: the camera reports straight to the FSM via callbacks.)
    capture_stall_timeout: float = 75.0
    show_names_on_photo: bool = True
    printer_name: str = "mock"
    printer_options: str = "fit-to-page media=4x6"
    max_photos: int = 1000
    disk_min_free_gb: float = 2.0
    couple_names: str = "Sarah & Michael"
    event_date: str = "June 14, 2026"
    default_text: str = "Sarah & Michael \u00b7 June 14, 2026"
    port: int = 8000
    selected_overlay: str = "none"
    wifi_network_name: str = "Our Wedding WiFi"
    overlays: List[OverlayConfig] = [
        OverlayConfig(id="none", name="No Frame", filename=""),
        OverlayConfig(id="blush_floral", name="Chic Blush Floral", filename="blush_floral.png"),
        OverlayConfig(id="gold_glitter", name="Elegant Gold Frame", filename="gold_glitter.png")
    ]

def load_settings() -> AppSettings:
    """Load settings from config.json."""
    if not os.path.exists(CONFIG_PATH):
        # Fallback default values
        return AppSettings()

    with open(CONFIG_PATH, "r") as f:
        data = json.load(f)
        return AppSettings(**data)

def save_settings(settings: AppSettings):
    """Save settings back to config.json."""
    with open(CONFIG_PATH, "w") as f:
        json.dump(settings.model_dump(), f, indent=2)

def update_settings(updates: Dict) -> AppSettings:
    """Update settings with a dictionary of changes and save."""
    current = load_settings()
    updated_data = current.model_dump()
    for key, value in updates.items():
        if key in updated_data:
            updated_data[key] = value
    
    updated_settings = AppSettings(**updated_data)
    save_settings(updated_settings)
    return updated_settings

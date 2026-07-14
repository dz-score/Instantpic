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
    # playbackRate is scaled to match. 1.25 plays a full 5,4,3,2,1 in 4.0s.
    #
    # This exists because the *effective* countdown length feeds the shot-spacing
    # budget below, but simply dropping numbers (a 3-count) reads as rushed to
    # guests. Speeding the count buys the time back without losing a number.
    #
    #     effective countdown (s) = countdown_duration / countdown_speed
    countdown_speed: float = 1.0
    # Pacing between shots in a multi-shot layout; owned by backend per Rule 14.
    #
    # The shot-to-shot gap
    #
    #     shot_interval_ms/1000 + countdown_duration/countdown_speed + ~0.05s
    #
    # must stay UNDER ~6s. A real capture resets the M50's live-view stall clock,
    # and the next stall lands ~6.1s later (measured: healthy live view of 6.06,
    # 6.19, 6.17, 6.16, 6.06s before each stall, every stall exactly 3.01s). Fire
    # the next shot before that and the preview worker is never mid-stall when the
    # capture wants the camera lock. Hardware-measured 2026-07-13:
    #
    #   ~4.5s   : shutter takes the lock in 19-162ms. Booth: 11/11 clean.
    #             --contention probe: 0/13 blocked, 0 stalls in 605 preview grabs
    #             (the stall never even fires — 4.5s never reaches the ~6.1s mark).
    #   ~6.0s   : the stall lands ON the shutter. --contention: 3/14 blocked ~3.0s;
    #             booth: 2/6 mid-collage shots, 2858ms and 2827ms.
    #   ~8s     : the stall started at ~6.1s and is still draining — capture waits
    #             out its remainder, ~1.0s (booth: 794-990ms). This is the shape of
    #             guest-paced shots (first shot of a session, first after a RETAKE):
    #             live view has been running unbounded, so the clock is at a random
    #             phase and nothing here can control it.
    #
    # There is NO lower bound. An earlier note here claimed captures fail below
    # ~3.5s; that was overfit to 2 samples. Across 36 booth shots the fast
    # capture_image [-1] (0.45-0.73s, vs ~1.8s healthy) appears at 3.1s, 4.8s AND
    # 8.2s spacing at ~11%, with no spacing dependence. It is the known periodic
    # fast-fail; retry-once recovers it. Tightening the gap does not cause it and
    # will not fix it.
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

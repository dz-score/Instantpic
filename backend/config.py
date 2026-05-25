import os
import json
from pydantic import BaseModel
from typing import List, Dict

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

class OverlayConfig(BaseModel):
    id: str
    name: str
    filename: str

class AppSettings(BaseModel):
    printer_name: str
    max_photos: int
    disk_min_free_gb: float
    default_text: str
    port: int
    selected_overlay: str
    overlays: List[OverlayConfig]

def load_settings() -> AppSettings:
    """Load settings from config.json."""
    if not os.path.exists(CONFIG_PATH):
        # Fallback default values
        return AppSettings(
            printer_name="mock",
            max_photos=1000,
            disk_min_free_gb=2.0,
            default_text="Our Wedding 2026",
            port=8000,
            selected_overlay="none",
            overlays=[
                OverlayConfig(id="none", name="No Frame", filename=""),
                OverlayConfig(id="blush_floral", name="Chic Blush Floral", filename="blush_floral.png"),
                OverlayConfig(id="gold_glitter", name="Elegant Gold Frame", filename="gold_glitter.png")
            ]
        )
    
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

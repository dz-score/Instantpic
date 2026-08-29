from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.settings import AppSettings, LedConfig, PrinterMockConfig, SettingsService
from backend.deps import get_led, get_settings_service, get_sse
from backend.logger import log
from backend.sse_service import SseService

router = APIRouter(tags=["config"])


class ConfigUpdateRequest(BaseModel):
    printer_name: Optional[str] = None
    printer_options: Optional[str] = None
    printer_media_low_threshold: Optional[int] = None
    print_allowance: Optional[int] = None
    max_photos: Optional[int] = None
    disk_min_free_gb: Optional[float] = None
    couple_names: Optional[str] = None
    event_date: Optional[str] = None
    default_text: Optional[str] = None
    selected_overlay: Optional[str] = None
    welcome_message: Optional[str] = None
    thank_you_message: Optional[str] = None
    countdown_duration: Optional[int] = None
    countdown_speed: Optional[float] = None
    shot_interval_ms: Optional[int] = None
    flash_enabled: Optional[bool] = None
    max_photos_per_session: Optional[int] = None
    session_timeout: Optional[int] = None
    show_names_on_photo: Optional[bool] = None
    wifi_network_name: Optional[str] = None
    # Nested blocks. Partial payloads are merged, not replaced — see _merge_block
    # below for why that needs handling here rather than in SettingsService.
    led: Optional[LedConfig] = None
    printer_mock: Optional[PrinterMockConfig] = None


class ChangePinRequest(BaseModel):
    current_pin: str
    new_pin: str = Field(min_length=6)


@router.get("/api/config", response_model=AppSettings)
async def get_config(settings_svc: SettingsService = Depends(get_settings_service)):
    """Retrieve current application settings."""
    return settings_svc.get()


@router.post("/api/config", response_model=AppSettings)
async def post_config(
    updates: ConfigUpdateRequest,
    settings_svc: SettingsService = Depends(get_settings_service),
    sse: SseService = Depends(get_sse),
    led=Depends(get_led),
):
    """Update configurations."""
    try:
        changed = {k: v for k, v in updates.model_dump(exclude_unset=True).items() if v is not None}
        for block in ("led", "printer_mock"):
            if block in changed:
                changed[block] = _merge_block(
                    getattr(settings_svc.get(), block).model_dump(), changed[block]
                )
        updated = settings_svc.update(changed)

        # Apply the ring change now rather than at the next restart. Whoever is
        # setting the booth up is standing at the venue typing an IP, and the
        # only useful feedback is the status dot going green while they watch.
        # Never fatal: a wrong host must not make the config save fail, or the
        # operator cannot correct the value they just typed.
        if "led" in changed:
            try:
                await led.reconfigure(updated)
            except Exception as e:
                log.error("config", "led_reconfigure_fail",
                          f"LED reconfigure failed: {e}")

        # Push the new config to all connected clients over SSE.
        sse.dispatch_event("config_update", updated.model_dump())
        log.info("config", "config_updated", f"Config updated: {list(changed.keys())}", data=changed)
        return updated
    except Exception as e:
        log.error("config", "config_update_fail", f"Config update failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


def _merge_block(current: dict, incoming: dict) -> dict:
    """Merge a partial nested block onto the live one, at any depth.

    SettingsService.update() replaces top-level keys outright, which is right
    for flat scalars and wrong for a nested block: a payload carrying only
    {"host": ...} would take `enabled` and the timeouts back to their model
    defaults and silently switch the ring off. model_dump(exclude_unset=True) is
    recursive, so what arrives here is exactly the keys the caller sent, and
    everything else is carried over.
    """
    merged = dict(current)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_block(merged[key], value)
        else:
            merged[key] = value
    return merged


@router.post("/api/change-pin")
async def change_pin(
    req: ChangePinRequest,
    settings_svc: SettingsService = Depends(get_settings_service),
    sse: SseService = Depends(get_sse),
):
    if req.current_pin != settings_svc.get().admin_pin:
        log.warn("config", "config_pin_fail", "PIN change attempted with wrong current PIN")
        raise HTTPException(status_code=403, detail="Invalid current PIN")
    updated = settings_svc.update({"admin_pin": req.new_pin})
    sse.dispatch_event("config_update", updated.model_dump())
    log.info("config", "config_pin_changed", "Admin PIN changed")
    return {"status": "success", "detail": "PIN updated"}

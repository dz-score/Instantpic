"""Builders for the job payloads the FSM submits to the JobQueue.

This is the single home of the FSM <-> job_queue payload schema: the dict
keys built here are exactly the keys job_queue._worker reads. The builders
are dumb assembly — deciding WHEN to enqueue and WHICH callbacks to bind is
flow control and stays in the state machine.
"""

from backend.settings import AppSettings


def compose_banner_text(settings: AppSettings) -> str:
    """Assemble the branding text printed on the photo. This is a business
    rule and must be owned by the backend, not derived in the UI."""
    if not settings.show_names_on_photo:
        return ""
    parts = [p for p in (settings.couple_names, settings.event_date) if p]
    return " · ".join(parts) if parts else (settings.default_text or "")


def process_photo_job(images: list, layout: str, settings: AppSettings,
                      on_success, on_failure) -> dict:
    """Initial capture processing; the result lands on the REVEAL screen."""
    return {
        "type": "PROCESS_PHOTO",
        "images": images,
        "layout": layout,
        "text": compose_banner_text(settings),
        "overlay_id": settings.selected_overlay or "none",
        "on_success": on_success,
        "on_failure": on_failure,
    }


def process_frame_job(images: list, layout: str, overlay_id: str,
                      settings: AppSettings, on_success, on_failure) -> dict:
    """Re-processing after the guest picks an overlay frame; the result
    moves the session to PRINTING."""
    return {
        "type": "PROCESS_FRAME",
        "images": images,
        "layout": layout,
        "text": compose_banner_text(settings),
        "overlay_id": overlay_id,
        "on_success": on_success,
        "on_failure": on_failure,
    }


def print_photo_job(filename: str, on_success, on_failure) -> dict:
    """Send the final photo to the printer; the FSM projects the real
    outcome as printStatus."""
    return {
        "type": "PRINT_PHOTO",
        "filename": filename,
        "on_success": on_success,
        "on_failure": on_failure,
    }

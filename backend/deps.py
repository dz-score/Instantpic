"""FastAPI dependencies that hand routes the services the lifespan built.

Routes used to import services directly (a module-global `camera_svc`), which is
Rule 19's "module-level singletons that other modules import to reach a service".
They now ask for what they need, and the composition root decides what that is —
so a test can build an app with a fake camera and never monkeypatch a module.

Everything here reads `request.app.state`, which main.py's lifespan populates.
"""

from fastapi import Depends, HTTPException, Request

from backend.settings import AppSettings, SettingsService
from backend.print_service import PrintService
from backend.sse_service import SseService
from backend.state_machine import StateMachine


def get_settings_service(request: Request) -> SettingsService:
    """The live settings service. Ask for this if you need to see later edits."""
    return request.app.state.settings


def get_settings(
    settings_svc: SettingsService = Depends(get_settings_service),
) -> AppSettings:
    """A settings snapshot for the duration of this request."""
    return settings_svc.get()


def get_print_service(request: Request) -> PrintService:
    """The print service the lifespan built."""
    return request.app.state.print_svc


def get_job_queue(request: Request):
    """The job queue the lifespan built.

    Routes need it for the one job a human starts rather than the FSM: a test
    print, which has to share the serial print lane with the guest's.
    """
    return request.app.state.job_queue


def get_counters(request: Request):
    """Runtime tallies (backend/counters.py), not configuration."""
    return request.app.state.counters


def get_sse(request: Request) -> SseService:
    """The SSE fan-out service."""
    return request.app.state.sse


def get_state_machine(request: Request) -> StateMachine:
    """The booth FSM."""
    return request.app.state.state_machine


def get_camera(request: Request):
    """The camera, or None when no backend is usable."""
    return request.app.state.camera


def get_led(request: Request):
    """The LED controller the lifespan built.

    Always present and always safe to call — create_led_controller returns an
    inert controller rather than None when no ring is configured.
    """
    return request.app.state.led


def require_camera(camera=Depends(get_camera)):
    """For routes that cannot work without a camera at all."""
    if camera is None:
        raise HTTPException(status_code=501, detail="gphoto2 not installed")
    return camera

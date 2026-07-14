"""Chooses the camera backend once, at import, and owns the resulting singleton.

This selection used to live in main.py, which made the entrypoint the only
module that knew which camera existed — routes and tests both had to reach into
it (`backend.main.camera_svc`) to get at the service. Owning it here lets the
routers import the camera without importing the app, and lets tests swap the
backend without monkeypatching the entrypoint.

`camera_svc` is None when no backend is usable (gphoto2 is not installed); the
camera routes turn that into a 501.
"""

from backend.config import get_settings
from backend.logger import log


def _select_backend():
    settings = get_settings()

    if settings.camera_backend == "mock":
        from backend.mock_camera import MockCameraService
        return MockCameraService()

    try:
        # camera_service imports gphoto2 at module level, so this raises
        # ImportError on machines without the library (e.g. Windows dev boxes).
        from backend.camera_service import camera_svc as gphoto2_svc
        return gphoto2_svc
    except ImportError:
        log.warn(
            "system",
            "camera_import_failed",
            "python-gphoto2 not installed. Camera functions will return errors.",
        )
        return None


camera_svc = _select_backend()


def get_camera():
    """The active camera service, or None if no backend is available.

    Reads the module global on every call so tests can patch
    `backend.camera_provider.camera_svc` and have every caller see the double.
    """
    return camera_svc

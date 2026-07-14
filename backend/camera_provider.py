"""Builds the camera backend named by the config.

This used to select the backend AND construct the service at import time, which
meant that importing this module picked a camera — and on a real booth, reached for
a device handle. Rule 19: "importing a module must have no side effects." It also
made the camera unreplaceable: tests had to monkeypatch the module global to get a
double in, and nothing could shut one down and build another.

It is now a plain factory. The composition root (main.py's lifespan) calls it once,
holds the result on app.state, and routes reach the camera through a dependency.
"""

from typing import Optional

from backend.config import AppSettings
from backend.logger import log


def create_camera(settings: AppSettings) -> Optional[object]:
    """Construct the camera backend named by `settings`.

    Returns None when no backend is usable — gphoto2 is not installed, e.g. on a
    Windows dev box. The camera routes turn that into a 501.
    """
    if settings.camera_backend == "mock":
        from backend.mock_camera import MockCameraService
        return MockCameraService()

    try:
        # camera_service imports gphoto2 at module level, so this raises ImportError
        # on machines without the library.
        from backend.camera_service import CameraService
        return CameraService()
    except ImportError:
        log.warn(
            "system",
            "camera_import_failed",
            "python-gphoto2 not installed. Camera functions will return errors.",
        )
        return None

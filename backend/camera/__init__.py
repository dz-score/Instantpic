"""Camera package.

Split by responsibility (Rule 20):

    factory.py  - create_camera(): builds the backend named by the config
    device.py   - CameraDevice: the gphoto2 handle and every locked USB primitive
    preview.py  - PreviewService: worker thread, frame buffer, MJPEG generator
    capture.py  - CaptureRunner: high-res capture jobs, retry policy, callbacks
    gate.py     - CaptureGate: the ONE owner of the preview-vs-capture rule
    service.py  - CameraService: thin facade composing the above
    mock.py     - MockCameraService: same facade contract, no hardware

Only create_camera is exported here. Importing this package must not import
gphoto2 (service.py -> device.py does, at module level) so it stays importable
on machines without the library — the factory imports the real backend lazily.
"""

from backend.camera.factory import create_camera

__all__ = ["create_camera"]

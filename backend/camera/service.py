"""CameraService — the facade the rest of the app talks to.

Composes the device (gphoto2 I/O), the gate (preview-vs-capture rule), the
preview service (worker/buffer/stream) and the capture runner (jobs/retry).
The public surface is unchanged from the pre-split single class, so the FSM,
the routes and MockCameraService parity are untouched.
"""

from backend.camera.capture import CaptureRunner
from backend.camera.device import CameraDevice
from backend.camera.gate import CaptureGate
from backend.camera.preview import PreviewService
from backend.logger import log


class CameraService:

    def __init__(self, sse):
        # Required, not defaulted to a global: an injected dependency that also
        # falls back to a module singleton is two wiring mechanisms for one
        # edge (Rule 19).
        self._sse = sse
        self._gate = CaptureGate()
        self._device = CameraDevice(notify_status=self._notify_status)
        self._runner = CaptureRunner(self._device, self._gate, sse,
                                     notify_status=self._notify_status)
        self._preview = PreviewService(self._device, self._gate, sse,
                                       status_fn=self.get_status,
                                       run_pending_capture=self._runner.run_pending,
                                       full_init=self.init)
        # The device's init-time warmup frame lands in the preview buffer so
        # the first HTTP frame is instant.
        self._device.set_warm_frame_sink(self._preview.publish_frame)
        # No threads started here — Rule 19 forbids work in a constructor.
        # init() starts the monitor and, on a successful connect, the worker.

    def _notify_status(self):
        self._sse.dispatch_event("camera_status", self.get_status())

    # ─── Back-compat attribute surface (routes, tests) ────────────

    @property
    def connected(self) -> bool:
        return self._device.connected

    @connected.setter
    def connected(self, value: bool):
        self._device.connected = value

    @property
    def camera(self):
        return self._device.camera

    @camera.setter
    def camera(self, value):
        self._device.camera = value

    # ─── Lifecycle ────────────────────────────────────────────────

    def init(self):
        # Idempotent end to end: the monitor/worker starts are guarded, and
        # device.init() returns immediately when already connected — which
        # matters because the worker's reconnect path re-enters via the device
        # and the generator's cold path re-enters via this method.
        self._preview.start_monitor()
        self._device.init()
        if self._device.connected:
            self._preview.start_worker()

    def shutdown(self):
        log.info("camera", "camera_shutdown", "Shutting down camera service...")
        self._preview.stop()
        self._device.close()

    # ─── Live view ────────────────────────────────────────────────

    def preview_generator(self):
        return self._preview.generator()

    def standby(self):
        self._preview.standby()

    def resume_preview(self):
        self._preview.resume()

    # ─── High-res capture ─────────────────────────────────────────

    def enqueue_capture(self, on_complete=None, on_failure=None) -> str:
        return self._runner.enqueue(on_complete=on_complete, on_failure=on_failure)

    # ─── Settings & status ────────────────────────────────────────

    def get_settings(self):
        return self._device.get_settings()

    def set_settings(self, new_settings):
        self._device.set_settings(new_settings)

    def get_status(self):
        return {
            "connected": self._device.connected,
            "is_capturing": self._gate.capture_in_progress,
            "error": self._device.last_error
        }


# No module-level singleton: camera.factory.create_camera() constructs this,
# and the composition root owns the instance. Importing this module must not
# pick a camera (Rule 19).

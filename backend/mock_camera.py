import os
import time
import uuid
import threading
from backend.logger import log
from backend.storage import PHOTOS_DIR
from backend.sse_service import sse_svc


class MockCameraService:
    """Mock camera service with the same decoupled architecture as CameraService.

    A background worker writes frames into a shared buffer.
    The MJPEG generator reads from the buffer via Condition.wait().
    """

    def __init__(self):
        self.connected = False
        self._capture_in_progress = False
        self._last_error = None
        self._shutdown_event = threading.Event()
        self._preview_allowed = threading.Event()
        self._preview_allowed.set()
        self._preview_generation = 0
        self.lock = threading.Lock()

        # Decoupled frame buffer
        self._latest_frame = None
        self._frame_condition = threading.Condition()
        self._worker_thread = None
        self._worker_running = False

        # 1x1 black JPEG used for both preview and capture
        self._black_jpeg = bytes.fromhex(
            "ffd8ffe000104a46494600010101006000600000ffdb0043000806060706050807"
            "07070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e272022"
            "2c231c1c2837292c30313434341f27393d38323c2e333432ffdb004301090909"
            "0c0b0c180d0d1832211c213232323232323232323232323232323232323232323"
            "2323232323232323232323232323232323232323232323232ffc0001108000100"
            "0103011100021101031101ffc40015000101000000000000000000000000000000"
            "09ffc40014100100000000000000000000000000000000ffc400150101010000000"
            "00000000000000000000000009ffc40014110100000000000000000000000000000"
            "000ffda000c03010002110311003f00a0000ffd9"
        )

    def init(self):
        with self.lock:
            log.info("camera", "camera_init", "Initializing MOCK camera...")
            self.connected = True
            self._last_error = None
            sse_svc.dispatch_event("camera_status", self.get_status())
            log.info("camera", "camera_ready", "MOCK camera ready")
            self._start_worker()

    def _start_worker(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_running = True
        self._worker_thread = threading.Thread(target=self._camera_worker, daemon=True, name="mock-camera-worker")
        self._worker_thread.start()

    def _camera_worker(self):
        """Background thread: produces mock frames into the shared buffer."""
        while not self._shutdown_event.is_set():
            if not self._preview_allowed.wait(timeout=0.5):
                continue
            if self._shutdown_event.is_set():
                break

            with self._frame_condition:
                self._latest_frame = self._black_jpeg
                self._frame_condition.notify_all()

            time.sleep(0.1)  # ~10fps

        self._worker_running = False

    def preview_generator(self):
        self._preview_generation += 1
        my_gen = self._preview_generation

        if not self.connected:
            self.init()
        self._start_worker()

        while my_gen == self._preview_generation and not self._shutdown_event.is_set():
            with self._frame_condition:
                got_frame = self._frame_condition.wait(timeout=2.0)
                if not got_frame:
                    continue
                frame = self._latest_frame

            if frame is None:
                continue
            if my_gen != self._preview_generation:
                return

            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

    def capture(self):
        log.info("camera", "camera_capture_start", "MOCK capture started")

        self._capture_in_progress = True
        self._preview_allowed.clear()
        sse_svc.dispatch_event("camera_status", self.get_status())

        time.sleep(1)  # Simulate shutter lag

        filename = f"capture_{uuid.uuid4().hex[:8]}_mock.jpg"
        save_path = os.path.join(PHOTOS_DIR, filename)

        with open(save_path, "wb") as f:
            f.write(self._black_jpeg)

        self._capture_in_progress = False
        self._preview_allowed.set()
        sse_svc.dispatch_event("camera_status", self.get_status())

        log.info("camera", "camera_capture_done", f"MOCK capture saved: {filename}")
        return filename

    def get_settings(self):
        return {"status": "connected", "mock": True}

    def set_settings(self, new_settings):
        log.info("camera", "camera_settings_updated", f"MOCK camera settings updated: {new_settings}")

    def get_status(self):
        return {
            "connected": self.connected,
            "is_capturing": self._capture_in_progress,
            "error": self._last_error
        }

    def shutdown(self):
        log.info("camera", "camera_shutdown", "Shutting down MOCK camera service...")
        self._shutdown_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3)
        self.connected = False
        sse_svc.dispatch_event("camera_status", self.get_status())

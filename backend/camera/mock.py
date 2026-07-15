import asyncio
import os
import time
import uuid
import threading
from backend import storage
from backend.logger import log


class MockCameraService:
    """Mock camera service with the same decoupled architecture as CameraService.

    A background worker writes frames into a shared buffer.
    The MJPEG generator reads from the buffer via Condition.wait().
    """

    def __init__(self, sse):
        self.connected = False
        # Required, not defaulted to a global — same reason as CameraService (Rule 19).
        self._sse = sse
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

        # Dark JPEG used for both preview and capture. Generated with PIL so
        # it is genuinely decodable — captured mock files flow into
        # photo_processor (PROCESS_PHOTO) now that capture completion is
        # backend-owned, and a hand-crafted byte blob broke there.
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (640, 480), (18, 18, 22)).save(buf, format="JPEG")
        self._black_jpeg = buf.getvalue()

    def init(self):
        with self.lock:
            log.info("camera", "camera_init", "Initializing MOCK camera...")
            self.connected = True
            self._last_error = None
            self._sse.dispatch_event("camera_status", self.get_status())
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

    def _emit_job_state(self, job_id: str, status: str, filename: str = None, error: str = None):
        payload = {"job_id": job_id, "status": status, "filename": filename, "error": error}
        self._sse.dispatch_event("camera_job", payload)
        log.info("camera", f"capture_{status}", f"MOCK capture job {job_id} is {status}", data=payload)

    def enqueue_capture(self, on_complete=None, on_failure=None) -> str:
        """Same contract as CameraService.enqueue_capture: emits the granular
        camera_job SSE stages (presentation-only) and delivers the terminal
        outcome to the submitter's coroutines on the caller's event loop."""
        callbacks_loop = asyncio.get_running_loop() if (on_complete or on_failure) else None
        job_id = uuid.uuid4().hex[:8]
        self._emit_job_state(job_id, "pending")

        def _run():
            self._emit_job_state(job_id, "started")
            self._emit_job_state(job_id, "fired")
            try:
                self._capture_in_progress = True
                self._preview_allowed.clear()
                time.sleep(1)  # simulate shutter lag
                self._emit_job_state(job_id, "downloading")
                filename = f"capture_{job_id}_mock.jpg"
                with open(os.path.join(storage.PHOTOS_DIR, filename), "wb") as f:
                    f.write(self._black_jpeg)
                self._capture_in_progress = False
                self._preview_allowed.set()
                self._emit_job_state(job_id, "completed", filename=filename)
                if on_complete and callbacks_loop:
                    asyncio.run_coroutine_threadsafe(on_complete(filename), callbacks_loop)
            except Exception as e:
                self._capture_in_progress = False
                self._preview_allowed.set()
                self._emit_job_state(job_id, "failed", error=str(e))
                if on_failure and callbacks_loop:
                    asyncio.run_coroutine_threadsafe(on_failure(str(e)), callbacks_loop)

        threading.Thread(target=_run, daemon=True, name="mock-capture").start()
        return job_id

    def standby(self):
        """Parity with CameraService: pause the mock preview worker."""
        if self._preview_allowed.is_set():
            self._preview_allowed.clear()
            self._sse.dispatch_event("camera_status", self.get_status())

    def resume_preview(self):
        """Parity with CameraService: wake the mock preview worker."""
        if not self._preview_allowed.is_set():
            self._preview_allowed.set()
            self._sse.dispatch_event("camera_status", self.get_status())

    def capture(self):
        log.info("camera", "camera_capture_start", "MOCK capture started")

        self._capture_in_progress = True
        self._preview_allowed.clear()
        self._sse.dispatch_event("camera_status", self.get_status())

        time.sleep(1)  # Simulate shutter lag

        filename = f"capture_{uuid.uuid4().hex[:8]}_mock.jpg"
        save_path = os.path.join(storage.PHOTOS_DIR, filename)

        with open(save_path, "wb") as f:
            f.write(self._black_jpeg)

        self._capture_in_progress = False
        self._preview_allowed.set()
        self._sse.dispatch_event("camera_status", self.get_status())

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
        self._sse.dispatch_event("camera_status", self.get_status())

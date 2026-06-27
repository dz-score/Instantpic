import os
import time
import uuid
import threading
from backend.logger import log
from backend.storage import PHOTOS_DIR
from backend.sse_service import sse_svc

class MockCameraService:
    def __init__(self):
        self.connected = False
        self._capture_in_progress = False
        self._last_error = None
        self._shutdown_event = threading.Event()
        self._preview_allowed = threading.Event()
        self._preview_allowed.set()
        self._preview_generation = 0
        self.lock = threading.Lock()

    def init(self):
        with self.lock:
            log.info("camera", "camera_init", "Initializing MOCK camera...")
            self.connected = True
            self._last_error = None
            sse_svc.dispatch_event("camera_status", self.get_status())
            log.info("camera", "camera_ready", "MOCK camera ready")

    def preview_generator(self):
        self._preview_generation += 1
        my_gen = self._preview_generation

        if not self.connected and my_gen == self._preview_generation:
            self.init()

        # We'll just yield a very simple empty frame or we could skip it.
        # To emulate a real stream, we generate a small 1x1 black JPEG frame.
        black_jpeg = bytes.fromhex("ffd8ffe000104a46494600010101006000600000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffdb0043010909090c0b0c180d0d1832211c213232323232323232323232323232323232323232323232323232323232323232323232323232323232323232323232323232ffc00011080001000103011100021101031101ffc4001500010100000000000000000000000000000009ffc40014100100000000000000000000000000000000ffc4001501010100000000000000000000000000000009ffc40014110100000000000000000000000000000000ffda000c03010002110311003f00a0000ffd9")

        while my_gen == self._preview_generation and not self._shutdown_event.is_set():
            allowed = self._preview_allowed.wait(timeout=0.5)
            if not allowed:
                continue
                
            if my_gen != self._preview_generation:
                return
                
            try:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + black_jpeg + b'\r\n')
                time.sleep(0.1) # 10fps
            except Exception:
                time.sleep(0.5)

    def capture(self):
        log.info("camera", "camera_capture_start", "MOCK capture started")
        
        self._capture_in_progress = True
        self._preview_allowed.clear()
        sse_svc.dispatch_event("camera_status", self.get_status())
        
        time.sleep(1) # Simulate shutter lag and download time
        
        # Create a mock image (just write the black jpeg for simplicity)
        filename = f"capture_{uuid.uuid4().hex[:8]}_mock.jpg"
        save_path = os.path.join(PHOTOS_DIR, filename)
        
        # We'll use a black jpeg for testing
        black_jpeg = bytes.fromhex("ffd8ffe000104a46494600010101006000600000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffdb0043010909090c0b0c180d0d1832211c2132323232323232323232323232323232323232323232323232323232323232323232323232323232323232323232323232ffc00011080001000103011100021101031101ffc400150001010000000000000000000000000000000009ffc40014100100000000000000000000000000000000ffc400150101010000000000000000000000000000000009ffc40014110100000000000000000000000000000000ffda000c03010002110311003f00a0000ffd9")
        
        with open(save_path, "wb") as f:
            f.write(black_jpeg)
            
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
        self.connected = False
        sse_svc.dispatch_event("camera_status", self.get_status())

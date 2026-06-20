import threading
import time
import os
import uuid
import gphoto2 as gp
from backend.logger import log
from backend.storage import PHOTOS_DIR

class CameraService:
    def __init__(self):
        self.camera = None
        self.lock = threading.Lock()
        self.connected = False
        # Event-based synchronization: set = preview may run, clear = preview must pause
        self._preview_allowed = threading.Event()
        self._preview_allowed.set()  # Start with preview allowed
        self._capture_in_progress = False
        # Generation counter — only the latest generator does work, older ones exit
        self._preview_generation = 0
        # Cooldown to prevent rapid-fire re-init from preview loop
        self._last_init_time = 0

    def init(self):
        with self.lock:
            try:
                if self.camera:
                    try:
                        self.camera.exit()
                    except Exception:
                        pass
                
                log.info("camera", "camera_init", "Initializing gphoto2 camera...")
                self.camera = gp.Camera()
                self.camera.init()
                
                # Try to set capturetarget to internal RAM
                try:
                    config = self.camera.get_config()
                    ok, capture_target = gp.gp_widget_get_child_by_name(config, 'capturetarget')
                    if ok >= gp.GP_OK:
                        choices = [capture_target.get_choice(i) for i in range(capture_target.count_choices())]
                        for choice in choices:
                            if 'RAM' in choice or 'Internal' in choice:
                                capture_target.set_value(choice)
                                break
                        self.camera.set_config(config)
                except Exception as e:
                    log.warn("camera", "camera_config_warn", f"Could not set capture target: {e}")
                    
                self.connected = True
                self._last_init_time = time.monotonic()
                log.info("camera", "camera_ready", "Camera initialized successfully")
            except gp.GPhoto2Error as e:
                self.connected = False
                self._last_init_time = time.monotonic()
                log.error("camera", "camera_init_fail", f"Failed to initialize camera: {e}", data={"error": str(e)})

    def preview_generator(self):
        """Generator yielding MJPEG frames for the live view.
        
        Uses a generation counter so only the most recently created generator
        does actual work. Older generators exit immediately, closing their
        HTTP connections cleanly.
        """
        # Bump generation — this invalidates all older generators
        self._preview_generation += 1
        my_generation = self._preview_generation

        # Auto-init if not connected
        if not self.connected and my_generation == self._preview_generation:
            self.init()

        consecutive_errors = 0

        while my_generation == self._preview_generation:
            # Wait until preview is allowed (capture clears this event)
            allowed = self._preview_allowed.wait(timeout=0.5)
            if not allowed:
                continue

            # Check if we've been superseded by a newer generator
            if my_generation != self._preview_generation:
                return

            if not self.connected:
                # Never re-init while a capture is in progress
                if self._capture_in_progress:
                    time.sleep(0.5)
                    continue
                # Cooldown: only attempt re-init every 5 seconds
                elapsed = time.monotonic() - self._last_init_time
                if elapsed < 5.0:
                    time.sleep(1)
                    continue
                self.init()
                continue

            try:
                if self.lock.acquire(timeout=0.5):
                    try:
                        # Re-check conditions inside the lock
                        if not self._preview_allowed.is_set():
                            continue
                        if my_generation != self._preview_generation:
                            return
                            
                        camera_file = self.camera.capture_preview()
                        file_data = camera_file.get_data_and_size()
                        frame = bytes(memoryview(file_data))
                        consecutive_errors = 0
                    finally:
                        self.lock.release()
                        
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                    # Cap at ~15fps to prevent backpressure freezes
                    time.sleep(0.05)
                else:
                    time.sleep(0.1)
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors > 5 and not self._capture_in_progress:
                    log.error("camera", "camera_preview_fail", f"Preview stream failed: {e}")
                    self.connected = False
                time.sleep(0.5)
        
        # This generator has been superseded — exit cleanly

    def _do_capture(self):
        """Internal capture — viewfinder off, flush events, capture, download, save.
        Must be called with self.lock already held.
        """
        log.info("camera", "camera_capture_start", "Starting high-res capture")
        
        # 1. Disable viewfinder (Live View) so sensor is freed for still capture
        try:
            config = self.camera.get_config()
            ok, viewfinder = gp.gp_widget_get_child_by_name(config, 'viewfinder')
            if ok >= gp.GP_OK:
                viewfinder.set_value(0)
                self.camera.set_config(config)
        except Exception as e:
            log.warn("camera", "camera_viewfinder_warn", f"Could not disable viewfinder: {e}")

        # 2. Flush pending camera events
        try:
            while True:
                evt_type, evt_data = self.camera.wait_for_event(10)
                if evt_type == gp.GP_EVENT_TIMEOUT:
                    break
        except Exception:
            pass
        
        # 3. Wait briefly for camera to exit Live View mode
        time.sleep(0.3)
        
        # 4. Trigger capture
        file_path = self.camera.capture(gp.GP_CAPTURE_IMAGE)
        
        # 5. Do NOT re-enable viewfinder here.
        #    Let capture_preview() in the preview loop re-enable it naturally
        #    when it resumes. Re-enabling here causes a race condition.
        
        # 6. Download file
        log.info("camera", "camera_downloading", "Downloading image from camera")
        camera_file = self.camera.file_get(
            file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL
        )
        
        # 7. Save to disk
        filename = f"capture_{uuid.uuid4().hex[:8]}.jpg"
        save_path = os.path.join(PHOTOS_DIR, filename)
        camera_file.save(save_path)
        
        log.info("camera", "camera_capture_done", f"Image saved: {filename}")
        return filename

    def capture(self):
        """Captures a full-res image with retry logic.
        
        Flow:
        1. Pause preview loop via Event
        2. Acquire lock (wait up to 5s for preview loop to release)
        3. Attempt capture
        4. On failure: re-init camera and retry once
        5. Release lock and resume preview
        """
        if not self.connected:
            self.init()
            if not self.connected:
                raise Exception("Camera not connected")

        # Signal preview loop to pause
        self._capture_in_progress = True
        self._preview_allowed.clear()
        
        # Wait for the lock — preview loop should release within 0.5s
        acquired = self.lock.acquire(timeout=5)
        if not acquired:
            log.error("camera", "camera_lock_timeout", "Could not acquire camera lock for capture")
            self._capture_in_progress = False
            self._preview_allowed.set()
            raise Exception("Camera busy — could not acquire lock")

        try:
            try:
                return self._do_capture()
            except Exception as first_error:
                log.warn("camera", "camera_capture_retry", 
                         f"First capture attempt failed: {first_error}, re-initializing and retrying...")
                
                # Release lock so init() can acquire it
                self.lock.release()
                
                try:
                    self.init()
                except Exception as init_err:
                    log.error("camera", "camera_reinit_fail", f"Re-init failed: {init_err}")
                    self._capture_in_progress = False
                    self._preview_allowed.set()
                    raise Exception(f"Camera re-init failed: {init_err}")
                
                if not self.connected:
                    self._capture_in_progress = False
                    self._preview_allowed.set()
                    raise Exception("Camera not connected after re-init")
                
                # Re-acquire lock for retry
                acquired = self.lock.acquire(timeout=5)
                if not acquired:
                    self._capture_in_progress = False
                    self._preview_allowed.set()
                    raise Exception("Camera busy after re-init")
                
                try:
                    return self._do_capture()
                except Exception as retry_error:
                    log.error("camera", "camera_capture_error", f"Retry capture also failed: {retry_error}")
                    self.connected = False
                    raise retry_error
        finally:
            try:
                self.lock.release()
            except RuntimeError:
                pass  # Lock was already released during retry path
            
            self._capture_in_progress = False
            self._preview_allowed.set()

    def get_settings(self):
        if not self.connected:
            return {"status": "disconnected"}
            
        settings = {"status": "connected"}
        try:
            with self.lock:
                config = self.camera.get_config()
                
                for key in ['iso', 'aperture', 'shutterspeed', 'whitebalance']:
                    ok, widget = gp.gp_widget_get_child_by_name(config, key)
                    if ok >= gp.GP_OK:
                        val = widget.get_value()
                        choices = []
                        if widget.get_type() in (gp.GP_WIDGET_RADIO, gp.GP_WIDGET_MENU):
                            for i in range(widget.count_choices()):
                                choices.append(widget.get_choice(i))
                        settings[key] = {"value": val, "choices": choices}
        except Exception as e:
            log.warn("camera", "camera_settings_error", f"Failed to get settings: {e}")
            
        return settings

    def set_settings(self, new_settings):
        if not self.connected:
            raise Exception("Camera not connected")
            
        try:
            with self.lock:
                config = self.camera.get_config()
                changed = False
                
                for key, value in new_settings.items():
                    ok, widget = gp.gp_widget_get_child_by_name(config, key)
                    if ok >= gp.GP_OK:
                        widget.set_value(str(value))
                        changed = True
                        
                if changed:
                    self.camera.set_config(config)
                    log.info("camera", "camera_settings_updated", f"Camera settings updated: {new_settings}")
        except Exception as e:
            log.error("camera", "camera_settings_fail", f"Failed to set settings: {e}")
            raise e

    def get_status(self):
        return {
            "connected": self.connected,
            "is_capturing": self._capture_in_progress
        }

# Global singleton instance
camera_svc = CameraService()

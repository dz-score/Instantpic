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
        # Flag to track that a capture is in progress (prevents re-init from preview loop)
        self._capture_in_progress = False

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
                
                # Try to set capturetarget to memory card (1) or internal RAM (0)
                # For photo booth, RAM is usually preferred to save SD card wear,
                # but some cameras require it to be set explicitly.
                try:
                    config = self.camera.get_config()
                    ok, capture_target = gp.gp_widget_get_child_by_name(config, 'capturetarget')
                    if ok >= gp.GP_OK:
                        # Find the internal RAM choice to avoid type errors
                        choices = [capture_target.get_choice(i) for i in range(capture_target.count_choices())]
                        for choice in choices:
                            if 'RAM' in choice or 'Internal' in choice:
                                capture_target.set_value(choice)
                                break
                        self.camera.set_config(config)
                except Exception as e:
                    log.warn("camera", "camera_config_warn", f"Could not set capture target: {e}")
                    
                self.connected = True
                log.info("camera", "camera_ready", "Camera initialized successfully")
            except gp.GPhoto2Error as e:
                self.connected = False
                log.error("camera", "camera_init_fail", f"Failed to initialize camera: {e}", data={"error": str(e)})

    def preview_generator(self):
        """Generator yielding MJPEG frames for the live view."""
        # Auto-init if not connected
        if not self.connected:
            self.init()

        consecutive_errors = 0

        while True:
            # Wait until preview is allowed (capture clears this event)
            # Use a timeout so we can check periodically
            allowed = self._preview_allowed.wait(timeout=0.5)
            if not allowed:
                # Preview is paused (capture in progress), just loop
                continue

            if not self.connected:
                # Don't re-init if a capture is in progress — the capture
                # method will handle its own recovery
                if self._capture_in_progress:
                    time.sleep(0.5)
                    continue
                time.sleep(2)
                self.init()
                continue

            try:
                # Attempt to get the lock with a short timeout so we don't block forever
                if self.lock.acquire(timeout=0.5):
                    try:
                        # Re-check inside the lock — capture may have started
                        if not self._preview_allowed.is_set():
                            continue
                            
                        camera_file = self.camera.capture_preview()
                        file_data = camera_file.get_data_and_size()
                        frame = bytes(memoryview(file_data))
                        consecutive_errors = 0
                    finally:
                        self.lock.release()
                        
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                else:
                    time.sleep(0.1)
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors > 5 and not self._capture_in_progress:
                    log.error("camera", "camera_preview_fail", f"Preview stream failed, re-initializing: {e}")
                    self.connected = False
                time.sleep(0.5)

    def _do_capture(self):
        """Internal capture method — performs the actual gphoto2 capture sequence.
        Must be called with self.lock already held.
        Returns the filename on success, raises on failure.
        """
        log.info("camera", "camera_capture_start", "Starting high-res capture")
        
        # 1. Disable viewfinder (Live View) so sensor is freed
        try:
            config = self.camera.get_config()
            ok, viewfinder = gp.gp_widget_get_child_by_name(config, 'viewfinder')
            if ok >= gp.GP_OK:
                viewfinder.set_value(0)
                self.camera.set_config(config)
        except Exception as e:
            log.warn("camera", "camera_viewfinder_warn", f"Could not disable viewfinder: {e}")

        # 2. Flush pending camera events to clear the queue
        try:
            while True:
                evt_type, evt_data = self.camera.wait_for_event(10)
                if evt_type == gp.GP_EVENT_TIMEOUT:
                    break
        except Exception:
            pass
        
        # Some cameras need a tiny delay between stopping preview and capturing
        time.sleep(0.5)
        
        # 3. Trigger capture
        file_path = self.camera.capture(gp.GP_CAPTURE_IMAGE)
        
        # 4. Re-enable viewfinder
        try:
            config = self.camera.get_config()
            ok, viewfinder = gp.gp_widget_get_child_by_name(config, 'viewfinder')
            if ok >= gp.GP_OK:
                viewfinder.set_value(1)
                self.camera.set_config(config)
        except Exception as e:
            log.warn("camera", "camera_viewfinder_warn", f"Could not enable viewfinder: {e}")
        
        # Download file
        log.info("camera", "camera_downloading", "Downloading image from camera")
        camera_file = self.camera.file_get(
            file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL
        )
        
        # Save to disk
        filename = f"capture_{uuid.uuid4().hex[:8]}.jpg"
        save_path = os.path.join(PHOTOS_DIR, filename)
        camera_file.save(save_path)
        
        log.info("camera", "camera_capture_done", f"Image saved: {filename}")
        return filename

    def capture(self):
        """Captures a full-res image, saves it to disk, and returns the filename.
        Includes retry logic: if the first attempt fails, re-inits the camera and tries once more.
        """
        if not self.connected:
            self.init()
            if not self.connected:
                raise Exception("Camera not connected")

        # Signal preview loop to pause via Event
        self._capture_in_progress = True
        self._preview_allowed.clear()
        
        # Wait for preview loop to actually release the lock.
        # We try to acquire the lock with a generous timeout.
        # The preview loop checks _preview_allowed every 0.5s at most,
        # so 2 seconds should be more than enough.
        acquired = self.lock.acquire(timeout=3)
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
                
                # Release lock for re-init (init() acquires its own lock)
                self.lock.release()
                
                # Re-init camera
                try:
                    self.init()
                except Exception as init_err:
                    log.error("camera", "camera_reinit_fail", f"Re-init failed: {init_err}")
                    self._capture_in_progress = False
                    self._preview_allowed.set()
                    raise Exception(f"Camera re-init failed after capture error: {init_err}")
                
                if not self.connected:
                    self._capture_in_progress = False
                    self._preview_allowed.set()
                    raise Exception("Camera not connected after re-init")
                
                # Re-acquire lock for retry
                acquired = self.lock.acquire(timeout=3)
                if not acquired:
                    self._capture_in_progress = False
                    self._preview_allowed.set()
                    raise Exception("Camera busy after re-init — could not acquire lock")
                
                try:
                    return self._do_capture()
                except Exception as retry_error:
                    log.error("camera", "camera_capture_error", f"Retry capture also failed: {retry_error}")
                    self.connected = False
                    raise retry_error
        finally:
            # Always release the lock if we still hold it
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
                        # Get current value
                        val = widget.get_value()
                        # Get choices
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

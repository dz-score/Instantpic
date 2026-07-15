"""CameraDevice — the gphoto2 handle and every locked USB primitive.

This is the ONLY module in the package that imports gphoto2. Everything the
other modules do to the camera goes through a method here, under the device
lock where the operation needs it. Tests patch `backend.camera.device.gp`.
"""

import threading
import time
from typing import Optional

import gphoto2 as gp

from backend.logger import log


class CameraDevice:
    """Owns the connection: init/backoff, the camera lock, and raw USB ops.

    `notify_status` is the facade's status broadcaster (SSE camera_status) —
    injected so the device can announce connect/disconnect without knowing
    about SSE or about the gate's is_capturing flag.

    `warm_frame_sink` (set by the facade) receives the pre-warm preview frame
    produced during init, so the preview buffer has a frame before the worker
    produces its first one.
    """

    def __init__(self, notify_status):
        self.camera = None
        self.lock = threading.Lock()
        self.connected = False
        self.last_error: Optional[str] = None
        self.last_init_time = 0
        self._notify_status = notify_status
        self._warm_frame_sink = None
        self._init_backoff = 5
        self._init_fail_count = 0

        # Route libgphoto2 logs to Python's logging
        import logging
        gp_logger = logging.getLogger("gphoto2")
        gp_logger.setLevel(logging.DEBUG)

    def set_warm_frame_sink(self, sink):
        self._warm_frame_sink = sink

    @property
    def init_backoff(self) -> float:
        return self._init_backoff

    def init(self):
        with self.lock:
            # Prevent double-initialization race conditions
            if self.connected:
                return

            try:
                if self.camera:
                    try:
                        self.camera.exit()
                    except Exception:
                        pass

                if self._init_fail_count == 0:
                    log.info("camera", "camera_init", "Initializing gphoto2 camera...")
                else:
                    log.debug("camera", "camera_init",
                              f"Re-attempting camera init (attempt #{self._init_fail_count + 1}, backoff={self._init_backoff}s)")

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
                self.last_error = None
                self._init_backoff = 5
                self._init_fail_count = 0
                self.last_init_time = time.monotonic()
                self._notify_status()
                log.info("camera", "camera_ready", "Camera initialized successfully")

                # Configure basic settings for stability
                try:
                    config = self.camera.get_config()

                    # Prevent camera from hunting for focus during Live View
                    ok, af_widget = gp.gp_widget_get_child_by_name(config, 'autofocusdrive')
                    if ok >= gp.GP_OK:
                        af_widget.set_value(0)

                    self.camera.set_config(config)
                except Exception as cfg_err:
                    log.debug("camera", "camera_config_warn", f"Could not set initial config: {cfg_err}")

                # Log display/live-view related widgets once per init — no
                # runtime cost, and invaluable when diagnosing camera
                # behavior on-site (kept from the 2026-07 whine investigation).
                try:
                    config = self.camera.get_config()
                    # focusmode is here because it is the prime suspect for the
                    # residual ~7% capture_image [-1]: the body reports "One Shot"
                    # (AF-S), so every shutter release attempts an autofocus lock
                    # first, and a failed lock fails the release ~0.9s in. gphoto
                    # cannot change it (the widget offers a single choice) — MF is
                    # a camera-menu setting. Log it so a failing session says so.
                    for key in ['output', 'movierecordtarget', 'liveviewsize', 'eosmovieswitch',
                                'capturetarget', 'focusmode', 'autofocusdrive']:
                        ok, widget = gp.gp_widget_get_child_by_name(config, key)
                        if ok >= gp.GP_OK:
                            choices = []
                            try:
                                choices = [widget.get_choice(i) for i in range(widget.count_choices())]
                            except Exception:
                                pass
                            log.debug("camera", "camera_widget_info",
                                      f"Widget '{key}': value={widget.get_value()!r} choices={choices}")
                        else:
                            log.debug("camera", "camera_widget_info", f"Widget '{key}': not exposed")
                except Exception as e:
                    log.debug("camera", "camera_widget_info_fail", f"Could not enumerate widgets: {e}")

                # Pre-warm the viewfinder so the first preview frame is instant
                try:
                    camera_file = self.camera.capture_preview()
                    file_data = camera_file.get_data_and_size()
                    if self._warm_frame_sink:
                        self._warm_frame_sink(bytes(memoryview(file_data)))
                    log.debug("camera", "camera_warmup", "Viewfinder pre-warmed")
                except Exception as e:
                    # One-off: the worker's error handling covers it. But if this
                    # fires on EVERY launch, the venv is on the python-gphoto2
                    # wheel's broken libgphoto2 — see CONSTRAINTS.md.
                    log.warn("camera", "camera_warmup_fail",
                             f"Warmup preview failed ({e}) — first frame may be slow")

            except gp.GPhoto2Error as e:
                self.connected = False
                self.last_error = str(e)
                self._init_fail_count += 1
                self.last_init_time = time.monotonic()
                self._notify_status()
                if self._init_fail_count == 1:
                    log.error("camera", "camera_init_fail",
                              f"Failed to initialize camera: {e}", data={"error": str(e)})
                else:
                    log.debug("camera", "camera_init_fail",
                              f"Camera init retry #{self._init_fail_count} failed: {e}")
                self._init_backoff = min(self._init_backoff * 2, 30)

    def mark_disconnected(self):
        """Called by the preview worker when consecutive errors cross the
        disconnect threshold; init()'s backoff loop takes over from here."""
        self.connected = False
        self._notify_status()

    def read_preview_frame_locked(self) -> bytes:
        """One preview grab. The CALLER must hold self.lock (the worker
        acquires it with a timeout so a wedged grab can't hang the loop)."""
        camera_file = self.camera.capture_preview()
        file_data = camera_file.get_data_and_size()
        return bytes(memoryview(file_data))

    def flush_events(self, first_ms: int, subsequent_ms: int):
        """Drain pending camera events (notably GP_EVENT_FILE_ADDED, the one
        event that poisons the next preview session if left). Never raises."""
        try:
            evt_type, evt_data = self.camera.wait_for_event(first_ms)
            while evt_type != gp.GP_EVENT_TIMEOUT:
                evt_type, evt_data = self.camera.wait_for_event(subsequent_ms)
        except Exception:
            pass

    def trigger_capture(self):
        """Fire the physical shutter. Returns the on-camera file path.
        On failure: marks the device disconnected (a failed release usually
        means the session needs a re-init) and re-raises for the caller's
        retry policy."""
        try:
            with self.lock:
                return self.camera.capture(gp.GP_CAPTURE_IMAGE)
        except Exception as e:
            log.error("camera", "camera_capture_fail", f"Capture failed: {e}")
            self.connected = False
            raise

    def download(self, file_path):
        """Fetch the captured file off the camera. Raises on failure."""
        with self.lock:
            return self.camera.file_get(
                file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL
            )

    # ─── EXIF settings (admin panel) ──────────────────────────────

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

    def close(self):
        """Release the camera handle. Called by the facade's shutdown after
        the worker thread has been joined."""
        with self.lock:
            if self.camera:
                try:
                    self.camera.exit()
                    log.info("camera", "camera_exit", "Camera connection closed cleanly")
                except Exception as e:
                    log.warn("camera", "camera_exit_error", f"Error closing camera: {e}")
                finally:
                    self.camera = None
            self.connected = False
            self._notify_status()

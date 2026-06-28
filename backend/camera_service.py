import threading
import time
import os
import uuid
import gphoto2 as gp
from backend.logger import log
from backend.storage import PHOTOS_DIR
from backend.sse_service import sse_svc


class CameraService:
    """Camera service with decoupled capture architecture.

    A dedicated background worker thread continuously calls capture_preview()
    and stores the latest frame in a shared buffer. HTTP consumers read from
    the buffer via a Condition variable, so they always get the freshest frame.
    If an HTTP consumer is slow, old frames are silently overwritten — no
    backpressure can ever reach the camera.
    """

    def __init__(self):
        self.camera = None
        self.lock = threading.Lock()
        self.connected = False
        self._capture_in_progress = False
        self._preview_generation = 0
        self._last_init_time = 0
        self._last_error = None
        self._init_backoff = 5
        self._init_fail_count = 0
        self._shutdown_event = threading.Event()

        # --- Decoupled frame buffer ---
        # The worker thread writes the latest frame here.
        # HTTP consumers wait on _frame_condition for new frames.
        self._latest_frame = None
        self._frame_condition = threading.Condition()
        self._worker_thread = None
        self._worker_running = False
        # Event to pause/resume the worker (cleared during capture)
        self._preview_allowed = threading.Event()
        self._preview_allowed.set()

        # --- Diagnostics ---
        self._frames_produced = 0
        self._last_frame_time = time.perf_counter()
        self._last_cap_time = 0
        self._monitor_thread = None
        
        # Route libgphoto2 logs to Python's logging
        import logging
        gp_logger = logging.getLogger("gphoto2")
        gp_logger.setLevel(logging.DEBUG)
        
        # Start diagnostic monitor
        self._start_monitor()

    def _start_monitor(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_thread = threading.Thread(target=self._diagnostic_monitor, daemon=True, name="camera-monitor")
        self._monitor_thread.start()

    def _diagnostic_monitor(self):
        last_count = self._frames_produced
        while not self._shutdown_event.is_set():
            time.sleep(1.0)
            current_count = self._frames_produced
            fps = current_count - last_count
            last_count = current_count
            
            time_since_last = time.perf_counter() - self._last_frame_time
            
            # Broadcast metrics
            sse_svc.dispatch_event("camera_metrics", {
                "fps": fps,
                "latency_ms": round(self._last_cap_time * 1000, 1),
                "time_since_last_frame_ms": round(time_since_last * 1000, 1),
                "connected": self.connected,
                "is_capturing": self._capture_in_progress,
                "worker_running": self._worker_running,
                "allowed": self._preview_allowed.is_set()
            })

    def init(self):
        with self.lock:
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
                self._last_error = None
                self._init_backoff = 5
                self._init_fail_count = 0
                self._last_init_time = time.monotonic()
                sse_svc.dispatch_event("camera_status", self.get_status())
                log.info("camera", "camera_ready", "Camera initialized successfully")

                # Configure basic settings for stability
                try:
                    config = self.camera.get_config()
                    
                    # Prevent camera from hunting for focus during Live View
                    ok, af_widget = gp.gp_widget_get_child_by_name(config, 'autofocusdrive')
                    if ok >= gp.GP_OK:
                        af_widget.set_value(0)
                        
                    # Set capture target to SD card (or RAM) if needed, but for now just apply config
                    self.camera.set_config(config)
                except Exception as cfg_err:
                    log.debug("camera", "camera_config_warn", f"Could not set initial config: {cfg_err}")

                # Pre-warm the viewfinder so the first preview frame is instant
                try:
                    camera_file = self.camera.capture_preview()
                    file_data = camera_file.get_data_and_size()
                    with self._frame_condition:
                        self._latest_frame = bytes(memoryview(file_data))
                        self._frame_condition.notify_all()
                    log.debug("camera", "camera_warmup", "Viewfinder pre-warmed")
                except Exception:
                    pass

                # Start the background worker if not already running
                self._start_worker()

            except gp.GPhoto2Error as e:
                self.connected = False
                self._last_error = str(e)
                self._init_fail_count += 1
                self._last_init_time = time.monotonic()
                sse_svc.dispatch_event("camera_status", self.get_status())
                if self._init_fail_count == 1:
                    log.error("camera", "camera_init_fail",
                              f"Failed to initialize camera: {e}", data={"error": str(e)})
                else:
                    log.debug("camera", "camera_init_fail",
                              f"Camera init retry #{self._init_fail_count} failed: {e}")
                self._init_backoff = min(self._init_backoff * 2, 30)

    # ─── Background Worker ────────────────────────────────────────

    def _start_worker(self):
        """Start the background camera worker thread if not already running."""
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_running = True
        self._worker_thread = threading.Thread(target=self._camera_worker, daemon=True, name="camera-worker")
        self._worker_thread.start()
        log.info("camera", "worker_started", "Background camera worker thread started")

    def _camera_worker(self):
        """Dedicated thread: continuously captures preview frames into the shared buffer.

        This thread owns all camera USB I/O for preview. It runs independently
        of HTTP consumers. If no consumer is reading, frames are silently
        overwritten. The camera never stalls due to HTTP backpressure.
        """
        consecutive_errors = 0
        frame_count = 0

        while not self._shutdown_event.is_set():
            # Pause during capture — wait up to 0.5s, then re-check
            if not self._preview_allowed.wait(timeout=0.5):
                continue

            if self._shutdown_event.is_set():
                break

            if not self.connected:
                if self._capture_in_progress:
                    time.sleep(0.5)
                    continue
                elapsed = time.monotonic() - self._last_init_time
                if elapsed < self._init_backoff:
                    time.sleep(1)
                    continue
                self.init()
                continue

            try:
                loop_start = time.perf_counter()
                
                acq_start = time.perf_counter()
                acquired = self.lock.acquire(timeout=0.5)
                if not acquired:
                    log.debug("camera_timing", "worker_lock_timeout", "Worker failed to acquire lock within 0.5s")
                    continue
                acq_time = time.perf_counter() - acq_start

                try:
                    if not self._preview_allowed.is_set():
                        continue

                    cap_start = time.perf_counter()
                    camera_file = self.camera.capture_preview()
                    file_data = camera_file.get_data_and_size()
                    frame = bytes(memoryview(file_data))
                    cap_time = time.perf_counter() - cap_start
                    consecutive_errors = 0
                    flush_time = 0
                finally:
                    self.lock.release()

                # Publish to the shared buffer — overwrites any unread frame
                with self._frame_condition:
                    self._latest_frame = frame
                    self._frame_condition.notify_all()
                    
                self._frames_produced += 1
                self._last_frame_time = time.perf_counter()
                self._last_cap_time = cap_time

                loop_time = time.perf_counter() - loop_start
                log.debug("camera_timing", "worker_cycle", 
                          f"Worker cycle: cap={cap_time*1000:.1f}ms, flush={flush_time*1000:.1f}ms, lock={acq_time*1000:.1f}ms, total={loop_time*1000:.1f}ms")

                # Target ~15fps
                time.sleep(0.066)

            except Exception as e:
                consecutive_errors += 1
                log.debug("camera_timing", "worker_preview_err", f"Preview error #{consecutive_errors}: {e}")
                
                if consecutive_errors > 5 and not self._capture_in_progress:
                    log.error("camera", "camera_preview_fail", f"Preview worker failed: {e}")
                    self.connected = False
                    sse_svc.dispatch_event("camera_status", self.get_status())
                
                # Sleep briefly on error. 0.1s is enough to let USB settle 
                # without causing a massive freeze on the frontend.
                time.sleep(0.1)

        self._worker_running = False
        log.info("camera", "worker_stopped", "Background camera worker thread stopped")

    # ─── MJPEG Stream (HTTP consumer) ─────────────────────────────

    def preview_generator(self):
        """Generator yielding MJPEG frames for the live view.

        Reads from the shared frame buffer. The camera worker thread produces
        frames independently — this generator just waits for the latest one
        and yields it. If the HTTP connection is slow, frames are skipped,
        not queued. No backpressure reaches the camera.
        """
        self._preview_generation += 1
        my_generation = self._preview_generation

        # Auto-init if not connected (also starts the worker)
        if not self.connected:
            self.init()

        # If the worker isn't running, start it
        self._start_worker()

        while my_generation == self._preview_generation and not self._shutdown_event.is_set():
            wait_start = time.perf_counter()
            with self._frame_condition:
                # Wait up to 2s for a new frame from the worker
                got_frame = self._frame_condition.wait(timeout=2.0)
                wait_time = time.perf_counter() - wait_start
                
                if not got_frame:
                    log.debug("camera_timing", "http_generator_timeout", "HTTP generator timed out waiting for frame (2s)")
                    continue
                frame = self._latest_frame

            if frame is None:
                continue

            # Check if this generator has been superseded
            if my_generation != self._preview_generation:
                log.debug("camera_timing", "http_generator_superseded", "HTTP generator superseded, exiting")
                return

            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            
            # log.debug("camera_timing", "http_generator_yield", f"Yielded frame (wait={wait_time*1000:.1f}ms)")

    # ─── High-res Capture ─────────────────────────────────────────

    def _do_capture(self):
        """Internal capture — trigger, flush events, download, save.
        Must be called with self.lock already held.
        """
        log.info("camera", "camera_capture_start", "Starting high-res capture")
        cap_start = time.perf_counter()

        # 1. Flush pending camera events BEFORE capture.
        # This clears any leftover live-view events that might cause the camera
        # to throw `[-1] Unspecified error` when we transition to high-res capture.
        flush1_start = time.perf_counter()
        try:
            evt_type, evt_data = self.camera.wait_for_event(10)
            while evt_type != gp.GP_EVENT_TIMEOUT:
                evt_type, evt_data = self.camera.wait_for_event(5)
        except Exception:
            pass
        flush1_time = time.perf_counter() - flush1_start
        log.debug("camera_timing", "capture_flush1", f"Pre-capture flush in {flush1_time*1000:.1f}ms")

        # 2. Exit Live View and Movie Mode (drop the mirror)
        # We explicitly set BOTH to 0 to prevent the camera from locking up or expecting
        # a manual shutter button press, which eliminates the [-1] error delay.
        # We also sleep for 0.2s before doing this to allow any leftover USB I/O to finish,
        # otherwise the camera will reject the config change with `[-110] I/O in progress`.
        time.sleep(0.2)
        
        for attempt in range(2):
            try:
                config = self.camera.get_config()
                dirty = False
                for param in ['viewfinder', 'eosmoviemode']:
                    ok, widget = gp.gp_widget_get_child_by_name(config, param)
                    if ok >= gp.GP_OK:
                        widget.set_value(0)
                        dirty = True
                if dirty:
                    self.camera.set_config(config)
                break  # Success, exit the retry loop
            except Exception as e:
                log.debug("camera", "camera_config_warn", f"Could not disable Live View modes (attempt {attempt+1}): {e}")
                time.sleep(0.2)

        # 3. Trigger capture
        trig_start = time.perf_counter()
        file_path = self.camera.capture(gp.GP_CAPTURE_IMAGE)
        trig_time = time.perf_counter() - trig_start
        log.debug("camera_timing", "capture_trigger", f"Capture triggered in {trig_time*1000:.1f}ms")

        # 2. Flush pending camera events IMMEDIATELY after capture (before download).
        # This clears GP_EVENT_FILE_ADDED and other capture-related events
        # from the internal queue so they don't pile up.
        flush2_start = time.perf_counter()
        try:
            evt_type, evt_data = self.camera.wait_for_event(200)
            while evt_type != gp.GP_EVENT_TIMEOUT:
                evt_type, evt_data = self.camera.wait_for_event(10)
        except Exception:
            pass
        flush2_time = time.perf_counter() - flush2_start
        log.debug("camera_timing", "capture_flush2", f"Post-capture flush in {flush2_time*1000:.1f}ms")

        # 3. Download file
        dl_start = time.perf_counter()
        log.info("camera", "camera_downloading", "Downloading image from camera")
        camera_file = self.camera.file_get(
            file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL
        )
        dl_time = time.perf_counter() - dl_start
        log.debug("camera_timing", "capture_download", f"Image downloaded in {dl_time*1000:.1f}ms")

        # 4. Save to disk
        filename = f"capture_{uuid.uuid4().hex[:8]}.jpg"
        save_path = os.path.join(PHOTOS_DIR, filename)
        camera_file.save(save_path)

        total_time = time.perf_counter() - cap_start
        log.info("camera", "camera_capture_done", f"Image saved: {filename} (Total: {total_time:.2f}s)")
        return filename

    def capture(self):
        """Captures a full-res image with retry logic.

        Flow:
        1. Pause worker thread via Event
        2. Acquire lock (wait up to 5s for worker to release)
        3. Attempt capture
        4. On failure: re-init camera and retry once
        5. Release lock and resume worker
        """
        if not self.connected:
            self.init()
            if not self.connected:
                raise Exception("Camera not connected")

        # Signal worker thread to pause
        self._capture_in_progress = True
        self._preview_allowed.clear()
        sse_svc.dispatch_event("camera_status", self.get_status())

        # Wait for the lock — worker should release within 0.5s
        acquired = self.lock.acquire(timeout=5)
        if not acquired:
            log.error("camera", "camera_lock_timeout", "Could not acquire camera lock for capture")
            self._capture_in_progress = False
            self._preview_allowed.set()
            sse_svc.dispatch_event("camera_status", self.get_status())
            raise Exception("Camera busy — could not acquire lock")

        try:
            try:
                return self._do_capture()
            except Exception as first_error:
                log.warn("camera", "camera_capture_retry",
                         f"First capture attempt failed: {first_error}, waiting 0.6s and retrying...")

                # The camera's mirror might be dropping from live view.
                # Just wait a moment and try again without dropping the USB connection.
                time.sleep(0.6)
                
                # Flush pending events before retry to clear any transient errors
                try:
                    evt_type, _ = self.camera.wait_for_event(10)
                    while evt_type != gp.GP_EVENT_TIMEOUT:
                        evt_type, _ = self.camera.wait_for_event(10)
                except Exception:
                    pass

                try:
                    return self._do_capture()
                except Exception as retry_error:
                    log.error("camera", "camera_capture_error",
                              f"Retry capture also failed: {retry_error}")
                    self.connected = False
                    sse_svc.dispatch_event("camera_status", self.get_status())
                    raise retry_error
        finally:
            try:
                self.lock.release()
            except RuntimeError:
                pass  # Lock was already released during retry path

            self._capture_in_progress = False
            # self._preview_allowed.set() # Do NOT set here. Remain in standby.
            sse_svc.dispatch_event("camera_status", self.get_status())

    def standby(self):
        """Gently pauses the live view worker without acquiring locks or forcing errors.
        Used right before capture to let the camera's USB bus naturally drain."""
        if self._preview_allowed.is_set():
            log.info("camera", "camera_standby", "Entering standby mode (pausing live view)")
            self._preview_allowed.clear()
            sse_svc.dispatch_event("camera_status", self.get_status())

    def resume_preview(self):
        """Wakes up the background worker to resume the preview stream."""
        if not self._preview_allowed.is_set():
            log.info("camera", "camera_resume", "Resuming live view (waking worker)")
            self._preview_allowed.set()
            sse_svc.dispatch_event("camera_status", self.get_status())

    # ─── Settings & Status ────────────────────────────────────────

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
            "is_capturing": self._capture_in_progress,
            "error": self._last_error
        }

    def shutdown(self):
        log.info("camera", "camera_shutdown", "Shutting down camera service...")
        self._shutdown_event.set()
        # Wait for worker to finish
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3)
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
            sse_svc.dispatch_event("camera_status", self.get_status())


# Global singleton instance
camera_svc = CameraService()

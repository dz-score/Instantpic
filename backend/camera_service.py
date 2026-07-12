import asyncio
import threading
import time
import os
import uuid
import queue
import anyio.to_thread
import gphoto2 as gp
from pydantic import BaseModel
from typing import Optional
from backend.logger import log
from backend.storage import PHOTOS_DIR
from backend.sse_service import sse_svc

class CaptureJobState(BaseModel):
    job_id: str
    status: str  # 'pending', 'started', 'fired', 'downloading', 'completed', 'failed'
    filename: Optional[str] = None
    error: Optional[str] = None


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
        
        # Watchdog for auto-standby
        self._last_preview_request = time.monotonic()
        self._preview_idle_timeout = 10.0

        # Grace period right after init() during which preview errors are
        # expected (Canon live-view needs a moment to settle) and shouldn't
        # be treated as a real disconnect.
        self._preview_warmup_grace = 3.0

        # True while the current session's init-time warmup preview has
        # failed — the reliable signature of a wedged live-view session
        # (stale camera-side state left by a previous unclean process kill;
        # config reads work but every capture_preview stalls ~3s then
        # errors [-1]). While set, the worker fails fast into its
        # exit+re-init heal and the idle watchdog defers resting so the
        # heal isn't stranded until the next viewer arrives.
        self._warmup_failed = False

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

        # Command queue for the worker thread
        self._cmd_queue = queue.Queue()

        # Retry-once policy for a failed high-res capture (trigger or download),
        # mirroring PrintService.RETRY_DELAY_S — this is a workflow policy and
        # must live here, not in the frontend (Rule 14).
        self.CAPTURE_RETRY_DELAY_S = 1.5

        # Tiny idle gap between the last preview grab and the capture trigger, so
        # the camera's live view has actually released the USB/PTP path before we
        # fire (gphoto docs: "preview may not be fully stopped when capture is
        # triggered" — usually just milliseconds). This is NOT the old ~1s
        # stall-dodge settle; it only covers the preview-release race.
        self.PREVIEW_RELEASE_SETTLE_S = 0.015

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

                # Log display/live-view related widgets once per init — no
                # runtime cost, and invaluable when diagnosing camera
                # behavior on-site (kept from the 2026-07 whine investigation).
                try:
                    config = self.camera.get_config()
                    for key in ['output', 'movierecordtarget', 'liveviewsize', 'eosmovieswitch', 'capturetarget']:
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
                    with self._frame_condition:
                        self._latest_frame = bytes(memoryview(file_data))
                        self._frame_condition.notify_all()
                    self._warmup_failed = False
                    log.debug("camera", "camera_warmup", "Viewfinder pre-warmed")
                except Exception as e:
                    self._warmup_failed = True
                    log.warn("camera", "camera_warmup_fail",
                             f"Warmup preview failed ({e}) — wedged live-view session "
                             "(previous run took a photo; survives a clean exit, see "
                             "CAMERA_NOTES §2); worker will fail fast to re-init")

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
        # Instrumentation for the periodic Canon [-1] preview stall: track how
        # long the healthy run lasted (good frames + seconds) before each stall,
        # logged on the error below so live sessions are self-describing. See
        # backend/tools/preview_stall_probe.py for isolated, controlled runs.
        frames_since_stall = 0
        t_last_stall = time.monotonic()

        while not self._shutdown_event.is_set():
            # 1. Process command queue
            try:
                cmd = self._cmd_queue.get_nowait()
                if cmd.get("type") == "CAPTURE":
                    self._execute_capture_job(cmd["job_id"], cmd.get("callbacks"))
                    continue
            except queue.Empty:
                pass

            # 2. Pause during capture/standby — wait up to 0.1s to re-check queue
            if not self._preview_allowed.wait(timeout=0.1):
                continue

            if self._shutdown_event.is_set():
                break

            # A capture is queued or running — do not start a preview grab. The
            # CAPTURE job is serviced at the top of the loop; skipping the grab
            # keeps the worker from riding a preview into the M50 stall and
            # delaying the shutter (belt-and-suspenders with resume_preview()'s
            # guard, in case _preview_allowed was re-armed by a race).
            if self._capture_in_progress:
                continue

            # Watchdog check — runs every iteration regardless of whether the
            # camera is connected or the previous capture attempt errored, so
            # a camera that's erroring intermittently (never enough
            # consecutive failures to trip a full disconnect) still gets
            # forced to rest within _preview_idle_timeout of the last viewer.
            # Exception: while the session is wedged (_warmup_failed) the
            # worker is mid-heal — pausing now would strand the fail-fast →
            # re-init cascade until the next viewer arrives, so the rest is
            # deferred until the session produces a frame again.
            if self._preview_allowed.is_set() and not self._warmup_failed and \
                    time.monotonic() - self._last_preview_request > self._preview_idle_timeout:
                log.info("camera", "camera_watchdog", f"No preview requested for {self._preview_idle_timeout}s. Auto-pausing worker.")
                self.standby()
                continue

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

                    # Flush events to prevent camera buffer overflow during continuous preview
                    # Without this, the Canon M50 freezes for ~3 seconds and throws [-1] Unspecified error
                    flush_start = time.perf_counter()
                    try:
                        evt_type, evt_data = self.camera.wait_for_event(10)
                        while evt_type != gp.GP_EVENT_TIMEOUT:
                            evt_type, evt_data = self.camera.wait_for_event(5)
                    except Exception:
                        pass
                    flush_time = time.perf_counter() - flush_start

                    cap_start = time.perf_counter()
                    camera_file = self.camera.capture_preview()
                    file_data = camera_file.get_data_and_size()
                    frame = bytes(memoryview(file_data))
                    cap_time = time.perf_counter() - cap_start
                    consecutive_errors = 0
                    # A real frame proves the session healed.
                    self._warmup_failed = False
                finally:
                    self.lock.release()

                # Publish to the shared buffer — overwrites any unread frame
                with self._frame_condition:
                    self._latest_frame = frame
                    self._frame_condition.notify_all()
                    
                self._frames_produced += 1
                frames_since_stall += 1
                self._last_frame_time = time.perf_counter()
                self._last_cap_time = cap_time

                loop_time = time.perf_counter() - loop_start
                log.debug("camera_timing", "worker_cycle", 
                          f"Worker cycle: cap={cap_time*1000:.1f}ms, flush={flush_time*1000:.1f}ms, lock={acq_time*1000:.1f}ms, total={loop_time*1000:.1f}ms")

                # Target ~15fps
                time.sleep(0.066)

            except Exception as e:
                consecutive_errors += 1
                stall_ms = (time.perf_counter() - loop_start) * 1000
                now = time.monotonic()
                log.debug("camera_timing", "worker_preview_err",
                          f"Preview error #{consecutive_errors}: {e} "
                          f"(healthy run before stall: {frames_since_stall} frames / "
                          f"{now - t_last_stall:.1f}s; stalled cycle {stall_ms:.0f}ms)")
                frames_since_stall = 0
                t_last_stall = now

                warming_up = (time.monotonic() - self._last_init_time) < self._preview_warmup_grace
                # A wedged live-view session (warmup already failed) clears ONLY
                # after ~2 stalls of polling followed by an exit()+init() — polling
                # alone never heals it, and a re-init BEFORE ~2 stalls of priming
                # doesn't either (both proven on the M50: preview_stall_probe
                # --heal-probe, CAMERA_NOTES §2). The init-time warmup grab is
                # stall #1, so ONE worker stall (error_limit=1) supplies the 2
                # stalls the re-init needs — reaching the heal ~3s sooner than
                # waiting for 2 worker errors. Self-correcting: if the re-init's
                # own warmup still fails, _warmup_failed stays set and the worker
                # just runs another 2-stall cycle.
                error_limit = 1 if self._warmup_failed else 6
                if consecutive_errors >= error_limit and not self._capture_in_progress and not warming_up:
                    log.error("camera", "camera_preview_fail", f"Preview worker failed: {e}")
                    self.connected = False
                    # Start the next session's error count from zero —
                    # carrying it over gave the healed session a hair trigger
                    # where a single transient stall re-tripped a disconnect.
                    consecutive_errors = 0
                    sse_svc.dispatch_event("camera_status", self.get_status())
                
                # Sleep briefly on error. 0.1s is enough to let USB settle
                # without causing a massive freeze on the frontend.
                time.sleep(0.1)

        self._worker_running = False
        log.info("camera", "worker_stopped", "Background camera worker thread stopped")

    # ─── MJPEG Stream (HTTP consumer) ─────────────────────────────

    def _wait_for_frame(self, timeout):
        """Blocks the calling (worker) thread for up to `timeout`s for a new frame."""
        with self._frame_condition:
            return self._frame_condition.wait(timeout=timeout)

    async def preview_generator(self):
        """Async generator yielding MJPEG frames for the live view.

        Reads from the shared frame buffer. The camera worker thread produces
        frames independently — this generator just waits for the latest one
        and yields it. If the HTTP connection is slow, frames are skipped,
        not queued. No backpressure reaches the camera.

        This must be a native async generator, not a sync one run in a
        thread pool: when the client disconnects, Starlette cancels the
        task awaiting this generator, and cancellation only lands where
        we actually have an `await` point — a thread blocked inside a
        plain sync call can't be cancelled at all. Frame waits are done
        in short slices so a cancellation is never more than one slice
        away from taking effect, instead of leaving an orphaned thread
        looping forever.

        Relying on the client disconnect alone isn't enough in practice:
        some browsers don't promptly close the underlying connection for
        a multipart/x-mixed-replace stream when the <img> is unmounted,
        so Starlette never learns the client is gone. As a backstop, this
        generator closes itself after MAX_IDLE_S of receiving no frames
        (which, once the camera's own idle watchdog has paused the
        worker, means nobody has been actively watching for a while) so
        an abandoned connection can't outlive its viewer indefinitely.
        """
        self._preview_generation += 1
        my_generation = self._preview_generation

        # Auto-init if not connected (also starts the worker). Must run in a
        # thread: init() does USB I/O under the camera lock (~1.5s, plus a
        # ~3s warmup stall on a wedged session) and inline it would freeze
        # the whole event loop — SSE, state machine, every endpoint.
        if not self.connected:
            await anyio.to_thread.run_sync(self.init)

        # Update timestamp and wake up worker
        self._last_preview_request = time.monotonic()
        self.resume_preview()

        # If the worker isn't running, start it
        self._start_worker()

        POLL_SLICE = 0.5  # bounds how long a cancelled request can outlive its client
        MAX_IDLE_S = 30.0  # self-close backstop for connections the browser never actually closed
        idle_time = 0.0
        total_idle = 0.0

        while my_generation == self._preview_generation and not self._shutdown_event.is_set():
            # An attached viewer counts as a preview request even while no
            # frames arrive (camera erroring / mid-re-init) — otherwise the
            # idle watchdog pauses the worker 10s into an outage and the
            # stream stays black until the next screen entry.
            self._last_preview_request = time.monotonic()
            got_frame = await anyio.to_thread.run_sync(self._wait_for_frame, POLL_SLICE)

            if not got_frame:
                idle_time += POLL_SLICE
                total_idle += POLL_SLICE
                if idle_time >= 2.0:
                    log.debug("camera_timing", "http_generator_timeout", "HTTP generator timed out waiting for frame (2s)")
                    idle_time = 0.0
                if total_idle >= MAX_IDLE_S:
                    log.debug("camera_timing", "http_generator_idle_close",
                              f"No frames for {total_idle:.0f}s, closing stream (client likely gone)")
                    return
                continue
            idle_time = 0.0
            total_idle = 0.0

            with self._frame_condition:
                frame = self._latest_frame

            if frame is None:
                continue

            # Check if this generator has been superseded
            if my_generation != self._preview_generation:
                log.debug("camera_timing", "http_generator_superseded", "HTTP generator superseded, exiting")
                return

            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

    # ─── High-res Capture ─────────────────────────────────────────

    def enqueue_capture(self, on_complete=None, on_failure=None) -> str:
        """Enqueues a high-res capture job and returns its ID immediately.

        `on_complete(filename)` / `on_failure(error)` are optional coroutines
        invoked on the caller's event loop at the job's terminal state — the
        same submitter-owned-callback inversion as JobQueue, so the camera
        never knows who consumes the result (the FSM supplies bound methods).
        The camera_job SSE events still fire for every stage, but they are
        presentation-only (flash, sounds, progress): workflow completion
        travels through these callbacks, never through the browser.
        """
        callbacks = None
        if on_complete or on_failure:
            callbacks = {
                # Captured here because enqueue runs on the event loop; the
                # worker thread uses it to marshal the callback back over.
                "loop": asyncio.get_running_loop(),
                "on_complete": on_complete,
                "on_failure": on_failure,
            }
        job_id = uuid.uuid4().hex[:8]

        # Make the capture authoritative over live view BEFORE the job is even
        # queued: set the in-progress gate and standby first, so the worker can
        # neither start a new preview grab nor be re-armed by resume_preview()
        # (e.g. a preview-stream reconnect) in the window before it services the
        # capture. Without this the worker can ride a preview grab into the M50
        # ~3s stall and drag the shutter out with it.
        self._capture_in_progress = True
        self.standby()
        self._cmd_queue.put({"type": "CAPTURE", "job_id": job_id, "callbacks": callbacks})

        # Emit initial pending state
        self._emit_job_state(job_id, "pending")
        return job_id

    def _invoke_capture_callback(self, callbacks, key: str, arg):
        """Deliver a submitter callback coroutine onto its event loop. Runs on
        the worker thread; failures are logged, never raised — the SSE events
        already reported the outcome for presentation regardless."""
        if not callbacks or not callbacks.get(key):
            return
        try:
            asyncio.run_coroutine_threadsafe(callbacks[key](arg), callbacks["loop"])
        except Exception as e:
            log.error("camera", "capture_callback_fail",
                      f"Could not deliver capture {key} callback: {e}")

    def _emit_job_state(self, job_id: str, status: str, filename: str = None, error: str = None):
        state = CaptureJobState(job_id=job_id, status=status, filename=filename, error=error)
        sse_svc.dispatch_event("camera_job", state.model_dump())
        log.info("camera", f"capture_{status}", f"Capture job {job_id} is {status}", data=state.model_dump())

    def _execute_capture_job(self, job_id: str, callbacks=None):
        """Internal capture execution on the worker thread.
        Handles trigger, flush, download, save, and emits granular SSE events.

        Retries once on failure before giving up, mirroring PrintService's
        retry-once policy. This is a workflow decision and must live here,
        not in the frontend (Rule 14) — callers just see one 'failed' event
        if both attempts fail. The terminal outcome is also delivered to the
        submitter's on_complete/on_failure coroutines when provided.
        """
        self._capture_in_progress = True
        self._emit_job_state(job_id, "started")

        # Preview-release settle: a few ms of idle after live view stopped and
        # before we touch the capture path, so the camera has released the
        # live-view USB/PTP state (gphoto: "preview may not be fully stopped when
        # capture is triggered"). Distinct from the ~1s stall dodge.
        time.sleep(self.PREVIEW_RELEASE_SETTLE_S)

        if not self.connected:
            self.init()
            if not self.connected:
                self._emit_job_state(job_id, "failed", error="Camera not connected")
                self._capture_in_progress = False
                self._invoke_capture_callback(callbacks, "on_failure", "Camera not connected")
                return

        error = self._attempt_capture(job_id)

        if error:
            log.warn("camera", "camera_capture_retry",
                      f"Capture attempt failed: {error}, retrying in {self.CAPTURE_RETRY_DELAY_S}s...")
            time.sleep(self.CAPTURE_RETRY_DELAY_S)
            if not self.connected:
                self.init()
            error = self._attempt_capture(job_id) if self.connected else "Camera not connected"

        self._capture_in_progress = False
        if error:
            self._emit_job_state(job_id, "failed", error=error)
            self._invoke_capture_callback(callbacks, "on_failure", error)
        else:
            # Filename is deterministic from the job id (see _attempt_capture).
            self._invoke_capture_callback(callbacks, "on_complete", f"capture_{job_id}.jpg")

    def _attempt_capture(self, job_id: str) -> Optional[str]:
        """Runs a single trigger+download+save attempt. Emits the 'fired',
        'downloading', and (on success) 'completed' events itself. Returns
        None on success, or an error message on failure — the caller decides
        whether to retry or emit 'failed'.
        """
        cap_start = time.perf_counter()

        # Since we are on the worker thread, we don't need self.lock for USB operations
        # EXCEPT for get_config/set_config/capture which we still wrap just in case 
        # API endpoints call settings concurrently.


        # 1. Drain pending camera events BEFORE capture.
        flush1_start = time.perf_counter()

        try:
            evt_type, evt_data = self.camera.wait_for_event(10)
            while evt_type != gp.GP_EVENT_TIMEOUT:
                evt_type, evt_data = self.camera.wait_for_event(5)
        except Exception:
            pass
        flush1_time = time.perf_counter() - flush1_start
        log.debug("camera_timing", "capture_flush1", f"Pre-capture flush in {flush1_time*1000:.1f}ms")



        # 3. Trigger capture
        trig_start = time.perf_counter()

        # Fire the physical shutter - tell the UI immediately
        self._emit_job_state(job_id, "fired")
        
        try:
            with self.lock:
                file_path = self.camera.capture(gp.GP_CAPTURE_IMAGE)
        except Exception as e:
            # Capture failed
            log.error("camera", "camera_capture_fail", f"Capture failed: {e}")
            self.connected = False
            return str(e)
            
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
        self._emit_job_state(job_id, "downloading")
        dl_start = time.perf_counter()
        log.info("camera", "camera_downloading", "Downloading image from camera")
        
        try:
            with self.lock:
                camera_file = self.camera.file_get(
                    file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL
                )
        except Exception as e:
            log.error("camera", "camera_download_fail", f"Download failed: {e}")
            return f"Download failed: {str(e)}"
            
        dl_time = time.perf_counter() - dl_start
        log.debug("camera_timing", "capture_download", f"Image downloaded in {dl_time*1000:.1f}ms")

        # 4. Save to disk
        filename = f"capture_{job_id}.jpg"
        save_path = os.path.join(PHOTOS_DIR, filename)
        camera_file.save(save_path)

        total_time = time.perf_counter() - cap_start
        log.info("camera", "camera_capture_done", f"Image saved: {filename} (Total: {total_time:.2f}s)")
        
        self._emit_job_state(job_id, "completed", filename=filename)
        return None

    def standby(self):
        """Gently pauses the live view worker without acquiring locks or forcing errors.
        Used right before capture to let the camera's USB bus naturally drain."""
        if self._preview_allowed.is_set():
            log.info("camera", "camera_standby", "Entering standby mode (pausing live view)")
            self._preview_allowed.clear()
            sse_svc.dispatch_event("camera_status", self.get_status())

    def resume_preview(self):
        """Wakes up the background worker to resume the preview stream."""
        if self._capture_in_progress:
            # Never re-arm live view while a capture is pending/running — a
            # preview-stream reconnect (preview_generator calls this) must not
            # un-park the worker mid-capture and let it ride into the M50 stall.
            return
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

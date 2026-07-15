"""PreviewService — the live-view side: worker thread, frame buffer, MJPEG
generator, idle watchdog, and the once-per-second metrics monitor.

The worker thread owns all camera USB I/O. Captures execute on it too
(runner.run_pending() at the top of every iteration), so nothing else ever
touches the wire concurrently.
"""

import threading
import time

import anyio.to_thread

from backend.logger import log


class PreviewService:
    """A dedicated background worker continuously grabs preview frames into a
    shared buffer. HTTP consumers read from the buffer via a Condition
    variable, so they always get the freshest frame. If an HTTP consumer is
    slow, old frames are silently overwritten — no backpressure can ever
    reach the camera.

    `full_init` is the facade's init() — used where a cold path may need the
    whole service brought up (generator auto-init). The worker's own
    re-connect path calls device.init() directly: it IS the worker, so
    "start the worker" would be a no-op there.
    """

    def __init__(self, device, gate, sse, status_fn, run_pending_capture, full_init):
        self._device = device
        self._gate = gate
        self._sse = sse
        self._status = status_fn
        self._run_pending_capture = run_pending_capture
        self._full_init = full_init

        # --- Decoupled frame buffer ---
        self._latest_frame = None
        self._frame_condition = threading.Condition()
        self._worker_thread = None
        self._worker_running = False
        self._shutdown = threading.Event()
        self._preview_generation = 0

        # Watchdog for auto-standby
        self._last_preview_request = time.monotonic()
        self._preview_idle_timeout = 10.0

        # Grace period right after init() during which preview errors are
        # expected (Canon live-view needs a moment to settle) and shouldn't
        # be treated as a real disconnect.
        self._preview_warmup_grace = 3.0

        # --- Diagnostics ---
        self._frames_produced = 0
        self._last_frame_time = time.perf_counter()
        self._last_cap_time = 0
        self._monitor_thread = None

    # ─── Standby / resume (the gate does the guarding) ───────────

    def standby(self):
        """Gently pauses the live view worker without acquiring locks or
        forcing errors."""
        if self._gate.pause_preview():
            log.info("camera", "camera_standby", "Entering standby mode (pausing live view)")
            self._sse.dispatch_event("camera_status", self._status())

    def resume(self):
        """Wakes up the background worker to resume the preview stream.
        Refused by the gate while a capture is pending/running — a
        preview-stream reconnect must not put a grab in front of the
        shutter on the shared camera lock."""
        if self._gate.allow_preview():
            log.info("camera", "camera_resume", "Resuming live view (waking worker)")
            self._sse.dispatch_event("camera_status", self._status())

    # ─── Frame buffer ─────────────────────────────────────────────

    def publish_frame(self, frame: bytes):
        """Overwrites any unread frame and wakes waiting consumers. Also the
        sink for the device's init-time warmup frame."""
        with self._frame_condition:
            self._latest_frame = frame
            self._frame_condition.notify_all()

    def _wait_for_frame(self, timeout):
        """Blocks the calling thread for up to `timeout`s for a new frame."""
        with self._frame_condition:
            return self._frame_condition.wait(timeout=timeout)

    # ─── Worker thread ────────────────────────────────────────────

    def start_worker(self):
        """Start the background camera worker thread if not already running."""
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="camera-worker")
        self._worker_thread.start()
        log.info("camera", "worker_started", "Background camera worker thread started")

    def _worker_loop(self):
        """Dedicated thread: continuously captures preview frames into the
        shared buffer, and services queued capture jobs between grabs."""
        consecutive_errors = 0
        # Canary. Logged on each preview error below, so a live session says how
        # long it ran clean beforehand. A *recurring* ~3s-every-~6s pattern here
        # means the venv is back on the wheel's broken libgphoto2 (CONSTRAINTS.md).
        # Controlled runs: backend/tools/preview_stall_probe.py.
        frames_since_stall = 0
        t_last_stall = time.monotonic()

        while not self._shutdown.is_set():
            # 1. A queued capture job runs first, on this thread.
            if self._run_pending_capture():
                continue

            # 2. Pause during capture/standby — wait up to 0.1s to re-check queue
            if not self._gate.wait_preview_allowed(timeout=0.1):
                continue

            if self._shutdown.is_set():
                break

            # A capture is queued or running — do not start a preview grab.
            # The gate owns this rule (it also refuses resume() mid-capture).
            if not self._gate.preview_may_run():
                continue

            # Watchdog check — runs every iteration regardless of whether the
            # camera is connected or the previous capture attempt errored, so
            # a camera that's erroring intermittently (never enough
            # consecutive failures to trip a full disconnect) still gets
            # forced to rest within _preview_idle_timeout of the last viewer.
            if self._gate.preview_armed() and \
                    time.monotonic() - self._last_preview_request > self._preview_idle_timeout:
                log.info("camera", "camera_watchdog", f"No preview requested for {self._preview_idle_timeout}s. Auto-pausing worker.")
                self.standby()
                continue

            if not self._device.connected:
                elapsed = time.monotonic() - self._device.last_init_time
                if elapsed < self._device.init_backoff:
                    time.sleep(1)
                    continue
                self._device.init()
                continue

            try:
                loop_start = time.perf_counter()

                acq_start = time.perf_counter()
                acquired = self._device.lock.acquire(timeout=0.5)
                if not acquired:
                    log.debug("camera_timing", "worker_lock_timeout", "Worker failed to acquire lock within 0.5s")
                    continue
                acq_time = time.perf_counter() - acq_start

                try:
                    # Re-check under the lock: standby/capture may have landed
                    # while we waited for it.
                    if not self._gate.preview_may_run():
                        continue

                    # Do NOT add a per-frame wait_for_event flush here: it costs
                    # 12-30ms of every ~66ms frame and prevents nothing. Events are
                    # drained around the capture instead (CaptureRunner._attempt),
                    # which is what clears GP_EVENT_FILE_ADDED — the one event that
                    # poisons the next preview session if left.
                    cap_start = time.perf_counter()
                    frame = self._device.read_preview_frame_locked()
                    cap_time = time.perf_counter() - cap_start
                    consecutive_errors = 0
                finally:
                    self._device.lock.release()

                self.publish_frame(frame)

                self._frames_produced += 1
                frames_since_stall += 1
                self._last_frame_time = time.perf_counter()
                self._last_cap_time = cap_time

                loop_time = time.perf_counter() - loop_start
                log.debug("camera_timing", "worker_cycle",
                          f"Worker cycle: cap={cap_time*1000:.1f}ms, lock={acq_time*1000:.1f}ms, "
                          f"total={loop_time*1000:.1f}ms")

                # Target ~15fps — a CPU/bandwidth budget, not a camera limit (the
                # camera will sustain 60). Raise it if the preview should be smoother.
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

                warming_up = (time.monotonic() - self._device.last_init_time) < self._preview_warmup_grace
                # 6 consecutive failures = a real disconnect, not a blip. Keep the
                # threshold well above 1: a hair trigger here re-trips the disconnect
                # on any single transient error and thrashes the re-init.
                if consecutive_errors >= 6 and not self._gate.capture_in_progress and not warming_up:
                    log.error("camera", "camera_preview_fail", f"Preview worker failed: {e}")
                    # Reset, so the reconnected session starts from a clean count.
                    # Carrying it over leaves a hair trigger: one transient error
                    # after the re-init instantly re-trips the disconnect.
                    consecutive_errors = 0
                    self._device.mark_disconnected()

                # Sleep briefly on error. 0.1s is enough to let USB settle
                # without causing a massive freeze on the frontend.
                time.sleep(0.1)

        self._worker_running = False
        log.info("camera", "worker_stopped", "Background camera worker thread stopped")

    # ─── Metrics monitor ──────────────────────────────────────────

    def start_monitor(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="camera-monitor")
        self._monitor_thread.start()

    def _monitor_loop(self):
        last_count = self._frames_produced
        while not self._shutdown.is_set():
            time.sleep(1.0)
            current_count = self._frames_produced
            fps = current_count - last_count
            last_count = current_count

            time_since_last = time.perf_counter() - self._last_frame_time

            # Broadcast metrics
            self._sse.dispatch_event("camera_metrics", {
                "fps": fps,
                "latency_ms": round(self._last_cap_time * 1000, 1),
                "time_since_last_frame_ms": round(time_since_last * 1000, 1),
                "connected": self._device.connected,
                "is_capturing": self._gate.capture_in_progress,
                "worker_running": self._worker_running,
                "allowed": self._gate.preview_armed()
            })

    # ─── MJPEG Stream (HTTP consumer) ─────────────────────────────

    async def generator(self):
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

        # Auto-init if not connected (also starts the worker/monitor). Must run
        # in a thread: init() does USB I/O under the camera lock (~1.5s) and
        # inline it would freeze the whole event loop — SSE, state machine,
        # every endpoint.
        if not self._device.connected:
            await anyio.to_thread.run_sync(self._full_init)

        # Update timestamp and wake up worker
        self._last_preview_request = time.monotonic()
        self.resume()

        # If the worker isn't running, start it
        self.start_worker()

        POLL_SLICE = 0.5  # bounds how long a cancelled request can outlive its client
        MAX_IDLE_S = 30.0  # self-close backstop for connections the browser never actually closed
        idle_time = 0.0
        total_idle = 0.0

        while my_generation == self._preview_generation and not self._shutdown.is_set():
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

    # ─── Shutdown ─────────────────────────────────────────────────

    def stop(self):
        """Stop the worker (and monitor) threads. The generator loops also
        watch the shutdown event and close themselves."""
        self._shutdown.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3)

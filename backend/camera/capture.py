"""CaptureRunner — high-res capture jobs: queueing, retry policy, callbacks.

Jobs are enqueued from the event loop but EXECUTE on the preview worker's
thread (PreviewService calls run_pending() at the top of every loop
iteration): one thread owns all camera USB I/O, so a capture can never race
a preview grab on the wire.
"""

import asyncio
import os
import queue
import time
import uuid
from typing import Optional

from pydantic import BaseModel

from backend import storage
from backend.logger import log


class CaptureJobState(BaseModel):
    job_id: str
    status: str  # 'pending', 'started', 'fired', 'downloading', 'completed', 'failed'
    filename: Optional[str] = None
    error: Optional[str] = None


class CaptureRunner:
    # Retry-once policy for a failed high-res capture (trigger or download),
    # mirroring PrintService.RETRY_DELAY_S — this is a workflow policy and
    # must live here, not in the frontend (Rule 14).
    CAPTURE_RETRY_DELAY_S = 1.5

    # Tiny idle gap between the last preview grab and the capture trigger, so
    # the camera's live view has actually released the USB/PTP path before we
    # fire (gphoto docs: "preview may not be fully stopped when capture is
    # triggered" — usually just milliseconds).
    PREVIEW_RELEASE_SETTLE_S = 0.015

    def __init__(self, device, gate, sse, notify_status):
        self._device = device
        self._gate = gate
        self._sse = sse
        self._notify_status = notify_status
        self._queue = queue.Queue()

    def enqueue(self, on_complete=None, on_failure=None) -> str:
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
        # queued (gate.begin_capture pauses the preview AND blocks re-arming),
        # so a preview-stream reconnect in the window before the worker
        # services this job cannot put a grab in front of the shutter.
        if self._gate.begin_capture():
            log.info("camera", "camera_standby", "Entering standby mode (pausing live view)")
            self._notify_status()
        self._queue.put({"job_id": job_id, "callbacks": callbacks})

        # Emit initial pending state
        self._emit_job_state(job_id, "pending")
        return job_id

    def run_pending(self) -> bool:
        """Execute one queued capture job if there is one. Called by the
        preview worker at the top of its loop; returns True if a job ran."""
        try:
            cmd = self._queue.get_nowait()
        except queue.Empty:
            return False
        self.execute(cmd["job_id"], cmd.get("callbacks"))
        return True

    def _invoke_callback(self, callbacks, key: str, arg):
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
        self._sse.dispatch_event("camera_job", state.model_dump())
        log.info("camera", f"capture_{status}", f"Capture job {job_id} is {status}", data=state.model_dump())

    def execute(self, job_id: str, callbacks=None):
        """Capture execution on the worker thread.
        Handles trigger, flush, download, save, and emits granular SSE events.

        Retries once on failure before giving up, mirroring PrintService's
        retry-once policy. This is a workflow decision and must live here,
        not in the frontend (Rule 14) — callers just see one 'failed' event
        if both attempts fail. The terminal outcome is also delivered to the
        submitter's on_complete/on_failure coroutines when provided.
        """
        # Direct execute() calls (tests, a future manual trigger) may not have
        # gone through enqueue — make the capture authoritative here too.
        self._gate.begin_capture()
        self._emit_job_state(job_id, "started")

        # Preview-release settle: a few ms of idle after live view stopped and
        # before we touch the capture path, so the camera has released the
        # live-view USB/PTP state (gphoto: "preview may not be fully stopped
        # when capture is triggered").
        time.sleep(self.PREVIEW_RELEASE_SETTLE_S)

        if not self._device.connected:
            self._device.init()
            if not self._device.connected:
                self._emit_job_state(job_id, "failed", error="Camera not connected")
                self._gate.end_capture()
                self._invoke_callback(callbacks, "on_failure", "Camera not connected")
                return

        error = self._attempt(job_id)

        if error:
            log.warn("camera", "camera_capture_retry",
                     f"Capture attempt failed: {error}, retrying in {self.CAPTURE_RETRY_DELAY_S}s...")
            time.sleep(self.CAPTURE_RETRY_DELAY_S)
            if not self._device.connected:
                self._device.init()
            error = self._attempt(job_id) if self._device.connected else "Camera not connected"

        self._gate.end_capture()
        if error:
            self._emit_job_state(job_id, "failed", error=error)
            self._invoke_callback(callbacks, "on_failure", error)
        else:
            # Filename is deterministic from the job id (see _attempt).
            self._invoke_callback(callbacks, "on_complete", f"capture_{job_id}.jpg")

    def _attempt(self, job_id: str) -> Optional[str]:
        """Runs a single trigger+download+save attempt. Emits the 'fired',
        'downloading', and (on success) 'completed' events itself. Returns
        None on success, or an error message on failure — the caller decides
        whether to retry or emit 'failed'.
        """
        cap_start = time.perf_counter()

        # 1. Drain pending camera events BEFORE capture.
        flush1_start = time.perf_counter()
        self._device.flush_events(10, 5)
        flush1_time = time.perf_counter() - flush1_start
        log.debug("camera_timing", "capture_flush1", f"Pre-capture flush in {flush1_time*1000:.1f}ms")

        # 2. Trigger capture
        trig_start = time.perf_counter()

        # Fire the physical shutter - tell the UI immediately
        self._emit_job_state(job_id, "fired")

        try:
            file_path = self._device.trigger_capture()
        except Exception as e:
            # The device already logged and marked itself disconnected.
            return str(e)

        trig_time = time.perf_counter() - trig_start
        log.debug("camera_timing", "capture_trigger", f"Capture triggered in {trig_time*1000:.1f}ms")

        # 3. Flush pending camera events IMMEDIATELY after capture (before
        # download). This clears GP_EVENT_FILE_ADDED and other capture-related
        # events from the internal queue so they don't pile up.
        flush2_start = time.perf_counter()
        self._device.flush_events(200, 10)
        flush2_time = time.perf_counter() - flush2_start
        log.debug("camera_timing", "capture_flush2", f"Post-capture flush in {flush2_time*1000:.1f}ms")

        # 4. Download file
        self._emit_job_state(job_id, "downloading")
        dl_start = time.perf_counter()
        log.info("camera", "camera_downloading", "Downloading image from camera")

        try:
            camera_file = self._device.download(file_path)
        except Exception as e:
            log.error("camera", "camera_download_fail", f"Download failed: {e}")
            return f"Download failed: {str(e)}"

        dl_time = time.perf_counter() - dl_start
        log.debug("camera_timing", "capture_download", f"Image downloaded in {dl_time*1000:.1f}ms")

        # 5. Save to disk. storage.PHOTOS_DIR is read at call time so the test
        # fixture's redirection applies to camera saves too.
        filename = f"capture_{job_id}.jpg"
        save_path = os.path.join(storage.PHOTOS_DIR, filename)
        camera_file.save(save_path)

        total_time = time.perf_counter() - cap_start
        log.info("camera", "camera_capture_done", f"Image saved: {filename} (Total: {total_time:.2f}s)")

        self._emit_job_state(job_id, "completed", filename=filename)
        return None

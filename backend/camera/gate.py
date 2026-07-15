"""The single owner of the preview-vs-capture priority rule."""

import threading


class CaptureGate:
    """Live view never runs while a capture is pending or in flight.

    That is the whole rule, and this class is its only home. It used to be
    enforced twice — the worker loop checked `_capture_in_progress` before each
    grab, and resume_preview() separately refused to re-arm the preview flag
    mid-capture, each with a comment pointing at the other ("belt-and-
    suspenders"). Both call sites now ask the gate, so the rule cannot drift
    into two disagreeing copies.

    Why the rule exists: preview and capture share one camera lock, so a
    preview grab that sneaks in after a capture is enqueued makes the shutter
    queue behind it. The capture is made authoritative BEFORE its job is even
    queued (begin_capture at enqueue time), closing the window in which a
    preview-stream reconnect could re-arm live view ahead of the shutter.

    Thread notes: `_preview_allowed` is an Event (atomic); `_capture_in_progress`
    is a plain bool written by the event loop (enqueue) and the worker thread
    (job end) — same benign single-writer-at-a-time pattern as before the split.
    """

    def __init__(self):
        self._preview_allowed = threading.Event()
        self._preview_allowed.set()
        self._capture_in_progress = False

    @property
    def capture_in_progress(self) -> bool:
        return self._capture_in_progress

    def begin_capture(self) -> bool:
        """Make the capture authoritative over live view.

        Returns True if the preview was running and got paused, so the caller
        can announce the status change (log + SSE).
        """
        self._capture_in_progress = True
        return self.pause_preview()

    def end_capture(self):
        """Deliberately does NOT resume the preview: waking live view is the
        viewer's business (preview_generator calls resume on reconnect), and
        an idle booth should stay in standby after a capture."""
        self._capture_in_progress = False

    def pause_preview(self) -> bool:
        """Returns True if this call actually paused a running preview."""
        if self._preview_allowed.is_set():
            self._preview_allowed.clear()
            return True
        return False

    def allow_preview(self) -> bool:
        """Re-arm live view — refused while a capture is pending or running.

        Returns True if this call actually resumed a paused preview.
        """
        if self._capture_in_progress:
            return False
        if not self._preview_allowed.is_set():
            self._preview_allowed.set()
            return True
        return False

    def preview_may_run(self) -> bool:
        """May the worker start a preview grab right now?"""
        return self._preview_allowed.is_set() and not self._capture_in_progress

    def preview_armed(self) -> bool:
        """Is the preview flag set? (Ignores capture state — used by the idle
        watchdog and the metrics monitor, which report the flag itself.)"""
        return self._preview_allowed.is_set()

    def wait_preview_allowed(self, timeout: float) -> bool:
        return self._preview_allowed.wait(timeout)

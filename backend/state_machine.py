import asyncio
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from backend.logger import log
from backend.settings import AppSettings
from backend import jobs

class BoothState(BaseModel):
    screen: str = "ATTRACT"
    layoutMode: str = "single"
    totalShots: int = 1                # How many shots this layout requires (FSM-owned)
    capturedImages: List[str] = []
    # Screen-sized copies of capturedImages, produced by the processing job.
    # REVEAL and PICK_FAVORITE render these instead of the raws, which are 24MP
    # and take the Pi's browser 1-2s to decode. Empty until processing finishes
    # (and stays empty if it failed), so the UI falls back to capturedImages.
    previewImages: List[str] = []
    finalPhoto: Optional[str] = None
    retakeCount: int = 0
    allSessionPhotos: List[Dict[str, Any]] = []
    isProcessing: bool = False
    # Printing is backend-owned workflow, not a frontend guess. The FSM kicks
    # off the print on entering PRINTING and reports the real outcome here; the
    # UI only projects it. Values: "idle" | "printing" | "printed" | "failed"
    # | "skipped" (the event's print allowance is spent — no job was queued).
    printStatus: str = "idle"

# How many shots each layout requires. This is a workflow rule and must live
# in the backend FSM, never in the UI.
SHOTS_PER_LAYOUT = {
    "single": 1,
    "collage": 3,
}

class _NoLed:
    """Stand-in when no ring is injected, so call sites need no guard.

    Mirrors LedController's degraded behaviour exactly: everything succeeds
    silently, and capture() reports False — nothing was acknowledged because
    nothing was sent. The FSM fires the shutter anyway.
    """

    enabled = False

    async def idle(self): ...
    async def ready(self): ...
    async def phase(self, hue): ...
    async def countdown(self, duration_ms): ...
    async def release(self): ...
    async def printing(self): ...
    async def finished(self, duration_ms): ...
    async def error(self, code=1): ...

    async def capture(self) -> bool:
        return False


# Decorative hue per screen. The node is count- and screen-agnostic by design
# (Docs/LED_SPEC.md): it is handed a hue and spins, so the mapping from booth
# workflow to colour is the Pi's to own and lives here rather than in firmware.
SCREEN_HUE = {
    "CHOOSE_STYLE": 280,
    "REVEAL": 140,
    "PICK_FAVORITE": 200,
    "FRAME_PICKER": 320,
}


def countdown_ms(settings: AppSettings) -> int:
    """Effective countdown in ms, matching what the browser shows.

    Two clocks run this countdown, the browser's and the node's, and they always
    agreed on the duration — the browser derives the same number from the same
    two settings. What they disagreed on was the start: the FSM entered the
    countdown screen immediately, while the browser waited for the preview to
    paint its first frame, 1-3.5 s later. The node is now started by the browser
    reporting that its numbers began (COUNTDOWN_STARTED), so both run the same
    span from the same instant.
    """
    speed = settings.countdown_speed or 1.0
    return int(settings.countdown_duration / speed * 1000)


VALID_TRANSITIONS = {
    "ATTRACT": ["START_SESSION"],
    "CHOOSE_STYLE": ["SELECT_LAYOUT"],
    "COUNTDOWN": ["FIRE_SHOT"],
    "REVEAL": ["RETAKE", "PRINT_FROM_REVEAL"],
    "PICK_FAVORITE": ["FAVORITE_SELECT"],
    "FRAME_PICKER": ["FRAME_SELECT", "FRAME_SKIP"],
    "PRINTING": ["FINISH", "ANOTHER", "REPRINT"]
}

# Events that are valid from ANY state
GLOBAL_EVENTS = ["TIMEOUT", "FINISH"]

class StateMachine:
    """The booth's workflow. Its collaborators are handed in, never imported.

    `camera` is None when no backend is usable (gphoto2 absent); FIRE_SHOT then
    fails cleanly rather than crashing. The queue and camera report back through
    callbacks the FSM supplies, so neither imports the FSM — the dependency points
    one way only (Rule 18).
    """

    # How far past the configured session_timeout the backend floor waits
    # before it resets the booth. The browser's timer is the precise one (it
    # sees touches); this one deliberately loses that race — see _manage_watchdog.
    SESSION_WATCHDOG_GRACE_S = 60

    def __init__(self, sse, job_queue, camera=None, led=None, counters=None):
        self._state = BoothState()
        self._lock = None
        self._sse = sse
        self._job_queue = job_queue
        self._camera = camera
        # Inert when no ring is configured, so there is no null check below.
        self._led = led or _NoLed()
        # Absent means the allowance cannot be enforced, so it is not: a booth
        # that refuses to print because a tally is missing is worse than one
        # that overshoots a budget.
        self._counters = counters
        # True while a capture is between FIRE_SHOT and its terminal callback;
        # guards against double-firing the shutter.
        self._shot_in_flight = False
        # Floor for a session the browser can no longer end by itself — armed
        # for every screen except ATTRACT (see _manage_watchdog).
        self._watchdog: Optional[asyncio.Task] = None
        # Bumped on every transition. A watchdog captures this when it arms and
        # refuses to fire if it has moved, which is what makes a late-waking
        # task harmless without having to re-derive "did anything happen since".
        self._transition_seq = 0
        # The settings that armed the current session. Job callbacks re-arm the
        # watchdog but are not handed settings, so the session pins them here
        # at entry instead of re-fetching mid-flight (Rule 21 sanctions exactly
        # this; only accidental per-call re-fetching is the violation).
        self._watchdog_settings: Optional[AppSettings] = None

    def _get_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def get_state(self) -> BoothState:
        async with self._get_lock():
            return self._state.model_copy()

    async def broadcast_state(self, state_dict: dict):
        """Broadcast a state snapshot taken under the handler lock.

        The snapshot is passed in rather than read from self._state here:
        this runs outside the lock, and a job callback can mutate the state
        in the gap — reading live state would let clients observe broadcasts
        out of transition order.
        """
        self._sse.dispatch_event("state_update", state_dict)
        log.info("state_machine", "state_update", f"State transitioned to {state_dict['screen']}", data=state_dict)

    async def handle_event(self, event_type: str, payload: dict, settings: AppSettings):
        async with self._get_lock():
            if event_type == "COUNTDOWN_STARTED":
                # A cue, not a transition — deliberately handled before the
                # table, which stays a table of transitions. The browser owns
                # the instant its numerals start (it waits on the preview
                # painting, which the backend cannot observe), so this is the
                # only honest source for when the ring should start sweeping.
                # Same shape as FIRE_SHOT, which is the browser reporting that
                # the same countdown ended.
                if self._state.screen != "COUNTDOWN":
                    log.warn("state_machine", "countdown_cue_ignored",
                             f"COUNTDOWN_STARTED in {self._state.screen} — ignored")
                    return
                await self._led.countdown(countdown_ms(settings))
                return

            if event_type not in GLOBAL_EVENTS:
                valid_events = VALID_TRANSITIONS.get(self._state.screen, [])
                if event_type not in valid_events:
                    log.warn("state_machine", "invalid_transition", f"Event {event_type} is not allowed from state {self._state.screen}")
                    return

            log.info("state_machine", "event_received", f"Handling event: {event_type}", data=payload)
            
            if event_type == "START_SESSION":
                self._state = BoothState(screen="CHOOSE_STYLE")
            
            elif event_type == "SELECT_LAYOUT":
                mode = payload.get("mode", "single")
                self._state.layoutMode = mode
                self._state.totalShots = SHOTS_PER_LAYOUT.get(mode, 1)
                self._state.screen = "COUNTDOWN"
                self._state.capturedImages = []
                self._state.previewImages = []
                self._state.retakeCount = 0
                self._state.finalPhoto = None
                self._state.allSessionPhotos = []

            elif event_type == "FIRE_SHOT":
                # The UI's countdown finished — fire the shutter. The terminal
                # outcome returns via shot_completed/shot_failed on this loop
                # (backend-owned completion, same pattern as print/process);
                # the browser is never the courier. camera_job SSE events
                # remain purely presentational (flash, sounds, progress).
                if self._shot_in_flight:
                    log.warn("state_machine", "shot_in_flight",
                             "FIRE_SHOT ignored — a capture is already in flight")
                    return
                if not self._camera:
                    log.error("state_machine", "no_camera",
                              "FIRE_SHOT with no camera service injected")
                    return
                self._shot_in_flight = True

                # Light the ring BEFORE the shutter and wait for the node to
                # acknowledge. At Capture the ring is the key light, not
                # decoration — firing early photographs it mid-ramp.
                #
                # We fire regardless of the answer. False means unacknowledged,
                # not "definitely dark": a transport timeout and an ERR TIMEOUT
                # reply are both ambiguous (Docs/LED_PROTOCOL.md), and resolving
                # it costs another round trip on a link that just proved slow.
                # Refusing to shoot because a decorative-ish peripheral went
                # quiet is the worse failure — a dim photo beats no photo, and
                # the guest can retake.
                if not await self._led.capture():
                    log.warn("state_machine", "led_capture_unacked",
                             "Ring did not acknowledge CAPTURE — firing anyway; "
                             "this frame may be underexposed")

                self._camera.enqueue_capture(
                    on_complete=lambda filename: self.shot_completed(filename, settings),
                    on_failure=self.shot_failed,
                )
                # No state change yet — the shot lands via shot_completed.
                return

            elif event_type == "RETAKE":
                self._state.retakeCount += 1
                self._state.capturedImages = []
                self._state.previewImages = []
                self._state.finalPhoto = None
                self._state.screen = "COUNTDOWN"
                
            elif event_type == "PRINT_FROM_REVEAL":
                if len(self._state.allSessionPhotos) > 1:
                    self._state.screen = "PICK_FAVORITE"
                else:
                    await self._proceed_to_print_flow(settings)

            elif event_type == "FAVORITE_SELECT":
                filename = payload.get("filename")
                self._state.finalPhoto = filename
                for p in self._state.allSessionPhotos:
                    if p["filename"] == filename:
                        self._state.capturedImages = p.get("rawImages", [])
                        self._state.previewImages = p.get("previewImages", [])
                        break
                await self._proceed_to_print_flow(settings)

            elif event_type == "FRAME_SELECT":
                # overlay_id is genuine user input (which frame they tapped);
                # the banner text is a business rule the FSM assembles itself.
                overlay_id = payload.get("overlay_id", "none")
                self._state.isProcessing = True

                # Push job to queue
                if self._job_queue:
                    await self._job_queue.enqueue(jobs.process_frame_job(
                        self._state.capturedImages, self._state.layoutMode,
                        overlay_id, settings,
                        on_success=self.job_frame_processed,
                        on_failure=self.job_failed,
                    ))
                    
            elif event_type == "FRAME_SKIP":
                await self._enter_printing(settings)

            elif event_type == "REPRINT":
                # Failure only, and the guard doubles as the idempotency: the
                # first REPRINT moves printStatus off "failed", so a double tap
                # is refused here rather than queueing a duplicate. Why it is
                # restricted at all: Docs/API_PROTOCOL.md.
                if self._state.printStatus != "failed":
                    log.warn("state_machine", "reprint_rejected",
                             f"REPRINT ignored — printStatus is "
                             f"{self._state.printStatus!r}, not 'failed'")
                    return
                log.info("state_machine", "reprint",
                         f"Retrying the print for {self._state.finalPhoto}")
                await self._enter_printing(settings)

            elif event_type == "FINISH":
                self._state = BoothState(screen="ATTRACT")
                
            elif event_type == "ANOTHER":
                self._state = BoothState(screen="CHOOSE_STYLE")

            elif event_type == "TIMEOUT":
                self._state = BoothState(screen="ATTRACT")

            else:
                log.warn("state_machine", "unknown_event", f"Unknown event type: {event_type}")
                return

            self._manage_watchdog(settings)
            await self._sync_led(settings)
            state_dict = self._state.model_dump()

        # Broadcast outside the lock to avoid blocking
        await self.broadcast_state(state_dict)

    async def _sync_led(self, settings: Optional[AppSettings] = None):
        """Point the ring at whatever screen the transition landed on.

        Driven from the resulting screen rather than from each event branch, so
        a new transition cannot forget the ring and there is exactly one place
        the mapping lives. Capture and Release are not here: they bracket the
        shutter, not a screen.

        These are fire-and-forget — the controller serializes them on its own
        task, so this does not put a round trip inside the handler lock.

        `settings` is optional for the same reason _manage_watchdog's is: job
        callbacks land the guest on a new screen without being handed settings,
        so they fall back to the snapshot that armed the session. Only COUNTDOWN
        reads it at all.
        """
        settings = settings or self._watchdog_settings
        screen = self._state.screen
        if screen == "ATTRACT":
            await self._led.idle()
        elif screen == "COUNTDOWN":
            # Parked, not counting. The count starts on COUNTDOWN_STARTED,
            # which is the browser telling us its numerals began.
            await self._led.ready()
        elif screen == "PRINTING":
            await self._led.printing()
        elif screen in SCREEN_HUE:
            await self._led.phase(SCREEN_HUE[screen])

    def _manage_watchdog(self, settings: Optional[AppSettings] = None):
        """(Re)arm or cancel the backend session floor. Runs under the handler
        lock, after every transition that can change the screen.

        The browser owns the *precise* inactivity timer (App.jsx): it resets on
        every touch, which the backend cannot observe, so it stays where it is.
        This is the floor underneath it — for when that timer can never fire at
        all because the kiosk tab crashed, froze, or lost the network. Ending a
        session is workflow, and workflow is backend-owned (Rule 1).

        Two windows, because the two failures differ in kind:
          - COUNTDOWN keeps capture_stall_timeout: a tight, capture-specific
            window, since a shot either lands or it doesn't.
          - every other non-ATTRACT screen gets session_timeout plus
            SESSION_WATCHDOG_GRACE_S. It deliberately loses the race to the
            browser's own timer, because a guest reading the screen with a
            perfectly alive frontend must never be reset out from under it —
            only a frontend that has stopped reporting should trip this.
        """
        if settings is not None:
            self._watchdog_settings = settings
        settings = self._watchdog_settings

        self._transition_seq += 1
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None

        screen = self._state.screen
        # No settings yet means no session has started; ATTRACT is the resting
        # state and has nothing to time out of.
        if screen == "ATTRACT" or settings is None:
            return

        if screen == "COUNTDOWN":
            timeout = settings.capture_stall_timeout
            event = "capture_stalled"
            detail = (f"No shot progress after {timeout}s in COUNTDOWN "
                      f"({len(self._state.capturedImages)}/{self._state.totalShots} shots)")
        else:
            timeout = settings.session_timeout + self.SESSION_WATCHDOG_GRACE_S
            event = "session_abandoned"
            detail = (f"No activity for {timeout}s in {screen} — the browser's own "
                      f"inactivity timer never fired, so it is presumed gone")

        self._watchdog = asyncio.create_task(
            self._watchdog_expire(timeout, self._transition_seq, event, detail)
        )

    async def _watchdog_expire(self, timeout: float, armed_seq: int, event: str, detail: str):
        await asyncio.sleep(timeout)
        async with self._get_lock():
            # Anything at all transitioned while this task slept or waited for
            # the lock — a newer watchdog owns the session and this one is stale.
            if self._transition_seq != armed_seq:
                return
            log.error("state_machine", event, f"{detail} — resetting to ATTRACT")
            self._state = BoothState(screen="ATTRACT")
            self._watchdog = None
            # A capture whose callback never arrived would otherwise block
            # FIRE_SHOT forever; the reset is the floor for that too.
            self._shot_in_flight = False
            self._transition_seq += 1
            state_dict = self._state.model_dump()
        await self.broadcast_state(state_dict)

    async def _proceed_to_print_flow(self, settings: AppSettings):
        """Decide whether the guest should pick a frame or go straight to
        printing. The available overlays are passed in from config so the
        routing decision stays inside the FSM."""
        overlays = settings.overlays
        has_frame_options = any(o.id != "none" for o in overlays)
        if has_frame_options and len(overlays) > 1:
            self._state.screen = "FRAME_PICKER"
        else:
            await self._enter_printing(settings)

    async def _enter_printing(self, settings: Optional[AppSettings] = None):
        """Transition into PRINTING and kick off the actual print. The print is
        backend-owned workflow (Rule 1): the FSM enqueues the job and reports
        the real outcome via printStatus — the UI never guesses success from a
        timeout. Callers must set finalPhoto before invoking. Runs under the
        handler lock; enqueue is non-blocking.

        `settings` is optional for the same reason _sync_led's is: job callbacks
        land here without one.
        """
        settings = settings or self._watchdog_settings
        self._state.screen = "PRINTING"

        used = self._counters.get("prints_used") if self._counters else 0
        if settings and used >= settings.print_allowance:
            # Out of budget. The session is not cut short and the photo is not
            # lost — the guest still gets the QR — but no job is queued, and
            # printStatus says which of the two happened so the screen can too.
            self._state.printStatus = "skipped"
            log.info("state_machine", "print_allowance_spent",
                     f"Print skipped: {used} of "
                     f"{settings.print_allowance} prints used",
                     data={"prints_used": used,
                           "print_allowance": settings.print_allowance})
            return

        if not (self._job_queue and self._state.finalPhoto):
            # Nothing to print, or nowhere to send it. Say so instead of leaving
            # printStatus on "printing" with no job to ever move it off: that is
            # a guest watching the printing animation until the session watchdog
            # times them out, with nothing in the log to explain it.
            self._state.printStatus = "failed"
            log.error("state_machine", "print_not_enqueued",
                      "Entered PRINTING with nothing to print",
                      data={"finalPhoto": self._state.finalPhoto,
                            "has_queue": self._job_queue is not None})
            return
        self._state.printStatus = "printing"
        await self._job_queue.enqueue(jobs.print_photo_job(
            self._state.finalPhoto,
            on_success=self.job_print_done,
            on_failure=self.job_print_failed,
        ))

    # Capture callbacks — the camera worker delivers the terminal outcome
    # straight to the FSM (via enqueue_capture's marshalled coroutines). The
    # FSM appends the shot first-hand instead of trusting a browser-couriered
    # filename; the UI only presents.
    async def shot_completed(self, filename: str, settings: AppSettings):
        async with self._get_lock():
            self._shot_in_flight = False
            if self._state.screen != "COUNTDOWN":
                # Session ended (TIMEOUT / FINISH / stall watchdog) while the
                # shutter was busy — the photo stays on disk but the workflow
                # has moved on.
                log.warn("state_machine", "shot_after_exit",
                         f"Capture {filename} completed after leaving COUNTDOWN — ignored")
                return
            self._state.capturedImages.append(filename)
            log.info("state_machine", "shot_captured",
                     f"Shot {len(self._state.capturedImages)}/{self._state.totalShots} captured")

            # Sequence complete? -> move to REVEAL and kick off processing.
            if len(self._state.capturedImages) >= self._state.totalShots:
                self._state.screen = "REVEAL"
                self._state.isProcessing = True
                self._state.finalPhoto = None

                if self._job_queue:
                    images = list(self._state.capturedImages)
                    await self._job_queue.enqueue(jobs.process_photo_job(
                        images, self._state.layoutMode, settings,
                        on_success=lambda filename, previews: self.job_photo_processed(filename, images, previews),
                        on_failure=self.job_failed,
                    ))
            # Otherwise stay in COUNTDOWN; broadcasting the new state lets the
            # UI advance its shot-progress presentation.
            self._manage_watchdog(settings)
            # Takes the ring out of Capture. No explicit RELEASE is needed on
            # this path: whichever mode follows (Reveal's hue, or the parked
            # Ready of the next shot) leaves full white on its own.
            #
            # On the multi-shot path this parks the ring while the browser sits
            # out shot_interval_ms, and the next sweep starts with the next
            # shot's numerals rather than ahead of them.
            await self._sync_led(settings)
            state_dict = self._state.model_dump()
        await self.broadcast_state(state_dict)

    async def shot_failed(self, error: str):
        async with self._get_lock():
            self._shot_in_flight = False
            log.error("state_machine", "shot_failed",
                      f"Capture failed permanently: {error}")
            # The one path that needs an explicit RELEASE. State is unchanged,
            # so no transition follows to take the ring out of Capture, and the
            # node would otherwise sit at full white until its own 30 s timeout
            # — the highest-current, highest-heat state in the system.
            await self._led.release()
        # State unchanged: the UI shows its retry overlay from the camera_job
        # 'failed' SSE event (a retry re-fires FIRE_SHOT), and the stall
        # watchdog remains the floor if the guest walks away.

    # Job Completion Callbacks
    async def job_photo_processed(self, filename: str, images: list, previews: list = None):
        previews = previews or []
        async with self._get_lock():
            self._state.isProcessing = False
            self._state.finalPhoto = filename
            self._state.previewImages = previews
            self._state.allSessionPhotos.append({
                "filename": filename,
                "rawImages": images,
                "previewImages": previews
            })
            state_dict = self._state.model_dump()
        await self.broadcast_state(state_dict)

    # `previews` is accepted and ignored: the queue reports every processing job
    # the same way, and this one feeds PRINTING, which shows no individual shots.
    async def job_frame_processed(self, filename: str, previews: list = None):
        async with self._get_lock():
            self._state.isProcessing = False
            self._state.finalPhoto = filename
            await self._enter_printing()
            # Screen changed outside handle_event, so re-arm here too — this is
            # the one callback path that lands the guest on a new screen. The
            # ring needs the same treatment for the same reason: without this it
            # would sit on the frame-picker hue for the whole print.
            self._manage_watchdog()
            await self._sync_led()
            state_dict = self._state.model_dump()
        await self.broadcast_state(state_dict)

    async def job_failed(self, error: str):
        async with self._get_lock():
            self._state.isProcessing = False
            log.error("state_machine", "job_failed", f"Background job failed: {error}")
            state_dict = self._state.model_dump()
        await self.broadcast_state(state_dict)

    # Print job callbacks — the FSM records the real printer outcome so the UI
    # can project it instead of racing a timeout.
    async def job_print_done(self, filename: str):
        async with self._get_lock():
            self._state.printStatus = "printed"
            log.info("state_machine", "print_done", f"Print completed: {filename}")
            state_dict = self._state.model_dump()
        await self.broadcast_state(state_dict)

    async def job_print_failed(self, error: str):
        async with self._get_lock():
            self._state.printStatus = "failed"
            log.error("state_machine", "print_failed", f"Print failed: {error}")
            state_dict = self._state.model_dump()
        await self.broadcast_state(state_dict)

# No module-level singleton: the FSM needs the SSE service, the job queue and the
# camera, and the composition root (main.py's lifespan) is the one place that has
# all three. Rule 19.

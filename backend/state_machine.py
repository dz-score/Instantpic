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
    finalPhoto: Optional[str] = None
    retakeCount: int = 0
    allSessionPhotos: List[Dict[str, Any]] = []
    isProcessing: bool = False
    # Printing is backend-owned workflow, not a frontend guess. The FSM kicks
    # off the print on entering PRINTING and reports the real outcome here; the
    # UI only projects it. Values: "idle" | "printing" | "printed" | "failed".
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

    Two clocks run this countdown — the browser's and the node's — and they are
    started by the same transition but never resynchronised. The firmware clamps
    elapsed to duration (anim_countdown.c), so drift degrades to a head frozen
    at the top of the ring rather than a glitch or a wrapped animation.
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
    "PRINTING": ["FINISH", "ANOTHER"]
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

    def __init__(self, sse, job_queue, camera=None, led=None):
        self._state = BoothState()
        self._lock = None
        self._sse = sse
        self._job_queue = job_queue
        self._camera = camera
        # Inert when no ring is configured, so there is no null check below.
        self._led = led or _NoLed()
        # True while a capture is between FIRE_SHOT and its terminal callback;
        # guards against double-firing the shutter.
        self._shot_in_flight = False
        # Floor for a session stranded in COUNTDOWN (browser or camera died
        # mid-shot); armed while the FSM sits there (see _manage_stall_watchdog).
        self._stall_watchdog: Optional[asyncio.Task] = None

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
                await self._enter_printing()
                
            elif event_type == "FINISH":
                self._state = BoothState(screen="ATTRACT")
                
            elif event_type == "ANOTHER":
                self._state = BoothState(screen="CHOOSE_STYLE")

            elif event_type == "TIMEOUT":
                self._state = BoothState(screen="ATTRACT")

            else:
                log.warn("state_machine", "unknown_event", f"Unknown event type: {event_type}")
                return

            self._manage_stall_watchdog(settings)
            await self._sync_led(settings)
            state_dict = self._state.model_dump()

        # Broadcast outside the lock to avoid blocking
        await self.broadcast_state(state_dict)

    async def _sync_led(self, settings: AppSettings):
        """Point the ring at whatever screen the transition landed on.

        Driven from the resulting screen rather than from each event branch, so
        a new transition cannot forget the ring and there is exactly one place
        the mapping lives. Capture and Release are not here: they bracket the
        shutter, not a screen.

        These are fire-and-forget — the controller serializes them on its own
        task, so this does not put a round trip inside the handler lock.
        """
        screen = self._state.screen
        if screen == "ATTRACT":
            await self._led.idle()
        elif screen == "COUNTDOWN":
            await self._led.countdown(countdown_ms(settings))
        elif screen == "PRINTING":
            await self._led.printing()
        elif screen in SCREEN_HUE:
            await self._led.phase(SCREEN_HUE[screen])

    def _manage_stall_watchdog(self, settings: AppSettings):
        """(Re)arm or cancel the COUNTDOWN stall floor. Runs under the
        handler lock, after every state transition.

        Ordinary capture completion is backend-owned (camera -> FSM
        callbacks), so this watchdog is a true last resort: it only fires if
        the browser died mid-session (no FIRE_SHOT ever arrives) or a capture
        callback never lands. Either way the session is unrecoverable and the
        booth resets to ATTRACT for the next guest — the same backend-owned
        recovery idiom as TIMEOUT and printStatus (Rule 14).

        Re-armed on every event that lands in COUNTDOWN, so each shot of a
        multi-shot layout gets a fresh window; cancelled on any transition
        elsewhere.
        """
        if self._stall_watchdog:
            self._stall_watchdog.cancel()
            self._stall_watchdog = None
        if self._state.screen == "COUNTDOWN":
            self._stall_watchdog = asyncio.create_task(
                self._stall_watchdog_expire(
                    settings.capture_stall_timeout,
                    len(self._state.capturedImages),
                )
            )

    async def _stall_watchdog_expire(self, timeout: float, armed_shots: int):
        await asyncio.sleep(timeout)
        async with self._get_lock():
            # A shot may have landed while this task waited for the lock —
            # only a session still in COUNTDOWN with no shot progress since
            # arming is genuinely stalled.
            if self._state.screen != "COUNTDOWN" or len(self._state.capturedImages) != armed_shots:
                return
            log.error("state_machine", "capture_stalled",
                      f"No shot progress after {timeout}s in COUNTDOWN "
                      f"({armed_shots}/{self._state.totalShots} shots) — resetting to ATTRACT")
            self._state = BoothState(screen="ATTRACT")
            self._stall_watchdog = None
            # A capture whose callback never arrived would otherwise block
            # FIRE_SHOT forever; the stall reset is the floor for that too.
            self._shot_in_flight = False
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
            await self._enter_printing()

    async def _enter_printing(self):
        """Transition into PRINTING and kick off the actual print. The print is
        backend-owned workflow (Rule 1): the FSM enqueues the job and reports
        the real outcome via printStatus — the UI never guesses success from a
        timeout. Callers must set finalPhoto before invoking. Runs under the
        handler lock; enqueue is non-blocking."""
        self._state.screen = "PRINTING"
        self._state.printStatus = "printing"
        if self._job_queue and self._state.finalPhoto:
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
                        on_success=lambda filename: self.job_photo_processed(filename, images),
                        on_failure=self.job_failed,
                    ))
            # Otherwise stay in COUNTDOWN; broadcasting the new state lets the
            # UI advance its shot-progress presentation.
            self._manage_stall_watchdog(settings)
            # Takes the ring out of Capture. No explicit RELEASE is needed on
            # this path: whichever mode follows (Reveal's hue, or the next
            # shot's countdown) leaves full white on its own.
            #
            # Known imprecision on the multi-shot path: the node starts its next
            # countdown here, while the browser waits shot_interval_ms first. The
            # ring therefore finishes its sweep slightly early and holds, because
            # anim_countdown clamps elapsed to duration. Decorative, and it fails
            # in the safe direction.
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
    async def job_photo_processed(self, filename: str, images: list):
        async with self._get_lock():
            self._state.isProcessing = False
            self._state.finalPhoto = filename
            self._state.allSessionPhotos.append({
                "filename": filename,
                "rawImages": images
            })
            state_dict = self._state.model_dump()
        await self.broadcast_state(state_dict)

    async def job_frame_processed(self, filename: str):
        async with self._get_lock():
            self._state.isProcessing = False
            self._state.finalPhoto = filename
            await self._enter_printing()
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

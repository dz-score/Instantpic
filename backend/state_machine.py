import asyncio
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from backend.logger import log
from backend.sse_service import sse_svc
from backend.config import AppSettings
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

VALID_TRANSITIONS = {
    "ATTRACT": ["START_SESSION"],
    "CHOOSE_STYLE": ["SELECT_LAYOUT"],
    "COUNTDOWN": ["SHOT_CAPTURED"],
    "REVEAL": ["RETAKE", "PRINT_FROM_REVEAL"],
    "PICK_FAVORITE": ["FAVORITE_SELECT"],
    "FRAME_PICKER": ["FRAME_SELECT", "FRAME_SKIP"],
    "PRINTING": ["FINISH", "ANOTHER"]
}

# Events that are valid from ANY state
GLOBAL_EVENTS = ["TIMEOUT", "FINISH"]

class StateMachine:
    def __init__(self):
        self._state = BoothState()
        self._lock = None
        self._job_queue = None  # Injected later to avoid circular import
        # Backstop for the frontend-couriered SHOT_CAPTURED hop; armed while
        # the FSM sits in COUNTDOWN (see _manage_stall_watchdog).
        self._stall_watchdog: Optional[asyncio.Task] = None

    def _get_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def set_job_queue(self, queue):
        self._job_queue = queue

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
        sse_svc.dispatch_event("state_update", state_dict)
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

            elif event_type == "SHOT_CAPTURED":
                # The UI reports a single completed capture. The FSM owns shot
                # accumulation and decides when the capture sequence is finished.
                filename = payload.get("filename")
                if not filename:
                    log.error("state_machine", "capture_error", "No filename provided in SHOT_CAPTURED")
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
            state_dict = self._state.model_dump()

        # Broadcast outside the lock to avoid blocking
        await self.broadcast_state(state_dict)

    def _manage_stall_watchdog(self, settings: AppSettings):
        """(Re)arm or cancel the COUNTDOWN stall backstop. Runs under the
        handler lock, after every state transition.

        SHOT_CAPTURED reaches the FSM via the frontend (camera_job SSE event
        -> POST /api/events). If that hop is lost, the photo sits on disk but
        the session strands in COUNTDOWN with nothing backend-side to recover
        it — the print flow has printStatus, sessions have TIMEOUT; this is
        the same idiom for the capture sequence (Rule 14: recovery is
        workflow and belongs here, not in the UI or camera_service).

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

# Global Singleton
state_machine = StateMachine()

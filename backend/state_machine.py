import asyncio
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from backend.logger import log
from backend.sse_service import sse_svc
from backend.config import AppSettings

class BoothState(BaseModel):
    screen: str = "ATTRACT"
    layoutMode: str = "single"
    totalShots: int = 1                # How many shots this layout requires (FSM-owned)
    capturedImages: List[str] = []
    finalPhoto: Optional[str] = None
    retakeCount: int = 0
    allSessionPhotos: List[Dict[str, Any]] = []
    isProcessing: bool = False

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

    def _get_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def set_job_queue(self, queue):
        self._job_queue = queue

    async def get_state(self) -> BoothState:
        async with self._get_lock():
            return self._state.model_copy()

    async def broadcast_state(self):
        state_dict = self._state.model_dump()
        sse_svc.dispatch_event("state_update", state_dict)
        log.info("state_machine", "state_update", f"State transitioned to {self._state.screen}", data=state_dict)

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
                        await self._job_queue.enqueue({
                            "type": "PROCESS_PHOTO",
                            "images": images,
                            "layout": self._state.layoutMode,
                            "text": self._compose_banner_text(settings),
                            "overlay_id": settings.selected_overlay or "none",
                            "on_success": lambda filename: self.job_photo_processed(filename, images),
                            "on_failure": self.job_failed,
                        })
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
                    self._proceed_to_print_flow(settings)

            elif event_type == "FAVORITE_SELECT":
                filename = payload.get("filename")
                self._state.finalPhoto = filename
                for p in self._state.allSessionPhotos:
                    if p["filename"] == filename:
                        self._state.capturedImages = p.get("rawImages", [])
                        break
                self._proceed_to_print_flow(settings)

            elif event_type == "FRAME_SELECT":
                # overlay_id is genuine user input (which frame they tapped);
                # the banner text is a business rule the FSM assembles itself.
                overlay_id = payload.get("overlay_id", "none")
                self._state.isProcessing = True

                # Push job to queue
                if self._job_queue:
                    await self._job_queue.enqueue({
                        "type": "PROCESS_FRAME",
                        "images": self._state.capturedImages,
                        "layout": self._state.layoutMode,
                        "text": self._compose_banner_text(settings),
                        "overlay_id": overlay_id,
                        "on_success": self.job_frame_processed,
                        "on_failure": self.job_failed,
                    })
                    
            elif event_type == "FRAME_SKIP":
                self._state.screen = "PRINTING"
                
            elif event_type == "FINISH":
                self._state = BoothState(screen="ATTRACT")
                
            elif event_type == "ANOTHER":
                self._state = BoothState(screen="CHOOSE_STYLE")

            elif event_type == "TIMEOUT":
                self._state = BoothState(screen="ATTRACT")

            else:
                log.warn("state_machine", "unknown_event", f"Unknown event type: {event_type}")
                return

        # Broadcast outside the lock to avoid blocking
        await self.broadcast_state()

    def _compose_banner_text(self, settings) -> str:
        """Assemble the branding text printed on the photo. This is a business
        rule and must be owned by the backend, not derived in the UI."""
        if not settings.show_names_on_photo:
            return ""
        parts = [p for p in (settings.couple_names, settings.event_date) if p]
        return " · ".join(parts) if parts else (settings.default_text or "")

    def _proceed_to_print_flow(self, settings: AppSettings):
        """Decide whether the guest should pick a frame or go straight to
        printing. The available overlays are passed in from config so the
        routing decision stays inside the FSM."""
        overlays = settings.overlays
        has_frame_options = any(o.id != "none" for o in overlays)
        if has_frame_options and len(overlays) > 1:
            self._state.screen = "FRAME_PICKER"
        else:
            self._state.screen = "PRINTING"

    # Job Completion Callbacks
    async def job_photo_processed(self, filename: str, images: list):
        async with self._get_lock():
            self._state.isProcessing = False
            self._state.finalPhoto = filename
            self._state.allSessionPhotos.append({
                "filename": filename,
                "rawImages": images
            })
        await self.broadcast_state()

    async def job_frame_processed(self, filename: str):
        async with self._get_lock():
            self._state.isProcessing = False
            self._state.finalPhoto = filename
            self._state.screen = "PRINTING"
        await self.broadcast_state()

    async def job_failed(self, error: str):
        async with self._get_lock():
            self._state.isProcessing = False
            log.error("state_machine", "job_failed", f"Background job failed: {error}")
        await self.broadcast_state()

# Global Singleton
state_machine = StateMachine()

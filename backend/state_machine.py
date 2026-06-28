import asyncio
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from backend.logger import log
from backend.sse_service import sse_svc

class BoothState(BaseModel):
    screen: str = "ATTRACT"
    layoutMode: str = "single"
    capturedImages: List[str] = []
    finalPhoto: Optional[str] = None
    retakeCount: int = 0
    allSessionPhotos: List[Dict[str, Any]] = []
    isProcessing: bool = False
    config_overlays: List[Dict[str, Any]] = [] # We might need this for routing logic

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

    async def handle_event(self, event_type: str, payload: dict):
        async with self._get_lock():
            log.info("state_machine", "event_received", f"Handling event: {event_type}", data=payload)
            
            if event_type == "START_SESSION":
                self._state = BoothState(screen="CHOOSE_STYLE")
            
            elif event_type == "SELECT_LAYOUT":
                self._state.layoutMode = payload.get("mode", "single")
                self._state.screen = "COUNTDOWN"
                self._state.capturedImages = []
                self._state.retakeCount = 0
                self._state.finalPhoto = None
                self._state.allSessionPhotos = []
                
            elif event_type == "CAPTURE_DONE":
                images = payload.get("images", [])
                if not images:
                    log.error("state_machine", "capture_error", "No images provided in CAPTURE_DONE")
                    return
                    
                self._state.capturedImages = images
                self._state.screen = "REVEAL"
                self._state.isProcessing = True
                self._state.finalPhoto = None
                
                # Push job to queue
                if self._job_queue:
                    await self._job_queue.enqueue({
                        "type": "PROCESS_PHOTO",
                        "images": images,
                        "layout": self._state.layoutMode,
                        "text": payload.get("text", ""),
                        "overlay_id": payload.get("overlay_id", "none")
                    })
                    
            elif event_type == "RETAKE":
                self._state.retakeCount += 1
                self._state.capturedImages = []
                self._state.finalPhoto = None
                self._state.screen = "COUNTDOWN"
                
            elif event_type == "PRINT_FROM_REVEAL":
                if len(self._state.allSessionPhotos) > 1:
                    self._state.screen = "PICK_FAVORITE"
                else:
                    self._proceed_to_print_flow(payload.get("overlays", []))
                    
            elif event_type == "FAVORITE_SELECT":
                filename = payload.get("filename")
                self._state.finalPhoto = filename
                for p in self._state.allSessionPhotos:
                    if p["filename"] == filename:
                        self._state.capturedImages = p.get("rawImages", [])
                        break
                self._proceed_to_print_flow(payload.get("overlays", []))
                
            elif event_type == "FRAME_SELECT":
                self._state.isProcessing = True
                
                # Push job to queue
                if self._job_queue:
                    await self._job_queue.enqueue({
                        "type": "PROCESS_FRAME",
                        "images": self._state.capturedImages,
                        "layout": self._state.layoutMode,
                        "text": payload.get("text", ""),
                        "overlay_id": payload.get("overlay_id", "none")
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

    def _proceed_to_print_flow(self, overlays):
        has_frame_options = any(o.get("id") != "none" for o in overlays)
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

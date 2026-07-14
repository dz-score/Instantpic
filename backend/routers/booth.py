from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.settings import AppSettings
from backend.deps import get_settings
from backend.state_machine import state_machine

router = APIRouter(tags=["booth"])


class EventRequest(BaseModel):
    type: str
    payload: dict = Field(default_factory=dict)


@router.get("/api/state")
async def get_state():
    """Get current booth state."""
    return await state_machine.get_state()


@router.post("/api/events")
async def handle_event(req: EventRequest, settings: AppSettings = Depends(get_settings)):
    """Handle frontend events."""
    await state_machine.handle_event(req.type, req.payload, settings)
    return {"status": "ok"}

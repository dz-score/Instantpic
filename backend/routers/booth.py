from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.config import load_settings
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
async def handle_event(req: EventRequest):
    """Handle frontend events."""
    settings = load_settings()
    await state_machine.handle_event(req.type, req.payload, settings)
    return {"status": "ok"}

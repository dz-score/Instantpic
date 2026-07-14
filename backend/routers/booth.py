from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.settings import AppSettings
from backend.deps import get_settings, get_state_machine
from backend.state_machine import StateMachine

router = APIRouter(tags=["booth"])


class EventRequest(BaseModel):
    type: str
    payload: dict = Field(default_factory=dict)


@router.get("/api/state")
async def get_state(fsm: StateMachine = Depends(get_state_machine)):
    """Get current booth state."""
    return await fsm.get_state()


@router.post("/api/events")
async def handle_event(
    req: EventRequest,
    fsm: StateMachine = Depends(get_state_machine),
    settings: AppSettings = Depends(get_settings),
):
    """Handle frontend events."""
    await fsm.handle_event(req.type, req.payload, settings)
    return {"status": "ok"}

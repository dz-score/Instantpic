from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from backend.settings import AppSettings
from backend.deps import get_settings, get_sse, get_state_machine
from backend.sse_service import SseClient, SseService
from backend.state_machine import StateMachine

router = APIRouter(tags=["sse"])


@router.get("/api/sse")
async def sse_endpoint(
    request: Request,
    sse: SseService = Depends(get_sse),
    settings: AppSettings = Depends(get_settings),
    fsm: StateMachine = Depends(get_state_machine),
):
    """Eventstream for real-time updates to the frontend."""
    client = SseClient(request)
    sse.setup_client(client)
    # Seed this client with the current config immediately, so the frontend
    # receives it over the resilient stream (on connect and every reconnect)
    # instead of relying on a one-shot REST fetch.
    sse.send_to_client(client, "config_update", settings.model_dump())
    # Seed the current FSM state for the same reason, and for a sharper one:
    # state_update is the ONLY thing that drives the screen, and a client that
    # missed one has no way to ask for it. The frontend's REST fetchState() is
    # a one-shot on first mount, so before this a reconnect (3s window) or a
    # dropped event left the browser painting a stale screen until the next
    # transition — which at REVEAL or PICK_FAVORITE only the guest can cause,
    # on a screen showing them the wrong thing. Every (re)connect now resyncs.
    sse.send_to_client(client, "state_update", (await fsm.get_state()).model_dump())
    return EventSourceResponse(sse.event_iterator(client))

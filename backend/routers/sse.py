from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from backend.settings import AppSettings
from backend.deps import get_settings
from backend.sse_service import sse_svc, SseClient

router = APIRouter(tags=["sse"])


@router.get("/api/sse")
async def sse_endpoint(request: Request, settings: AppSettings = Depends(get_settings)):
    """Eventstream for real-time updates to the frontend."""
    client = SseClient(request)
    sse_svc.setup_client(client)
    # Seed this client with the current config immediately, so the frontend
    # receives it over the resilient stream (on connect and every reconnect)
    # instead of relying on a one-shot REST fetch.
    sse_svc.send_to_client(client, "config_update", settings.model_dump())
    return EventSourceResponse(sse_svc.event_iterator(client))

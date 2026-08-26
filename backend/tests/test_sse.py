import pytest
import asyncio
import threading
from unittest.mock import MagicMock, AsyncMock, patch
from backend.sse_service import SseClient, SseService

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.fixture
def sse_service():
    return SseService()

@pytest.fixture
def mock_request():
    req = MagicMock()
    req.is_disconnected = AsyncMock(return_value=False)
    return req

@pytest.mark.anyio
async def test_setup_and_remove_client(sse_service, mock_request):
    client = SseClient(mock_request)
    
    assert len(sse_service._clients) == 0
    sse_service.setup_client(client)
    assert len(sse_service._clients) == 1
    
    sse_service.remove_client(client)
    assert len(sse_service._clients) == 0

@pytest.mark.anyio
async def test_dispatch_event(sse_service, mock_request):
    client1 = SseClient(mock_request)
    client2 = SseClient(mock_request)
    
    sse_service.setup_client(client1)
    sse_service.setup_client(client2)
    
    sse_service.dispatch_event("test_event", {"key": "value"})
    
    # Check that both clients received the payload in their queue
    assert client1.queue.qsize() == 1
    assert client2.queue.qsize() == 1
    
    payload1 = client1.queue.get_nowait()
    assert payload1["event"] == "test_event"
    assert "value" in payload1["data"]

@pytest.mark.anyio
async def test_dispatch_event_queue_full(sse_service, mock_request):
    client = SseClient(mock_request)
    # Mock a tiny queue to trigger QueueFull easily
    client.queue = asyncio.Queue(maxsize=1)
    sse_service.setup_client(client)
    
    # First dispatch fills the queue
    sse_service.dispatch_event("test_event", {"num": 1})
    assert client.queue.qsize() == 1
    
    # Second dispatch should hit asyncio.QueueFull but be caught silently
    sse_service.dispatch_event("test_event", {"num": 2})
    assert client.queue.qsize() == 1  # Still 1, didn't crash

@pytest.mark.anyio
async def test_dispatch_event_from_thread_reaches_client(sse_service, mock_request):
    """Dispatches from camera worker/monitor threads must be marshalled onto
    the event loop — client queues are asyncio.Queues (not thread-safe)."""
    client = SseClient(mock_request)
    sse_service.setup_client(client)
    sse_service.bind_loop()

    t = threading.Thread(target=sse_service.dispatch_event, args=("thread_event", {"n": 1}))
    t.start()
    t.join()

    # The enqueue lands via call_soon_threadsafe — give the loop a few ticks.
    for _ in range(50):
        if client.queue.qsize():
            break
        await asyncio.sleep(0.01)

    assert client.queue.qsize() == 1
    payload = client.queue.get_nowait()
    assert payload["event"] == "thread_event"

@pytest.mark.anyio
async def test_send_to_client_from_thread_reaches_client(sse_service, mock_request):
    client = SseClient(mock_request)
    sse_service.setup_client(client)
    sse_service.bind_loop()

    t = threading.Thread(target=sse_service.send_to_client, args=(client, "seed_event", {"k": "v"}))
    t.start()
    t.join()

    for _ in range(50):
        if client.queue.qsize():
            break
        await asyncio.sleep(0.01)

    assert client.queue.get_nowait()["event"] == "seed_event"

def test_dispatch_from_thread_before_bind_is_dropped(mock_request):
    """The camera monitor thread starts at import time and can dispatch before
    startup binds a loop — that must be a silent drop, never a crash."""
    svc = SseService()
    client = SseClient(mock_request)
    svc.setup_client(client)

    t = threading.Thread(target=svc.dispatch_event, args=("early_event", {}))
    t.start()
    t.join()

    assert client.queue.qsize() == 0

@pytest.mark.anyio
async def test_event_iterator_yields_events(sse_service, mock_request):
    client = SseClient(mock_request)
    sse_service.setup_client(client)
    
    # Pre-fill an event
    sse_service.dispatch_event("test_event", {"key": "val"})
    
    # We must iterate it manually since it's an async generator
    iterator = sse_service.event_iterator(client)
    
    payload = await iterator.__anext__()
    assert payload["event"] == "test_event"
    assert "val" in payload["data"]

@pytest.mark.anyio
async def test_event_iterator_disconnect(sse_service, mock_request):
    client = SseClient(mock_request)
    sse_service.setup_client(client)
    
    # Simulate client disconnecting on the first check
    mock_request.is_disconnected.return_value = True
    
    iterator = sse_service.event_iterator(client)
    
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()
        
    # Ensure client was cleaned up
    assert len(sse_service._clients) == 0

@pytest.mark.anyio
async def test_event_iterator_timeout(sse_service, mock_request):
    client = SseClient(mock_request)
    sse_service.setup_client(client)
    
    iterator = sse_service.event_iterator(client)
    
    # We use patch to simulate the timeout fast without actually waiting 1s
    with patch('asyncio.wait_for', side_effect=asyncio.TimeoutError):
        # We need to simulate shutdown so it doesn't infinite loop when testing timeout
        sse_service.request_shutdown()
        
        with pytest.raises(StopAsyncIteration):
            await iterator.__anext__()


# --- Backpressure: dropping is correct, silence is not ---

@pytest.mark.anyio
async def test_full_client_queue_drops_and_says_so(sse_service, mock_request):
    """A client that stopped draining must not block the others, but the drop
    has to reach the log: the lost payload can be a state_update, and that is
    the only thing driving the guest's screen."""
    client = SseClient(mock_request)
    sse_service.setup_client(client)
    for _ in range(client.queue.maxsize):
        client.queue.put_nowait({"filler": True})

    with patch("backend.sse_service.log.warn") as mock_warn:
        sse_service.dispatch_event("state_update", {"screen": "REVEAL"})
        assert client.dropped == 1
        assert mock_warn.call_count == 1
        assert mock_warn.call_args[0][1] == "client_queue_full"

        # Still wedged: keep counting, but do not log per dropped event or the
        # noise buries the very session it is meant to explain.
        for _ in range(5):
            sse_service.dispatch_event("state_update", {"screen": "REVEAL"})
        assert client.dropped == 6
        assert mock_warn.call_count == 1

    # The per-client total is reported once, on disconnect.
    with patch("backend.sse_service.log.warn") as mock_warn:
        sse_service.remove_client(client)
        assert mock_warn.call_args[0][1] == "client_dropped_events"
        assert mock_warn.call_args[1]["data"]["dropped"] == 6


@pytest.mark.anyio
async def test_a_full_client_does_not_starve_a_healthy_one(sse_service, mock_request):
    """The reason dropping is the right policy: one wedged phone must not cost
    the booth panel its state updates."""
    wedged, healthy = SseClient(mock_request), SseClient(mock_request)
    sse_service.setup_client(wedged)
    sse_service.setup_client(healthy)
    for _ in range(wedged.queue.maxsize):
        wedged.queue.put_nowait({"filler": True})

    sse_service.dispatch_event("state_update", {"screen": "ATTRACT"})

    assert wedged.dropped == 1
    assert healthy.queue.qsize() == 1


# --- Reconnect resync ---

@pytest.mark.anyio
async def test_connect_seeds_both_config_and_state(mock_request):
    """state_update is the only thing that drives the screen and a client has
    no way to ask for it, so every (re)connect must be handed the current one.
    Without this a 3s reconnect gap left the browser on a stale screen until
    the next transition -- which at REVEAL only the guest can cause, on a
    screen showing them the wrong thing."""
    import json as _json
    from backend.routers.sse import sse_endpoint
    from backend.settings import AppSettings
    from backend.state_machine import BoothState

    svc = SseService()
    fsm = MagicMock()
    fsm.get_state = AsyncMock(return_value=BoothState(screen="REVEAL", finalPhoto="f.jpg"))

    await sse_endpoint(mock_request, sse=svc, settings=AppSettings(), fsm=fsm)

    client = svc._clients[0]
    seeded = [client.queue.get_nowait() for _ in range(client.queue.qsize())]
    assert [p["event"] for p in seeded] == ["config_update", "state_update"]

    state = _json.loads(seeded[1]["data"])
    assert state["screen"] == "REVEAL"
    assert state["finalPhoto"] == "f.jpg"

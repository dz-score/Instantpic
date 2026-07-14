import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator
from fastapi import Request
from backend.logger import log

class SseClient:
    def __init__(self, request: Request):
        self.request = request
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=100)

class SseService:
    def __init__(self):
        self._clients: list[SseClient] = []
        self._shutdown = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self):
        """Capture the running event loop so dispatches coming from other
        threads (camera worker/monitor) can be marshalled onto it. Called
        once at startup, from the lifespan handler."""
        self._loop = asyncio.get_running_loop()

    def setup_client(self, client: SseClient):
        self._clients.append(client)
        log.debug("sse", "client_connected", f"SSE client connected. Total clients: {len(self._clients)}")

    def remove_client(self, client: SseClient):
        if client in self._clients:
            self._clients.remove(client)
            log.debug("sse", "client_disconnected", f"SSE client disconnected. Total clients: {len(self._clients)}")

    def request_shutdown(self):
        self._shutdown = True
        log.debug("sse", "shutdown_requested", "SSE service shutdown requested")

    def _make_payload(self, event_type: str, data: dict) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "event": event_type,
            "data": json.dumps(data)
        }

    def _enqueue(self, client: SseClient, payload: dict):
        try:
            client.queue.put_nowait(payload)
        except asyncio.QueueFull:
            # If a client is too slow, we might drop events.
            # This usually means a stale connection.
            pass

    def _enqueue_all(self, payload: dict):
        for client in self._clients:
            self._enqueue(client, payload)

    def _run_on_loop(self, fn, *args):
        """Run `fn` on the event loop thread.

        The client queues are asyncio.Queues, which are not thread-safe:
        put_nowait() from a foreign thread can race the loop waking a
        parked consumer. Callers (state machine, camera worker/monitor
        threads) stay ignorant of threading — this boundary owns it.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            # Already on the event loop thread — enqueue directly.
            fn(*args)
            return

        loop = self._loop
        if loop is None or loop.is_closed():
            # Dispatched before startup bound a loop (the camera monitor
            # thread starts at import time) or after shutdown — no client
            # can be listening, so dropping is safe.
            return
        loop.call_soon_threadsafe(fn, *args)

    def send_to_client(self, client: SseClient, event_type: str, data: dict):
        """Send one event to a single client.

        Used to seed a newly connected client with the current snapshot (e.g.
        config) so it arrives over the same resilient channel as state, without
        a separate REST pull.
        """
        self._run_on_loop(self._enqueue, client, self._make_payload(event_type, data))

    def dispatch_event(self, event_type: str, data: dict):
        if not self._clients:
            return

        self._run_on_loop(self._enqueue_all, self._make_payload(event_type, data))

    async def event_iterator(self, client: SseClient) -> AsyncGenerator[dict, None]:
        try:
            while True:
                if await client.request.is_disconnected() or self._shutdown:
                    break

                try:
                    # Wait for an event, timeout to check for disconnects
                    payload = await asyncio.wait_for(client.queue.get(), timeout=1.0)
                    yield payload
                except asyncio.TimeoutError:
                    # Just yield a comment to keep connection alive if desired, 
                    # but sse_starlette can also send ping messages.
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            self.remove_client(client)

# No module-level singleton: the composition root (main.py's lifespan) constructs
# the SseService and hands it to everyone who dispatches events. Rule 19.

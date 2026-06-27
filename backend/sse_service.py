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

    def dispatch_event(self, event_type: str, data: dict):
        if not self._clients:
            return

        payload = {
            "id": str(uuid.uuid4()),
            "event": event_type,
            "data": json.dumps(data)
        }
        
        for client in self._clients:
            try:
                client.queue.put_nowait(payload)
            except asyncio.QueueFull:
                # If a client is too slow, we might drop events. 
                # This usually means a stale connection.
                pass

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

# Global singleton
sse_svc = SseService()

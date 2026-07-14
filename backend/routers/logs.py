import json
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from backend.logger import log, BACKEND_LOG, FRONTEND_LOG

router = APIRouter(tags=["logs"])


class FrontendLogBatch(BaseModel):
    lines: List[str]  # Pre-formatted JSONL lines from the frontend


def _tail_file(filepath, n):
    """Read last n lines from a file efficiently."""
    try:
        with open(filepath, "rb") as f:
            # Seek to end
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return []
            # Read last chunk (generous: 1KB per line estimate)
            chunk_size = min(size, n * 1024)
            f.seek(max(0, size - chunk_size))
            data = f.read().decode("utf-8", errors="replace")
            lines = data.strip().split("\n")
            return lines[-n:]
    except FileNotFoundError:
        return []


@router.post("/api/logs")
async def receive_frontend_logs(batch: FrontendLogBatch):
    """Receive a batch of JSONL log lines from the frontend and write to frontend.log."""
    for line in batch.lines:
        log.write_frontend_line(line)
    return {"status": "ok", "count": len(batch.lines)}


@router.get("/api/logs/recent")
async def get_recent_logs(count: int = 50, source: str = "both"):
    """Tail the last N lines from log files. source: 'backend', 'frontend', or 'both'."""
    entries = []

    if source in ("backend", "both"):
        for line in _tail_file(BACKEND_LOG, count):
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    if source in ("frontend", "both"):
        for line in _tail_file(FRONTEND_LOG, count):
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    # Sort by timestamp descending (newest first) and cap
    entries.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return entries[:count]

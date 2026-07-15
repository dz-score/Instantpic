"""
Structured JSONL logger for the photo booth backend.

- Writes one JSON object per line to logs/backend_<startup-timestamp>.log
- Each process startup gets its own timestamped file, so runs are never
  mixed together in the same log
- RotatingFileHandler: 5MB per file, 3 backups (~20MB max) as a safety net
  for a single very long-running session
- Also logs to stdout for systemd journal capture

This module is the sanctioned exception to Rule 19 (BACKEND_RULES): it opens
its log files and stamps the run at import time, and `log` is a module-level
singleton. Every module needs logging (including during import), so
constructor-threading it would be noise for no gain. The escape hatch for the
import-time side effect is BOOTH_LOG_DIR, which must be set BEFORE any backend
module is imported — conftest.py does this first thing.

Usage:
    from backend.logger import log
    log.info("printer", "printer_sent", "Print job sent", sid="s_abc", data={"printer": "Canon"})
    log.error("camera", "camera_init_fail", "Camera not found", data={"error": str(e)})
"""

import os
import json
import logging
import time
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone

# ── Paths ──
# BOOTH_LOG_DIR overrides the destination directory — used by the test
# suite (conftest.py) to keep pytest runs out of the real logs/ folder.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.environ.get("BOOTH_LOG_DIR") or os.path.join(BASE_DIR, "logs")
_STARTUP_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKEND_LOG = os.path.join(LOG_DIR, f"backend_{_STARTUP_TS}.log")
FRONTEND_LOG = os.path.join(LOG_DIR, f"frontend_{_STARTUP_TS}.log")

os.makedirs(LOG_DIR, exist_ok=True)

# ── JSONL Formatter ──
# Python's own levelname for logging.WARNING is "WARNING", but the frontend
# logger (logger.js) and the admin panel's CSS/level-filter both use "WARN" —
# normalize here so backend and frontend entries render identically.
_LEVELNAME_MAP = {"WARNING": "WARN"}


class JSONLFormatter(logging.Formatter):
    """Formats each log record as a single JSON line."""

    def format(self, record):
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") +
                  f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z",
            "level": _LEVELNAME_MAP.get(record.levelname, record.levelname),
            "source": "backend",
            "module": getattr(record, "mod", "system"),
            "event": getattr(record, "event", "log"),
            "msg": record.getMessage(),
            "sid": getattr(record, "sid", None),
            "dur": getattr(record, "dur", None),
            "data": getattr(record, "data", None),
        }
        return json.dumps(entry, ensure_ascii=False, default=str)


# ── Build the backend logger ──
_logger = logging.getLogger("photobooth.backend")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False

# File handler (rotated)
_file_handler = RotatingFileHandler(
    BACKEND_LOG,
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(JSONLFormatter())
_logger.addHandler(_file_handler)

# Stdout handler (for systemd journal / dev console)
_stdout_handler = logging.StreamHandler()
_stdout_handler.setLevel(logging.INFO)
_stdout_fmt = logging.Formatter("[%(asctime)s] %(levelname)-5s  %(message)s", datefmt="%H:%M:%S")
_stdout_handler.setFormatter(_stdout_fmt)
_logger.addHandler(_stdout_handler)


# ── Frontend log file handler ──
_frontend_logger = logging.getLogger("photobooth.frontend")
_frontend_logger.setLevel(logging.DEBUG)
_frontend_logger.propagate = False

_frontend_file_handler = RotatingFileHandler(
    FRONTEND_LOG,
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,
    encoding="utf-8",
)
_frontend_file_handler.setLevel(logging.DEBUG)


class FrontendPassthroughFormatter(logging.Formatter):
    """Writes the pre-formatted JSON line from the frontend as-is."""
    def format(self, record):
        return record.getMessage()


_frontend_file_handler.setFormatter(FrontendPassthroughFormatter())
_frontend_logger.addHandler(_frontend_file_handler)


# ── Public API ──
class BoothLogger:
    """Structured logger with module/event/session context."""

    def _log(self, level, module, event, msg, sid=None, dur=None, data=None):
        extra = {"mod": module, "event": event, "sid": sid, "dur": dur, "data": data}
        _logger.log(level, msg, extra=extra)

    def debug(self, module, event, msg, **kwargs):
        self._log(logging.DEBUG, module, event, msg, **kwargs)

    def info(self, module, event, msg, **kwargs):
        self._log(logging.INFO, module, event, msg, **kwargs)

    def warn(self, module, event, msg, **kwargs):
        self._log(logging.WARNING, module, event, msg, **kwargs)

    def error(self, module, event, msg, **kwargs):
        self._log(logging.ERROR, module, event, msg, **kwargs)

    def write_frontend_line(self, json_line: str):
        """Write a pre-formatted JSONL line from the frontend."""
        _frontend_logger.info(json_line)


log = BoothLogger()

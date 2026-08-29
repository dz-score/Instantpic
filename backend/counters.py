"""Tallies the booth keeps for itself, across restarts.

Separate from `AppSettings` because the two have opposite lifecycles. Settings
are operator decisions: deliberate, reviewed, worth committing. These are
numbers the booth writes to itself every few seconds while an event runs.

They lived in config.json first, and that was wrong in a way that showed up
immediately: config.json is tracked, so every print dirtied the working tree and
a print count was one `git commit -a` away from being shipped as configuration.

Same durability requirement as settings, though — a booth restarted mid-event
must not hand back a budget it has already spent — so this persists, atomically,
and survives a corrupt file the same way settings do.
"""

import json
import os
import threading
from typing import Dict

from backend.logger import log
from backend.paths import COUNTERS_PATH


class Counters:
    """Named integer tallies, persisted. One instance per process, built at the
    composition root."""

    def __init__(self, path: str = None):
        # Resolved here rather than as a default argument: a default binds once
        # at import and would ignore a monkeypatched path (same reasoning as
        # SettingsService).
        self._path = path if path is not None else COUNTERS_PATH
        self._lock = threading.RLock()
        self._values: Dict[str, int] = {}

    def load(self) -> None:
        """Read the file once. Never raises: a missing or unreadable tally is a
        tally of zero, not a booth that will not boot."""
        with self._lock:
            self._values = {}
            if not os.path.exists(self._path):
                return
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._values = {k: int(v) for k, v in data.items()
                                if isinstance(v, (int, float))}
            except (OSError, ValueError, AttributeError, UnicodeDecodeError) as e:
                log.error("counters", "counters_load_failed",
                          f"Could not read {self._path} ({type(e).__name__}); "
                          f"starting every tally from zero", data={"error": str(e)})

    def get(self, name: str) -> int:
        with self._lock:
            return self._values.get(name, 0)

    def all(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._values)

    def increment(self, name: str, by: int = 1) -> int:
        """Add to a tally and persist it. Returns the new value.

        The read-modify-write happens under the lock, so two callers cannot both
        read 41 and both write 42. Today only the print lane counts and it is
        strictly serial, which makes this belt and braces — but a lost print in
        a budget is invisible until the budget is wrong.
        """
        with self._lock:
            self._values[name] = self._values.get(name, 0) + by
            self._flush()
            return self._values[name]

    def set(self, name: str, value: int) -> int:
        with self._lock:
            self._values[name] = int(value)
            self._flush()
            return self._values[name]

    def _flush(self) -> None:
        """Write atomically, and never let a failed write break a print.

        Temp file alongside, then os.replace — same reasoning as write_settings:
        write-in-place truncates first, so a crash mid-write leaves a file that
        cannot be parsed. Unlike settings this swallows the error: losing a tally
        matters far less than failing the print that was reporting it.
        """
        tmp = f"{self._path}.tmp"
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._values, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except OSError as e:
            log.error("counters", "counters_write_failed",
                      f"Could not persist tallies: {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass

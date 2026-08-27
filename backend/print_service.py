"""
Production-oriented print service for the photo booth.

Architecture:
    PrinterDriver (ABC)  ← abstract interface
        ├── CupsPrinterDriver  ← talks to any CUPS-managed printer (Epson, DNP, etc.)
        └── MockPrinterDriver  ← dev/Windows mock

    PrintService (singleton) ← wraps the driver with retry, status caching, structured logging

Swapping printers = changing printer_name in config.json (CUPS queue name).
No code changes needed.

A print is two phases, and the driver contract keeps them apart: print_file()
SUBMITS the job (a dye-sub queue accepts one in ~100ms), await_job() blocks until
that job reaches a terminal state (a DNP RX1HS takes ~12s per 4x6). Every failure
that matters at an event — ribbon out, paper out, jam, printer switched off —
happens after submission, so a service that reported the submit result as the
print result would tell a guest "done" and hand them nothing.
"""

import os
import re
import sys
import time
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Dict, Optional

from backend.settings import PrinterMockConfig, SettingsService
from backend.logger import log


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class PrintResult:
    """Outcome of a single print attempt."""
    success: bool
    job_id: Optional[str] = None
    error: Optional[str] = None
    duration_ms: int = 0

    def to_dict(self):
        return asdict(self)


# Terminal states a submitted job can reach. `completed` is the only success.
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"      # printer stopped mid-job: out of media, jam, powered off
JOB_TIMEOUT = "timeout"    # still not terminal when we gave up waiting
JOB_UNKNOWN = "unknown"    # we lost the ability to observe the job at all


@dataclass
class JobOutcome:
    """How a submitted job ended. Distinct from PrintResult, which also covers
    submission — a job can be accepted and still never reach paper."""
    state: str
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.state == JOB_COMPLETED

    def to_dict(self):
        return asdict(self)


@dataclass
class PrinterStatus:
    """Current state of the printer."""
    connected: bool
    ready: bool             # idle and accepting jobs
    printer_name: str
    status_text: str        # "Idle", "Printing", "Offline", etc.
    error: Optional[str] = None
    # Consumables. None means the driver has no way to know — an inkjet on CUPS
    # cannot answer this, and neither can a dye-sub whose backend does not
    # report markers. Absent is not the same as empty, so keep them Optional.
    media_type: Optional[str] = None
    prints_remaining: Optional[int] = None

    def to_dict(self):
        return asdict(self)


# ── Abstract driver ───────────────────────────────────────────────────────────

class PrinterDriver(ABC):
    """Interface that every printer backend must implement."""

    @abstractmethod
    def print_file(self, filepath: str, options: str) -> PrintResult:
        """Submit a file to the printer. Success here means the queue ACCEPTED
        the job, not that anything was printed — see await_job()."""
        ...

    @abstractmethod
    def await_job(self, job_id: str, timeout_s: float) -> JobOutcome:
        """Block until the submitted job reaches a terminal state. Blocking is
        the point: the caller runs on the job queue's print lane, off the event
        loop, and the guest is watching the printing animation until this returns."""
        ...

    @abstractmethod
    def get_status(self) -> PrinterStatus:
        """Query the printer's current status."""
        ...

    @abstractmethod
    def cancel_all(self) -> bool:
        """Cancel all pending jobs. Returns True on success."""
        ...


# ── CUPS driver ───────────────────────────────────────────────────────────────

class CupsPrinterDriver(PrinterDriver):
    """Drives any CUPS-managed printer via the `lp` / `lpstat` CLI."""

    # How often await_job asks CUPS where the job got to. A dye-sub print is
    # ~12s, so a 1s poll is ~12 subprocesses per print — cheap next to the
    # alternative of linking pycups, which needs libcups2-dev on the Pi.
    JOB_POLL_INTERVAL_S = 1.0
    # Consecutive failed observations tolerated before we admit we've lost the
    # job. One hiccup while cupsd reloads should not fail a good print.
    OBSERVE_FAILURE_LIMIT = 3

    def __init__(self, printer_name: str):
        self.printer_name = printer_name

    # ── lpstat helpers ────────────────────────────────────────────────────────

    def _lpstat(self, args: list, timeout: float = 5) -> Optional[str]:
        """Run an lpstat variant. Returns stdout, or None if it could not be
        run or the queue was rejected — callers decide what absence means."""
        try:
            r = subprocess.run(
                ["lpstat"] + args,
                capture_output=True, text=True, timeout=timeout,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        return r.stdout if r.returncode == 0 else None

    def _queued_job_ids(self) -> Optional[set]:
        """Ids of jobs CUPS still considers not-completed. The job id is the
        first token of each line. None means we could not look."""
        out = self._lpstat(["-o", self.printer_name])
        if out is None:
            return None
        return {line.split()[0] for line in out.splitlines() if line.split()}

    def _stop_reason(self) -> Optional[str]:
        """The reason CUPS has stopped this queue, or None if it is running.

        This is how the failures that matter actually present: out of ribbon,
        out of paper, jam and power-off all stop the queue rather than quietly
        discarding the job. `lpstat -p` prints the reason on the line after the
        `disabled since ...` header, so both are worth keeping.
        """
        out = self._lpstat(["-p", self.printer_name])
        if out is None:
            return None
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if not lines:
            return None
        head = lines[0].lower()
        if "disabled" not in head and "stopped" not in head:
            return None
        detail = lines[1].rstrip(" -") if len(lines) > 1 else ""
        return detail or lines[0]

    def print_file(self, filepath: str, options: str) -> PrintResult:
        start = time.monotonic()
        try:
            # Build command: lp -d <queue> -o opt1 -o opt2 <file>
            cmd = ["lp", "-d", self.printer_name]
            for opt in options.split():
                cmd.extend(["-o", opt])
            cmd.append(filepath)

            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            duration = int((time.monotonic() - start) * 1000)

            if result.returncode != 0:
                error_msg = result.stderr.strip() or f"lp exited with code {result.returncode}"
                return PrintResult(
                    success=False,
                    error=error_msg,
                    duration_ms=duration,
                )

            # Parse job ID from lp output, e.g. "request id is MyPrinter-42 (1 file(s))"
            job_id = None
            match = re.search(r"request id is (\S+)", result.stdout)
            if match:
                job_id = match.group(1)

            return PrintResult(
                success=True,
                job_id=job_id,
                duration_ms=duration,
            )

        except subprocess.TimeoutExpired:
            duration = int((time.monotonic() - start) * 1000)
            return PrintResult(
                success=False,
                error="Print command timed out after 30s",
                duration_ms=duration,
            )
        except FileNotFoundError:
            duration = int((time.monotonic() - start) * 1000)
            return PrintResult(
                success=False,
                error="CUPS 'lp' command not found — is CUPS installed?",
                duration_ms=duration,
            )
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            return PrintResult(
                success=False,
                error=str(e),
                duration_ms=duration,
            )

    def await_job(self, job_id: str, timeout_s: float) -> JobOutcome:
        """Poll CUPS until the job is off the queue, the queue stops, or we run out
        of patience.

        Why the queue's state and not the job's: `lpstat -W completed` is IPP
        `which-jobs=completed`, which returns completed, aborted AND canceled jobs
        together — it cannot tell success from failure. The queue can. Out of
        ribbon, out of paper, a jam and a power-off all stop the queue and leave
        our job sitting in it, which is exactly the pair of facts we check.

        Known imprecision: a job an operator cancels from the CUPS web UI also
        leaves the queue and reads here as completed. That mislabels an outcome
        the operator caused deliberately and already knows about, which is a far
        better trade than missing a jam.
        """
        deadline = time.monotonic() + timeout_s
        misses = 0

        while True:
            queued = self._queued_job_ids()

            if queued is None:
                # Could not look. Tolerate a hiccup; give up if it persists,
                # because claiming a print we cannot see is the failure mode
                # this whole method exists to remove.
                misses += 1
                if misses >= self.OBSERVE_FAILURE_LIMIT:
                    return JobOutcome(
                        JOB_UNKNOWN,
                        f"Lost track of job {job_id} — lpstat stopped answering",
                    )
            else:
                misses = 0
                if job_id not in queued:
                    return JobOutcome(JOB_COMPLETED)
                # Still queued. Only now does a stopped queue mean OUR job is
                # stuck — checking it first would fail a print that finished
                # just before someone opened the media door.
                reason = self._stop_reason()
                if reason:
                    return JobOutcome(JOB_FAILED, reason)

            if time.monotonic() >= deadline:
                return JobOutcome(
                    JOB_TIMEOUT,
                    f"Job {job_id} still queued after {timeout_s:.0f}s",
                )
            time.sleep(self.JOB_POLL_INTERVAL_S)

    def get_status(self) -> PrinterStatus:
        try:
            result = subprocess.run(
                ["lpstat", "-p", self.printer_name],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return PrinterStatus(
                    connected=False,
                    ready=False,
                    printer_name=self.printer_name,
                    status_text="Not found",
                    error=result.stderr.strip() or "Printer queue not found in CUPS",
                )

            output = result.stdout.strip()

            # Parse lpstat output for status keywords
            is_idle = "idle" in output.lower()
            is_disabled = "disabled" in output.lower()
            is_printing = "printing" in output.lower()

            if is_disabled:
                status_text = "Disabled"
                ready = False
            elif is_printing:
                status_text = "Printing"
                ready = False
            elif is_idle:
                status_text = "Idle"
                ready = True
            else:
                status_text = output[:80]
                ready = True

            return PrinterStatus(
                connected=True,
                ready=ready,
                printer_name=self.printer_name,
                status_text=status_text,
            )

        except subprocess.TimeoutExpired:
            return PrinterStatus(
                connected=False,
                ready=False,
                printer_name=self.printer_name,
                status_text="Status check timed out",
                error="lpstat timed out after 5s",
            )
        except FileNotFoundError:
            return PrinterStatus(
                connected=False,
                ready=False,
                printer_name=self.printer_name,
                status_text="CUPS not installed",
                error="'lpstat' command not found",
            )
        except Exception as e:
            return PrinterStatus(
                connected=False,
                ready=False,
                printer_name=self.printer_name,
                status_text=f"Error: {e}",
                error=str(e),
            )

    def cancel_all(self) -> bool:
        try:
            subprocess.run(
                ["cancel", "-a", self.printer_name],
                capture_output=True,
                timeout=5,
            )
            return True
        except Exception:
            return False


# ── Mock driver ───────────────────────────────────────────────────────────────

class MockPrinterDriver(PrinterDriver):
    """Development mock, shaped like a DNP DS-RX1HS.

    Not a stub. `_reload_driver` picks this driver on Windows whatever
    `printer_name` says, so until the real printer is on the bench it is the
    only printer this project can be developed against. That means it has to be
    wrong in the same ways a dye-sub is wrong — accept a job instantly, take
    ~13 s to actually print it, run out of ribbon, jam — or none of the handling
    for those cases can be written, let alone tested.

    Knobs live in `AppSettings.printer_mock`. It holds the SettingsService
    rather than a snapshot for the same reason PrintService does: a fault
    flipped in the admin panel has to land on the next print, not the next
    restart.
    """

    # How a submitted job is going to end, decided at submit time.
    _END_OK = "ok"
    _END_OUT_OF_MEDIA = "out_of_media"
    _END_JAM = "abort_mid_job"

    def __init__(self, printer_name: str = "mock", settings: Optional[SettingsService] = None):
        self.printer_name = printer_name
        self._settings = settings
        self._job_counter = 0
        # Seeded on first use, not here: constructors do no work (Rule 19), and
        # a later media_total edit then reads as "a fresh roll went in".
        self._media_total: Optional[int] = None
        self._media_remaining = 0
        self._pending: Dict[str, str] = {}
        self._submit_already_failed = False

    def _cfg(self) -> PrinterMockConfig:
        """Defaults when nobody handed us settings — a bare MockPrinterDriver()
        still has to behave, because that is how the tests build it."""
        if self._settings is None:
            return PrinterMockConfig()
        return self._settings.get().printer_mock

    def _sync_media(self, cfg: PrinterMockConfig) -> int:
        if self._media_total != cfg.media_total:
            self._media_total = cfg.media_total
            self._media_remaining = cfg.media_total
        return self._media_remaining

    def print_file(self, filepath: str, options: str) -> PrintResult:
        cfg = self._cfg()
        self._sync_media(cfg)

        if cfg.fault == "offline":
            return PrintResult(success=False, error="Mock printer is offline")

        # Fails the first submission and accepts the retry, which is the whole
        # point of it — it exercises the one retry PrintService still does.
        # Resets whenever the fault is cleared so it can be rehearsed again.
        if cfg.fault != "submit_fails_once":
            self._submit_already_failed = False
        elif not self._submit_already_failed:
            self._submit_already_failed = True
            return PrintResult(success=False, error="lp: Error - Mock rejected the first submission")

        self._job_counter += 1
        job_id = f"MOCK-{self._job_counter}"

        # The ending is fixed at submit time because that is when the hardware
        # fixes it too: the ribbon is already short before the job starts.
        # Exhaustion outranks the configured fault — a roll with nothing left
        # cannot print, whatever else is set.
        if self._media_remaining <= 0:
            ending = self._END_OUT_OF_MEDIA
        elif cfg.fault in (self._END_OUT_OF_MEDIA, self._END_JAM):
            ending = cfg.fault
        else:
            ending = self._END_OK
        self._pending[job_id] = ending

        log.info("printer", "printer_mock", f"Mock print: {os.path.basename(filepath)}", data={
            "job_id": job_id,
            "options": options,
            "ending": ending,
            "prints_remaining": self._media_remaining,
        })

        return PrintResult(success=True, job_id=job_id, duration_ms=0)

    def await_job(self, job_id: str, timeout_s: float) -> JobOutcome:
        cfg = self._cfg()
        ending = self._pending.pop(job_id, self._END_OK)

        # A job that would outlast the caller's patience times out here rather
        # than quietly finishing, so the timeout path is reachable in dev.
        if cfg.job_duration_s > timeout_s:
            time.sleep(timeout_s)
            return JobOutcome(JOB_TIMEOUT, f"Job {job_id} still queued after {timeout_s:.0f}s")

        if ending == self._END_OK:
            time.sleep(cfg.job_duration_s)
            self._media_remaining = max(0, self._media_remaining - 1)
            return JobOutcome(JOB_COMPLETED)

        # Both faults surface partway through, which is the point of them: they
        # land after the guest has already been told the print is on its way.
        time.sleep(cfg.job_duration_s / 2)
        if ending == self._END_OUT_OF_MEDIA:
            return JobOutcome(JOB_FAILED, "Media tray empty.")
        return JobOutcome(JOB_FAILED, "Paper jam.")

    def get_status(self) -> PrinterStatus:
        cfg = self._cfg()
        remaining = self._sync_media(cfg)

        if cfg.fault == "offline":
            return PrinterStatus(
                connected=False,
                ready=False,
                printer_name=self.printer_name,
                status_text="Offline (mock fault)",
                error="Mock printer is offline",
            )

        empty = remaining <= 0
        return PrinterStatus(
            connected=True,
            ready=not empty,
            printer_name=self.printer_name,
            status_text="Out of media (mock)" if empty else "Mock printer (development)",
            media_type="4x6",
            prints_remaining=remaining,
        )

    def cancel_all(self) -> bool:
        self._pending.clear()
        return True


# ── Print service (singleton) ────────────────────────────────────────────────

class PrintService:
    """
    High-level print service wrapping a PrinterDriver.

    Responsibilities:
    - Driver selection based on config
    - File validation
    - Retry on submission failures (and only those)
    - Waiting out the job so the reported outcome is the printed outcome
    - Status caching (avoids spamming lpstat)
    - Structured logging for every operation
    """

    RETRY_DELAY_S = 3
    STATUS_CACHE_TTL_S = 5
    # Ceiling on phase 2. A DNP RX1HS is ~12s per 4x6 and the job queue's print
    # lane is serial, so ours is the only job in CUPS — this is slack, not a
    # budget. It also has to stay under the LED firmware's ~120s printing-mode
    # timeout (Docs/LED_SPEC.md) so the ring only falls to Error if the backend
    # itself has hung, not merely because a print was slow.
    JOB_TIMEOUT_S = 90

    def __init__(self, settings: SettingsService):
        # Holds the SettingsService, not an AppSettings snapshot: _reload_driver runs
        # on every job so that swapping the printer in the admin panel takes effect
        # without a restart. A boot-time snapshot would freeze the printer choice.
        self._settings = settings
        self._driver: Optional[PrinterDriver] = None
        self._cached_status: Optional[PrinterStatus] = None
        self._status_cache_time: float = 0
        # No driver built here — Rule 19 forbids work in a constructor. Every public
        # entry point (print/get_status/cancel_all) calls _reload_driver() anyway.

    def _reload_driver(self):
        """Build the driver the config names — and keep the one we already have
        if that is already it.

        Rebuilding unconditionally was free while drivers were stateless. The
        mock now carries a media counter across prints, and every public entry
        point calls this, so a fresh instance per call would reset the roll on
        every status poll. It also logged a line each time.
        """
        name = self._settings.get().printer_name
        want = MockPrinterDriver if (name == "mock" or sys.platform == "win32") else CupsPrinterDriver

        if type(self._driver) is want and self._driver.printer_name == name:
            return

        # The mock is handed the service, not a snapshot: its faults and timings
        # are meant to be changed from the admin panel and take effect now.
        self._driver = MockPrinterDriver(name, self._settings) if want is MockPrinterDriver             else CupsPrinterDriver(name)
        log.info("printer", "printer_driver_loaded", f"Using {want.__name__}", data={"printer_name": name})

    def print(self, filepath: str) -> PrintResult:
        """
        Print a file and block until it has actually printed.

        Returns success only when the job reached paper. Blocking is deliberate:
        the caller is the job queue's print lane, which runs in a thread pool, and
        the guest is watching the printing animation until this returns.
        """
        # Reload driver in case config changed (printer swapped)
        self._reload_driver()

        settings = self._settings.get()
        options = settings.printer_options

        # Validate file
        if not os.path.exists(filepath):
            log.error("printer", "printer_file_missing", f"File not found: {filepath}")
            return PrintResult(success=False, error=f"File not found: {filepath}")

        filename = os.path.basename(filepath)
        log.info("printer", "printer_job_start", f"Print job started: {filename}", data={
            "filename": filename,
            "printer": settings.printer_name,
            "options": options,
        })

        started = time.monotonic()

        def elapsed_ms():
            return int((time.monotonic() - started) * 1000)

        # ── Phase 1: submit ──────────────────────────────────────────────────
        # Retry belongs here and nowhere else. A submission failure means nothing
        # reached the printer, so a second attempt cannot produce a second print.
        result = self._driver.print_file(filepath, options)

        if not result.success:
            log.warn("printer", "printer_retry", f"Submission failed: {result.error}, retrying in {self.RETRY_DELAY_S}s...", data={
                "filename": filename,
                "error": result.error,
            })
            time.sleep(self.RETRY_DELAY_S)
            result = self._driver.print_file(filepath, options)

        if not result.success:
            log.error("printer", "printer_job_fail", f"Print failed: {filename} — {result.error}", dur=elapsed_ms(), data={
                "filename": filename,
                "error": result.error,
                "phase": "submit",
            })
            self._status_cache_time = 0
            return PrintResult(success=False, error=result.error, duration_ms=elapsed_ms())

        # ── Phase 2: wait for paper ──────────────────────────────────────────
        # No retry past this point. A jam that gets cleared and silently reprinted
        # hands the guest two photos and burns two prints of media; a failure the
        # operator can see is the better outcome. Docs/API_PROTOCOL.md promises
        # exactly this ("The booth does not automatically retry printing").
        if not result.job_id:
            # The driver accepted the file but gave us nothing to follow. Say so
            # rather than dressing an unverified submission up as a finished print.
            log.warn("printer", "printer_job_untracked", f"Print accepted but not trackable: {filename}", dur=elapsed_ms(), data={
                "filename": filename,
            })
            return PrintResult(success=True, job_id=None, duration_ms=elapsed_ms())

        log.info("printer", "printer_job_accepted", f"Queued as {result.job_id}, waiting for the print", data={
            "filename": filename,
            "job_id": result.job_id,
        })

        outcome = self._driver.await_job(result.job_id, self.JOB_TIMEOUT_S)

        # Invalidate status cache — the printer has moved since we last looked.
        self._status_cache_time = 0

        if outcome.ok:
            log.info("printer", "printer_job_done", f"Print completed: {filename}", dur=elapsed_ms(), data={
                "filename": filename,
                "job_id": result.job_id,
            })
            return PrintResult(success=True, job_id=result.job_id, duration_ms=elapsed_ms())

        log.error("printer", "printer_job_fail", f"Print failed: {filename} — {outcome.message}", dur=elapsed_ms(), data={
            "filename": filename,
            "job_id": result.job_id,
            "error": outcome.message,
            "phase": outcome.state,
        })
        return PrintResult(
            success=False,
            job_id=result.job_id,
            error=outcome.message or f"Print job ended as {outcome.state}",
            duration_ms=elapsed_ms(),
        )

    def get_status(self) -> PrinterStatus:
        """Get printer status with caching to avoid spamming lpstat."""
        now = time.monotonic()
        if self._cached_status and (now - self._status_cache_time) < self.STATUS_CACHE_TTL_S:
            return self._cached_status

        self._reload_driver()
        self._cached_status = self._driver.get_status()
        self._status_cache_time = now

        log.debug("printer", "printer_status_check", f"Printer status: {self._cached_status.status_text}", data={
            "connected": self._cached_status.connected,
            "ready": self._cached_status.ready,
        })

        return self._cached_status

    def cancel_all(self) -> bool:
        """Cancel all pending print jobs."""
        self._reload_driver()
        success = self._driver.cancel_all()
        if success:
            log.info("printer", "printer_queue_cleared", "All print jobs cancelled")
        else:
            log.error("printer", "printer_cancel_fail", "Failed to cancel print jobs")
        self._status_cache_time = 0
        return success

    def shutdown(self):
        """Clean shutdown of the print service."""
        log.info("printer", "printer_shutdown", "Shutting down print service...")
        # (Could cancel pending jobs here if we were tracking them, but we just let cups handle it)

# No module-level singleton: PrintService needs settings, and the composition root
# (main.py's lifespan) is the one place that has them. It builds the service and
# hands it to the job queue and the routes.

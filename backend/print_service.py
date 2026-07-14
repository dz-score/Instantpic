"""
Production-oriented print service for the photo booth.

Architecture:
    PrinterDriver (ABC)  ← abstract interface
        ├── CupsPrinterDriver  ← talks to any CUPS-managed printer (Epson, DNP, etc.)
        └── MockPrinterDriver  ← dev/Windows mock

    PrintService (singleton) ← wraps the driver with retry, status caching, structured logging

Swapping printers = changing printer_name in config.json (CUPS queue name).
No code changes needed.
"""

import os
import re
import sys
import time
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Optional

from backend.config import get_settings
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


@dataclass
class PrinterStatus:
    """Current state of the printer."""
    connected: bool
    ready: bool             # idle and accepting jobs
    printer_name: str
    status_text: str        # "Idle", "Printing", "Offline", etc.
    error: Optional[str] = None

    def to_dict(self):
        return asdict(self)


# ── Abstract driver ───────────────────────────────────────────────────────────

class PrinterDriver(ABC):
    """Interface that every printer backend must implement."""

    @abstractmethod
    def print_file(self, filepath: str, options: str) -> PrintResult:
        """Send a file to the printer. Returns result with success/failure."""
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

    def __init__(self, printer_name: str):
        self.printer_name = printer_name

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
    """Development mock — simulates a 2-second print with structured logging."""

    def __init__(self, printer_name: str = "mock"):
        self.printer_name = printer_name
        self._job_counter = 0

    def print_file(self, filepath: str, options: str) -> PrintResult:
        self._job_counter += 1
        job_id = f"MOCK-{self._job_counter}"

        log.info("printer", "printer_mock", f"Mock print: {os.path.basename(filepath)}", data={
            "job_id": job_id,
            "options": options,
        })

        # Simulate print time
        time.sleep(2)

        return PrintResult(
            success=True,
            job_id=job_id,
            duration_ms=2000,
        )

    def get_status(self) -> PrinterStatus:
        return PrinterStatus(
            connected=True,
            ready=True,
            printer_name=self.printer_name,
            status_text="Mock printer (development)",
        )

    def cancel_all(self) -> bool:
        return True


# ── Print service (singleton) ────────────────────────────────────────────────

class PrintService:
    """
    High-level print service wrapping a PrinterDriver.

    Responsibilities:
    - Driver selection based on config
    - File validation
    - Retry on recoverable failures
    - Status caching (avoids spamming lpstat)
    - Structured logging for every operation
    """

    RETRY_DELAY_S = 3
    STATUS_CACHE_TTL_S = 5

    def __init__(self):
        self._driver: Optional[PrinterDriver] = None
        self._cached_status: Optional[PrinterStatus] = None
        self._status_cache_time: float = 0
        self._reload_driver()

    def _reload_driver(self):
        """Load (or reload) the correct driver from config."""
        settings = get_settings()
        name = settings.printer_name

        if name == "mock" or sys.platform == "win32":
            self._driver = MockPrinterDriver(name)
            log.info("printer", "printer_driver_loaded", "Using MockPrinterDriver", data={"printer_name": name})
        else:
            self._driver = CupsPrinterDriver(name)
            log.info("printer", "printer_driver_loaded", "Using CupsPrinterDriver", data={"printer_name": name})

    def print(self, filepath: str) -> PrintResult:
        """
        Print a file with validation, retry, and logging.

        Retry logic: if the first attempt fails with a recoverable error
        (printer busy, timeout), waits RETRY_DELAY_S and tries once more.
        """
        # Reload driver in case config changed (printer swapped)
        self._reload_driver()

        settings = get_settings()
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

        # First attempt
        result = self._driver.print_file(filepath, options)

        # Retry once on failure
        if not result.success:
            log.warn("printer", "printer_retry", f"First attempt failed: {result.error}, retrying in {self.RETRY_DELAY_S}s...", data={
                "filename": filename,
                "error": result.error,
            })
            time.sleep(self.RETRY_DELAY_S)
            result = self._driver.print_file(filepath, options)

        # Log outcome
        if result.success:
            log.info("printer", "printer_job_done", f"Print completed: {filename}", dur=result.duration_ms, data={
                "filename": filename,
                "job_id": result.job_id,
            })
        else:
            log.error("printer", "printer_job_fail", f"Print failed: {filename} — {result.error}", dur=result.duration_ms, data={
                "filename": filename,
                "error": result.error,
            })

        # Invalidate status cache after a print attempt
        self._status_cache_time = 0

        return result

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


# ── Global singleton ──────────────────────────────────────────────────────────

print_svc = PrintService()

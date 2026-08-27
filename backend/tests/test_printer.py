import os
import subprocess
import pytest
from backend.print_service import (
    PrintService, CupsPrinterDriver, MockPrinterDriver,
    PrintResult, PrinterStatus, JobOutcome,
    JOB_COMPLETED, JOB_FAILED, JOB_TIMEOUT, JOB_UNKNOWN,
)


def test_mock_driver_success():
    """MockPrinterDriver should always return success."""
    driver = MockPrinterDriver("mock")
    result = driver.print_file("/tmp/test.jpg", "fit-to-page")
    assert result.success is True
    assert result.job_id is not None
    assert result.job_id.startswith("MOCK-")


def test_mock_driver_status():
    """MockPrinterDriver should always report connected and ready."""
    driver = MockPrinterDriver("mock")
    status = driver.get_status()
    assert status.connected is True
    assert status.ready is True


def test_cups_driver_print_success(mocker):
    """CupsPrinterDriver should parse job ID from lp output on success."""
    mock_run = mocker.patch("backend.print_service.subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="request id is EPSON_ET2850-42 (1 file(s))",
        stderr="",
    )

    driver = CupsPrinterDriver("EPSON_ET2850")
    result = driver.print_file("/tmp/test.jpg", "fit-to-page media=4x6")

    assert result.success is True
    assert result.job_id == "EPSON_ET2850-42"
    # Verify correct command shape
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "lp"
    assert "-d" in call_args
    assert "EPSON_ET2850" in call_args
    assert "-o" in call_args
    assert "fit-to-page" in call_args
    assert "media=4x6" in call_args


def test_cups_driver_print_failure(mocker):
    """CupsPrinterDriver should return failure with error message on non-zero exit."""
    mock_run = mocker.patch("backend.print_service.subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=1,
        stdout="",
        stderr="lp: Error - The printer or class does not exist.",
    )

    driver = CupsPrinterDriver("BadPrinter")
    result = driver.print_file("/tmp/test.jpg", "fit-to-page")

    assert result.success is False
    assert "does not exist" in result.error


def test_cups_driver_print_timeout(mocker):
    """CupsPrinterDriver should handle subprocess timeout gracefully."""
    mock_run = mocker.patch("backend.print_service.subprocess.run")
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="lp", timeout=30)

    driver = CupsPrinterDriver("EPSON_ET2850")
    result = driver.print_file("/tmp/test.jpg", "fit-to-page")

    assert result.success is False
    assert "timed out" in result.error


def test_cups_driver_status_idle(mocker):
    """CupsPrinterDriver should parse idle status from lpstat."""
    mock_run = mocker.patch("backend.print_service.subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="printer EPSON_ET2850 is idle.",
        stderr="",
    )

    driver = CupsPrinterDriver("EPSON_ET2850")
    status = driver.get_status()

    assert status.connected is True
    assert status.ready is True
    assert status.status_text == "Idle"


def test_cups_driver_status_not_found(mocker):
    """CupsPrinterDriver should report disconnected when queue is missing."""
    mock_run = mocker.patch("backend.print_service.subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=1,
        stdout="",
        stderr="lpstat: Invalid destination name",
    )

    driver = CupsPrinterDriver("BadPrinter")
    status = driver.get_status()

    assert status.connected is False
    assert status.ready is False


def test_print_service_file_not_found(mocker):
    """PrintService.print() should return failure if file doesn't exist."""
    # PrintService takes its settings service, so a stub goes straight in.
    settings_svc = mocker.Mock()
    settings_svc.get.return_value = mocker.Mock(
        printer_name="mock", printer_options="fit-to-page"
    )
    svc = PrintService(settings_svc)
    result = svc.print("/nonexistent/file.jpg")
    assert result.success is False
    assert "not found" in result.error.lower()


def test_print_result_to_dict():
    """PrintResult.to_dict() should return a plain dictionary."""
    r = PrintResult(success=True, job_id="TEST-1", duration_ms=500)
    d = r.to_dict()
    assert d["success"] is True
    assert d["job_id"] == "TEST-1"
    assert d["duration_ms"] == 500
    assert d["error"] is None


# ── await_job: lpstat parsing ────────────────────────────────────────────────

LPSTAT_QUEUED = (
    "DS-RX1-42               rahim            1024   Wed 27 Aug 2026 10:00:00 AM\n"
    "DS-RX1-43               rahim            1024   Wed 27 Aug 2026 10:00:05 AM\n"
)
LPSTAT_STOPPED = (
    "printer DS-RX1 disabled since Wed 27 Aug 2026 10:00:00 AM -\n"
    "\tMedia tray empty.\n"
)
LPSTAT_RUNNING = "printer DS-RX1 is idle.  enabled since Wed 27 Aug 2026 09:00:00 AM\n"


def test_queued_job_ids_parses_first_token(mocker):
    """Job ids are the first whitespace token of each lpstat -o line."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=LPSTAT_QUEUED)
    assert driver._queued_job_ids() == {"DS-RX1-42", "DS-RX1-43"}


def test_queued_job_ids_empty_queue(mocker):
    """An empty queue is an empty set, not an unobservable queue."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value="")
    assert driver._queued_job_ids() == set()


def test_queued_job_ids_none_when_lpstat_unusable(mocker):
    """None means "could not look", which await_job must not read as done."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=None)
    assert driver._queued_job_ids() is None


def test_stop_reason_reads_the_detail_line(mocker):
    """The reason CUPS gives sits on the line after "disabled since ..."."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=LPSTAT_STOPPED)
    assert driver._stop_reason() == "Media tray empty."


def test_stop_reason_none_while_running(mocker):
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=LPSTAT_RUNNING)
    assert driver._stop_reason() is None


# ── await_job: outcomes ──────────────────────────────────────────────────────

def test_await_job_completed_when_job_leaves_queue(mocker):
    """Job present, then gone, and the queue never stopped -> completed."""
    mocker.patch("backend.print_service.time.sleep")
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_queued_job_ids",
                        side_effect=[{"DS-RX1-42"}, {"DS-RX1-42"}, set()])
    mocker.patch.object(driver, "_stop_reason", return_value=None)

    outcome = driver.await_job("DS-RX1-42", timeout_s=90)

    assert outcome.state == JOB_COMPLETED
    assert outcome.ok is True


def test_await_job_failed_when_queue_stops_with_job_still_in_it(mocker):
    """Out of media / jam / power-off: the queue stops and holds our job."""
    mocker.patch("backend.print_service.time.sleep")
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_queued_job_ids", return_value={"DS-RX1-42"})
    mocker.patch.object(driver, "_stop_reason", return_value="Media tray empty.")

    outcome = driver.await_job("DS-RX1-42", timeout_s=90)

    assert outcome.state == JOB_FAILED
    assert outcome.ok is False
    assert "Media tray empty." in outcome.message


def test_await_job_ignores_a_stopped_queue_once_our_job_is_gone(mocker):
    """A queue stopped AFTER our job finished must not fail a print that
    already came out - the job leaving is checked first."""
    mocker.patch("backend.print_service.time.sleep")
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_queued_job_ids", return_value=set())
    stop = mocker.patch.object(driver, "_stop_reason", return_value="Media tray empty.")

    outcome = driver.await_job("DS-RX1-42", timeout_s=90)

    assert outcome.state == JOB_COMPLETED
    stop.assert_not_called()


def test_await_job_times_out_while_still_queued(mocker):
    """Still queued at the deadline, with a healthy queue, is a timeout."""
    mocker.patch("backend.print_service.time.sleep")
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_queued_job_ids", return_value={"DS-RX1-42"})
    mocker.patch.object(driver, "_stop_reason", return_value=None)

    outcome = driver.await_job("DS-RX1-42", timeout_s=0)

    assert outcome.state == JOB_TIMEOUT
    assert outcome.ok is False


def test_await_job_tolerates_one_lpstat_hiccup(mocker):
    """A single unobservable poll must not fail an otherwise good print."""
    mocker.patch("backend.print_service.time.sleep")
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_queued_job_ids",
                        side_effect=[{"DS-RX1-42"}, None, set()])
    mocker.patch.object(driver, "_stop_reason", return_value=None)

    assert driver.await_job("DS-RX1-42", timeout_s=90).state == JOB_COMPLETED


def test_await_job_unknown_when_lpstat_keeps_failing(mocker):
    """Persistent blindness is reported, not papered over as success."""
    mocker.patch("backend.print_service.time.sleep")
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_queued_job_ids", return_value=None)

    outcome = driver.await_job("DS-RX1-42", timeout_s=90)

    assert outcome.state == JOB_UNKNOWN
    assert outcome.ok is False


def test_mock_driver_await_job_completes():
    driver = MockPrinterDriver("mock")
    job = driver.print_file("/tmp/test.jpg", "")
    assert driver.await_job(job.job_id, timeout_s=90).state == JOB_COMPLETED


# ── PrintService: the two-phase flow and the retry split ─────────────────────

def _service_with_driver(mocker, driver):
    """PrintService rebuilds its driver on every entry point, so a test driver
    has to survive _reload_driver."""
    settings_svc = mocker.Mock()
    settings_svc.get.return_value = mocker.Mock(
        printer_name="mock", printer_options="media=4x6"
    )
    svc = PrintService(settings_svc)
    mocker.patch.object(svc, "_reload_driver", side_effect=lambda: None)
    svc._driver = driver
    return svc


def test_print_reports_success_only_after_the_job_prints(mocker, tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.print_file.return_value = PrintResult(success=True, job_id="DS-RX1-42")
    driver.await_job.return_value = JobOutcome(JOB_COMPLETED)

    result = _service_with_driver(mocker, driver).print(str(f))

    assert result.success is True
    assert result.job_id == "DS-RX1-42"
    driver.await_job.assert_called_once()


def test_print_fails_when_the_job_aborts_after_acceptance(mocker, tmp_path):
    """The bug this task exists to remove: accepted is not printed."""
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.print_file.return_value = PrintResult(success=True, job_id="DS-RX1-42")
    driver.await_job.return_value = JobOutcome(JOB_FAILED, "Media tray empty.")

    result = _service_with_driver(mocker, driver).print(str(f))

    assert result.success is False
    assert "Media tray empty." in result.error


def test_print_does_not_retry_after_acceptance(mocker, tmp_path):
    """Reprinting a jammed job would double-print once the jam is cleared."""
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.print_file.return_value = PrintResult(success=True, job_id="DS-RX1-42")
    driver.await_job.return_value = JobOutcome(JOB_FAILED, "Media tray empty.")

    _service_with_driver(mocker, driver).print(str(f))

    assert driver.print_file.call_count == 1


def test_print_retries_once_on_submission_failure(mocker, tmp_path):
    """Nothing reached the printer, so a second submit cannot double-print."""
    mocker.patch("backend.print_service.time.sleep")
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.print_file.side_effect = [
        PrintResult(success=False, error="lp: Error - Bad file"),
        PrintResult(success=True, job_id="DS-RX1-43"),
    ]
    driver.await_job.return_value = JobOutcome(JOB_COMPLETED)

    result = _service_with_driver(mocker, driver).print(str(f))

    assert driver.print_file.call_count == 2
    assert result.success is True


def test_print_gives_up_after_two_failed_submissions(mocker, tmp_path):
    mocker.patch("backend.print_service.time.sleep")
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.print_file.return_value = PrintResult(success=False, error="queue gone")

    result = _service_with_driver(mocker, driver).print(str(f))

    assert result.success is False
    assert driver.print_file.call_count == 2
    driver.await_job.assert_not_called()


def test_print_does_not_claim_a_print_it_cannot_track(mocker, tmp_path):
    """No job id means no way to wait - report the accepted submission as-is
    rather than inventing a completion."""
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.print_file.return_value = PrintResult(success=True, job_id=None)

    result = _service_with_driver(mocker, driver).print(str(f))

    assert result.success is True
    driver.await_job.assert_not_called()

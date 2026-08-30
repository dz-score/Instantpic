import os
import subprocess
import pytest
from backend.print_service import (
    PrintService, CupsPrinterDriver, MockPrinterDriver,
    PrintResult, PrinterStatus, JobOutcome,
    JOB_ABORTED, JOB_COMPLETED, JOB_FAILED, JOB_TIMEOUT, JOB_UNKNOWN,
)
from backend.settings import PrinterMockConfig


class InstantAbort:
    """Abort event that never blocks. Waits are recorded rather than slept, so a
    test can assert on a print's duration without spending it."""

    def __init__(self):
        self._set = False
        self.waits = []

    def set(self):
        self._set = True

    def is_set(self):
        return self._set

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return self._set


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
        printer_name="mock", printer_options="fit-to-page",
        printer_media_low_threshold=25, prints_used=0, print_allowance=150,
    )
    svc = PrintService(settings_svc)
    result = svc.print("/nonexistent/file.jpg")
    assert result.success is False
    assert "not found" in result.error.lower()


def _svc(mocker, printer_name="DS-RX1"):
    settings_svc = mocker.Mock()
    settings_svc.get.return_value = mocker.Mock(
        printer_name=printer_name, printer_options="media=w288h432 scaling=100",
        printer_media_low_threshold=25, prints_used=0, print_allowance=150,
    )
    return PrintService(settings_svc)


def test_print_refuses_to_submit_to_an_absent_printer(mocker, tmp_path):
    """Queueing a job to a printer that is not there leaves work in the queue
    that has to be detected in flight and cancelled. Catch it first."""
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"x")
    svc = _svc(mocker)
    driver = mocker.Mock()
    driver.device_present.return_value = False
    driver.recover.return_value = None
    mocker.patch.object(svc, "_reload_driver")
    svc._driver = driver

    result = svc.print(str(photo))

    assert result.success is False
    assert "not connected" in result.error.lower()
    driver.print_file.assert_not_called()


def test_print_proceeds_when_presence_is_unknown(mocker, tmp_path):
    """None is "could not tell". Refusing to print on it would ground the booth
    wherever lpinfo needs privileges we do not have."""
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"x")
    svc = _svc(mocker)
    driver = mocker.Mock()
    driver.device_present.return_value = None
    driver.recover.return_value = None
    driver.print_file.return_value = PrintResult(success=True, job_id="DS-RX1-1")
    driver.await_job.return_value = JobOutcome(JOB_COMPLETED)
    mocker.patch.object(svc, "_reload_driver")
    svc._driver = driver

    assert svc.print(str(photo)).success is True
    driver.print_file.assert_called_once()


def test_status_reports_not_connected_when_the_device_is_gone(mocker):
    """The admin panel showed an unplugged printer as Idle: lpstat describes the
    queue, which stays idle and enabled with the hardware switched off."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch("backend.print_service.subprocess.run",
                 return_value=subprocess.CompletedProcess(
                     args=[], returncode=0,
                     stdout="printer DS-RX1 is idle.  enabled since Mon 31 Aug 2026",
                     stderr=""))
    mocker.patch.object(driver, "device_present", return_value=False)

    status = driver.get_status()

    assert status.connected is False
    assert status.ready is False
    assert status.status_text == "Not connected"


def test_status_stays_connected_when_presence_is_unknown(mocker):
    """Don't show the operator a disconnected printer that is sitting there
    working just because lpinfo would not run."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch("backend.print_service.subprocess.run",
                 return_value=subprocess.CompletedProcess(
                     args=[], returncode=0,
                     stdout="printer DS-RX1 is idle.  enabled since Mon 31 Aug 2026",
                     stderr=""))
    mocker.patch.object(driver, "device_present", return_value=None)
    mocker.patch.object(driver, "_read_media", return_value=(None, None))

    status = driver.get_status()

    assert status.connected is True
    assert status.status_text == "Idle"


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

# Verbatim from the booth's Pi with the DS-RX1 switched off. Note the queue
# reported "idle. enabled" with no fault at the same moment — the job is the
# only thing that knows anything is wrong.
LPSTAT_JOB_UNREADY = (
    "DS-RX1-18               instantpic        1024   Mon 31 Aug 2026 12:34:56 AM CEST\n"
    "        Status: Printer open failure (No matching printers found!)\n"
    "        Alerts: resources-are-not-ready\n"
    "        queued for DS-RX1\n"
)
LPSTAT_JOB_HEALTHY = (
    "DS-RX1-18               instantpic        1024   Mon 31 Aug 2026 12:34:56 AM CEST\n"
    "        Status: Sending data to printer.\n"
    "        queued for DS-RX1\n"
)
# Someone else's job is stuck; ours is fine. Must not be confused for ours.
LPSTAT_JOB_OTHER_UNREADY = (
    "DS-RX1-17               instantpic        1024   Mon 31 Aug 2026 12:30:00 AM CEST\n"
    "        Status: Printer open failure (No matching printers found!)\n"
    "        Alerts: resources-are-not-ready\n"
    "DS-RX1-18               instantpic        1024   Mon 31 Aug 2026 12:34:56 AM CEST\n"
    "        Status: Sending data to printer.\n"
)


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


# ── Device presence: lpinfo sees what lpstat cannot ──────────────────────────

DEVICE_URI = "gutenprint53+usb://dnp-dsrx1/CB2D63217299"
LPSTAT_V = f"device for DS-RX1: {DEVICE_URI}\n"

# Verbatim from the booth's Pi. With the printer powered off the DNP line is
# simply absent — `lpstat -p` is identical either way, this is not.
LPINFO_PRESENT = (
    "network socket\n"
    "direct vnc:/\n"
    "network beh\n"
    f"direct {DEVICE_URI}\n"
    "network ipp\n"
)
LPINFO_ABSENT = (
    "network socket\n"
    "direct vnc:/\n"
    "network beh\n"
    "network ipp\n"
)


def _lpinfo(mocker, stdout, returncode=0):
    return mocker.patch("backend.print_service.subprocess.run",
                        return_value=subprocess.CompletedProcess(
                            args=[], returncode=returncode, stdout=stdout, stderr=""))


def test_device_uri_read_from_the_queue(mocker):
    """The URI comes from the queue's own config, not hardcoded per printer."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=LPSTAT_V)
    assert driver._device_uri() == DEVICE_URI


def test_device_present_true_when_lpinfo_lists_the_uri(mocker):
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=LPSTAT_V)
    _lpinfo(mocker, LPINFO_PRESENT)
    assert driver.device_present() is True


def test_device_present_false_when_the_printer_is_off(mocker):
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=LPSTAT_V)
    _lpinfo(mocker, LPINFO_ABSENT)
    assert driver.device_present() is False


def test_device_present_probes_only_this_queues_scheme(mocker):
    """A bare `lpinfo -v` walks the network backends too, which costs seconds of
    discovery on a venue LAN — far too slow to sit in front of every print."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=LPSTAT_V)
    run = _lpinfo(mocker, LPINFO_PRESENT)

    driver.device_present()

    cmd = run.call_args[0][0]
    assert "--include-schemes" in cmd
    assert cmd[cmd.index("--include-schemes") + 1] == "gutenprint53+usb"


def test_device_present_unknown_when_lpinfo_unavailable(mocker):
    """None, not False. lpinfo can want privileges we lack, and a diagnostic
    that cannot run must never be read as a missing printer."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=LPSTAT_V)
    mocker.patch("backend.print_service.subprocess.run", side_effect=FileNotFoundError)
    assert driver.device_present() is None


def test_device_present_unknown_when_the_queue_has_no_uri(mocker):
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=None)
    assert driver.device_present() is None


def test_base_driver_reports_presence_unknown():
    """A driver with no way to look must not block prints."""
    assert MockPrinterDriver("mock").device_present() is None


# ── Job alerts: the powered-off printer the queue does not report ────────────

def test_job_trouble_reads_the_status_line(mocker):
    """The Status line names what an operator has to go and fix."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=LPSTAT_JOB_UNREADY)
    assert driver._job_trouble("DS-RX1-18") == (
        "Printer open failure (No matching printers found!)"
    )


def test_job_trouble_none_for_a_healthy_job(mocker):
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=LPSTAT_JOB_HEALTHY)
    assert driver._job_trouble("DS-RX1-18") is None


def test_job_trouble_only_reads_our_own_job(mocker):
    """Alerts are per-job. Another stuck job must not fail this guest's print."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=LPSTAT_JOB_OTHER_UNREADY)
    assert driver._job_trouble("DS-RX1-18") is None
    assert driver._job_trouble("DS-RX1-17") is not None


def test_job_trouble_none_when_lpstat_unusable(mocker):
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_lpstat", return_value=None)
    assert driver._job_trouble("DS-RX1-18") is None


def test_await_job_fails_fast_when_the_printer_is_switched_off(mocker):
    """The bug this closes: queue enabled and idle, job quietly waiting, so
    nothing fired and the guest watched a spinner out to the 90s ceiling."""
    driver = CupsPrinterDriver("DS-RX1", InstantAbort())
    mocker.patch.object(driver, "_queued_job_ids", return_value={"DS-RX1-18"})
    mocker.patch.object(driver, "_stop_reason", return_value=None)
    mocker.patch.object(driver, "_job_trouble",
                        return_value="Printer open failure (No matching printers found!)")

    outcome = driver.await_job("DS-RX1-18", timeout_s=90)

    assert outcome.state == JOB_FAILED
    assert outcome.ok is False
    assert "Printer open failure" in outcome.message


def test_await_job_tolerates_a_momentary_not_ready(mocker):
    """A job can report not-ready for an instant while CUPS opens the device.
    One blip must not fail a print that then goes on to come out."""
    driver = CupsPrinterDriver("DS-RX1", InstantAbort())
    mocker.patch.object(driver, "_queued_job_ids",
                        side_effect=[{"DS-RX1-18"}, {"DS-RX1-18"}, set()])
    mocker.patch.object(driver, "_stop_reason", return_value=None)
    mocker.patch.object(driver, "_job_trouble",
                        side_effect=["resources-are-not-ready", None, None])

    outcome = driver.await_job("DS-RX1-18", timeout_s=90)

    assert outcome.state == JOB_COMPLETED


# ── await_job: outcomes ──────────────────────────────────────────────────────

def test_await_job_completed_when_job_leaves_queue(mocker):
    """Job present, then gone, and the queue never stopped -> completed."""
    driver = CupsPrinterDriver("DS-RX1", InstantAbort())
    mocker.patch.object(driver, "_queued_job_ids",
                        side_effect=[{"DS-RX1-42"}, {"DS-RX1-42"}, set()])
    mocker.patch.object(driver, "_stop_reason", return_value=None)

    outcome = driver.await_job("DS-RX1-42", timeout_s=90)

    assert outcome.state == JOB_COMPLETED
    assert outcome.ok is True


def test_await_job_failed_when_queue_stops_with_job_still_in_it(mocker):
    """Out of media / jam / power-off: the queue stops and holds our job."""
    driver = CupsPrinterDriver("DS-RX1", InstantAbort())
    mocker.patch.object(driver, "_queued_job_ids", return_value={"DS-RX1-42"})
    mocker.patch.object(driver, "_stop_reason", return_value="Media tray empty.")

    outcome = driver.await_job("DS-RX1-42", timeout_s=90)

    assert outcome.state == JOB_FAILED
    assert outcome.ok is False
    assert "Media tray empty." in outcome.message


def test_await_job_ignores_a_stopped_queue_once_our_job_is_gone(mocker):
    """A queue stopped AFTER our job finished must not fail a print that
    already came out - the job leaving is checked first."""
    driver = CupsPrinterDriver("DS-RX1", InstantAbort())
    mocker.patch.object(driver, "_queued_job_ids", return_value=set())
    stop = mocker.patch.object(driver, "_stop_reason", return_value="Media tray empty.")

    outcome = driver.await_job("DS-RX1-42", timeout_s=90)

    assert outcome.state == JOB_COMPLETED
    stop.assert_not_called()


def test_await_job_times_out_while_still_queued(mocker):
    """Still queued at the deadline, with a healthy queue, is a timeout."""
    driver = CupsPrinterDriver("DS-RX1", InstantAbort())
    mocker.patch.object(driver, "_queued_job_ids", return_value={"DS-RX1-42"})
    mocker.patch.object(driver, "_stop_reason", return_value=None)

    outcome = driver.await_job("DS-RX1-42", timeout_s=0)

    assert outcome.state == JOB_TIMEOUT
    assert outcome.ok is False


def test_await_job_tolerates_one_lpstat_hiccup(mocker):
    """A single unobservable poll must not fail an otherwise good print."""
    driver = CupsPrinterDriver("DS-RX1", InstantAbort())
    mocker.patch.object(driver, "_queued_job_ids",
                        side_effect=[{"DS-RX1-42"}, None, set()])
    mocker.patch.object(driver, "_stop_reason", return_value=None)

    assert driver.await_job("DS-RX1-42", timeout_s=90).state == JOB_COMPLETED


def test_await_job_unknown_when_lpstat_keeps_failing(mocker):
    """Persistent blindness is reported, not papered over as success."""
    driver = CupsPrinterDriver("DS-RX1", InstantAbort())
    mocker.patch.object(driver, "_queued_job_ids", return_value=None)

    outcome = driver.await_job("DS-RX1-42", timeout_s=90)

    assert outcome.state == JOB_UNKNOWN
    assert outcome.ok is False




# ── PrintService: the two-phase flow and the retry split ─────────────────────

def _service_with_driver(mocker, driver):
    """PrintService rebuilds its driver on every entry point, so a test driver
    has to survive _reload_driver."""
    settings_svc = mocker.Mock()
    settings_svc.get.return_value = mocker.Mock(
        printer_name="mock", printer_options="media=4x6",
        printer_media_low_threshold=25, prints_used=0, print_allowance=150,
    )
    svc = PrintService(settings_svc)
    svc._abort = InstantAbort()
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


# ── Mock driver shaped like a DS-RX1HS ───────────────────────────────────────

def _mock_settings(mocker, printer_name="mock", media_low_threshold=25,
                   prints_used=0, print_allowance=150, **overrides):
    """The mock driver's whole behaviour comes from AppSettings.printer_mock,
    so tests configure it the same way the admin panel does."""
    cfg = PrinterMockConfig(**{"job_duration_s": 0.0, **overrides})
    svc = mocker.Mock()
    svc.get.return_value = mocker.Mock(
        printer_name=printer_name,
        printer_options="media=4x6",
        printer_media_low_threshold=media_low_threshold,
        prints_used=prints_used,
        print_allowance=print_allowance,
        printer_mock=cfg,
    )
    return svc


def _mock_driver(mocker, abort=None, **overrides):
    return MockPrinterDriver("mock", _mock_settings(mocker, **overrides),
                             abort if abort is not None else InstantAbort())


def _service(settings):
    """PrintService whose waits do not cost real time. Swapped before the first
    call, because _reload_driver hands the event to the driver it builds."""
    svc = PrintService(settings)
    svc._abort = InstantAbort()
    return svc


def test_mock_await_takes_the_configured_print_time(mocker):
    """The guest watches the printing animation for exactly this long, so the
    number has to reach the wait."""
    abort = InstantAbort()
    driver = _mock_driver(mocker, abort=abort, job_duration_s=13.0)

    job = driver.print_file("/tmp/p.jpg", "")
    assert driver.await_job(job.job_id, timeout_s=90).state == JOB_COMPLETED
    assert abort.waits == [13.0]


def test_mock_submit_is_instant(mocker):
    """Submission must not wait — that is the whole distinction the two-phase
    contract exists to make."""
    abort = InstantAbort()
    _mock_driver(mocker, abort=abort, job_duration_s=13.0).print_file("/tmp/p.jpg", "")
    assert abort.waits == []


def test_mock_times_out_when_the_print_outlasts_the_caller(mocker):
    driver = _mock_driver(mocker, job_duration_s=13.0)
    job = driver.print_file("/tmp/p.jpg", "")
    assert driver.await_job(job.job_id, timeout_s=5).state == JOB_TIMEOUT


def test_mock_await_gives_up_when_the_booth_is_shutting_down(mocker):
    """A print sits in a thread pool where cancelling the coroutine does not
    reach it, so the wait itself has to be interruptible."""
    abort = InstantAbort()
    driver = _mock_driver(mocker, abort=abort, job_duration_s=13.0)
    job = driver.print_file("/tmp/p.jpg", "")

    abort.set()

    assert driver.await_job(job.job_id, timeout_s=90).state == JOB_ABORTED


def test_mock_media_counts_down_per_print(mocker):
    driver = _mock_driver(mocker, media_total=700)
    assert driver.get_status().prints_remaining == 700

    for _ in range(3):
        job = driver.print_file("/tmp/p.jpg", "")
        driver.await_job(job.job_id, timeout_s=90)

    status = driver.get_status()
    assert status.prints_remaining == 697
    assert status.media_type == "4x6"


def test_mock_media_is_not_spent_by_a_failed_print(mocker):
    driver = _mock_driver(mocker, media_total=700, fault="abort_mid_job")
    job = driver.print_file("/tmp/p.jpg", "")
    assert driver.await_job(job.job_id, timeout_s=90).state == JOB_FAILED
    assert driver.get_status().prints_remaining == 700


def test_mock_runs_out_of_media(mocker):
    """media_total=1 is how you rehearse the end of a roll in a few seconds."""
    driver = _mock_driver(mocker, media_total=1)

    first = driver.print_file("/tmp/p.jpg", "")
    assert driver.await_job(first.job_id, timeout_s=90).state == JOB_COMPLETED

    second = driver.print_file("/tmp/p.jpg", "")
    outcome = driver.await_job(second.job_id, timeout_s=90)
    assert outcome.state == JOB_FAILED
    assert "Media tray empty." in outcome.message

    status = driver.get_status()
    assert status.prints_remaining == 0
    assert status.ready is False


def test_mock_reloading_media_total_is_a_new_roll(mocker):
    settings = _mock_settings(mocker, media_total=2)
    driver = MockPrinterDriver("mock", settings)

    job = driver.print_file("/tmp/p.jpg", "")
    driver.await_job(job.job_id, timeout_s=90)
    assert driver.get_status().prints_remaining == 1

    settings.get.return_value.printer_mock = PrinterMockConfig(
        job_duration_s=0.0, media_total=700
    )
    assert driver.get_status().prints_remaining == 700


def test_mock_fault_out_of_media_lands_after_acceptance(mocker):
    """The failure has to arrive late, after the guest has been told the print
    is on its way — that is the case the booth used to get wrong."""
    driver = _mock_driver(mocker, fault="out_of_media")

    submitted = driver.print_file("/tmp/p.jpg", "")
    assert submitted.success is True

    outcome = driver.await_job(submitted.job_id, timeout_s=90)
    assert outcome.state == JOB_FAILED
    assert "Media tray empty." in outcome.message


def test_mock_fault_jam_lands_after_acceptance(mocker):
    driver = _mock_driver(mocker, fault="abort_mid_job")
    submitted = driver.print_file("/tmp/p.jpg", "")
    assert submitted.success is True
    assert "Paper jam." in driver.await_job(submitted.job_id, timeout_s=90).message


def test_mock_fault_offline_rejects_and_reports(mocker):
    driver = _mock_driver(mocker, fault="offline")

    assert driver.print_file("/tmp/p.jpg", "").success is False

    status = driver.get_status()
    assert status.connected is False
    assert status.ready is False


def test_mock_fault_submit_fails_once_then_accepts(mocker):
    driver = _mock_driver(mocker, fault="submit_fails_once")
    assert driver.print_file("/tmp/p.jpg", "").success is False
    assert driver.print_file("/tmp/p.jpg", "").success is True


def test_mock_fault_submit_fails_once_rearms_when_cleared(mocker):
    settings = _mock_settings(mocker, fault="submit_fails_once")
    driver = MockPrinterDriver("mock", settings)
    assert driver.print_file("/tmp/p.jpg", "").success is False

    settings.get.return_value.printer_mock = PrinterMockConfig(job_duration_s=0.0)
    assert driver.print_file("/tmp/p.jpg", "").success is True

    settings.get.return_value.printer_mock = PrinterMockConfig(
        job_duration_s=0.0, fault="submit_fails_once"
    )
    assert driver.print_file("/tmp/p.jpg", "").success is False


def test_bare_mock_driver_still_works():
    """Constructed with no settings it falls back to PrinterMockConfig defaults,
    because that is how a few call sites and tests build it."""
    driver = MockPrinterDriver("mock")
    assert driver.get_status().connected is True
    assert driver.print_file("/tmp/p.jpg", "").success is True


# ── Driver selection keeps state across calls ────────────────────────────────

def test_reload_driver_keeps_the_instance_so_media_survives(mocker, tmp_path):
    """Every public entry point calls _reload_driver. Rebuilding each time would
    reset the roll on every status poll."""
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    svc = _service(_mock_settings(mocker, media_total=700))
    svc.print(str(f))
    first = svc._driver

    svc.get_status()
    svc.print(str(f))

    assert svc._driver is first
    assert svc.get_status().prints_remaining == 698


def test_reload_driver_swaps_when_the_queue_name_changes(mocker):
    mocker.patch("backend.print_service.sys.platform", "linux")
    settings = _mock_settings(mocker)
    svc = _service(settings)

    # cancel_all reloads unconditionally; get_status can answer from its 5s
    # cache without reselecting, which is fine in production and useless here.
    svc.cancel_all()
    assert isinstance(svc._driver, MockPrinterDriver)

    settings.get.return_value.printer_name = "DS-RX1"
    svc.cancel_all()
    assert isinstance(svc._driver, CupsPrinterDriver)
    assert svc._driver.printer_name == "DS-RX1"


# ── The mock driving the real PrintService ───────────────────────────────────

def test_service_recovers_from_a_first_submission_failure(mocker, tmp_path):
    mocker.patch("backend.print_service.time.sleep")
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    result = _service(_mock_settings(mocker, fault="submit_fails_once")).print(str(f))

    assert result.success is True


def test_service_reports_a_mid_job_jam_as_a_failure(mocker, tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    result = _service(_mock_settings(mocker, fault="abort_mid_job")).print(str(f))

    assert result.success is False
    assert "Paper jam." in result.error


# ── Reading consumables out of CUPS ──────────────────────────────────────────

# Verbatim from a DS-RX1HS on 6x4 media, 2026-08-29 (CUPS+Gutenprint 5.3.4).
# Trimmed to the lines that matter; the real response is ~9.8 KB. Keep it real:
# an invented fixture here is how a parser passes its tests and returns None on
# the booth.
IPPTOOL_OUTPUT = """\
"/usr/share/cups/ipptool/get-printer-attributes.test":
    Get printer attributes using get-printer-attributes                  [PASS]
        RECEIVED: 9808 bytes in response
        status-code = successful-ok (successful-ok)
        printer-state (enum) = idle
        device-uri (uri) = gutenprint53+usb://dnp-dsrx1/CB2D63217299
        marker-change-time (integer) = 1788014278
        marker-colors (nameWithoutLanguage) = #00FFFF#FF00FF#FFFF00
        marker-levels (integer) = 98
        marker-message (textWithoutLanguage) = 692 native prints remaining on 6x4 (PC) media
        marker-low-levels (integer) = 10
        marker-high-levels (integer) = 100
        marker-names (nameWithoutLanguage) = 6x4 (PC)
        marker-types (keyword) = ribbonWax
"""


def test_ipp_attributes_keeps_only_the_marker_lines(mocker):
    run = mocker.patch("backend.print_service.subprocess.run")
    run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=IPPTOOL_OUTPUT, stderr="")

    markers = CupsPrinterDriver("DS-RX1")._ipp_attributes()

    assert markers["marker-names"] == "6x4 (PC)"
    assert markers["marker-levels"] == "98"
    assert "692 native prints remaining" in markers["marker-message"]
    assert "printer-state" not in markers


def test_ipp_attributes_latches_a_missing_ipptool(mocker):
    """cups-ipp-utils is not always installed. Without the latch its absence
    would cost a failed subprocess on every 5s status poll, forever."""
    run = mocker.patch("backend.print_service.subprocess.run",
                       side_effect=FileNotFoundError())
    driver = CupsPrinterDriver("DS-RX1")

    assert driver._ipp_attributes() is None
    assert driver._ipp_attributes() is None
    assert driver._ipp_attributes() is None

    assert run.call_count == 1


def test_read_media_takes_the_count_from_the_message(mocker):
    """Confirmed on hardware: a nearly-full 700-print roll reports levels=98 and
    a message of 692. marker-levels is a percentage — reading the count from it
    would have shown "98 prints left" on a full roll."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_ipp_attributes", return_value={
        "marker-levels": "98",
        "marker-message": "692 native prints remaining on 6x4 (PC) media",
        "marker-names": "6x4 (PC)",
    })

    media_type, remaining = driver._read_media()

    assert remaining == 692
    assert media_type == "6x4"


def test_read_media_falls_back_to_the_marker_name(mocker):
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_ipp_attributes", return_value={
        "marker-message": "300 prints remaining",
        "marker-names": "Ribbon",
    })
    assert driver._read_media() == ("Ribbon", 300)


def test_read_media_claims_nothing_without_markers(mocker):
    """Absent is not empty. A printer that cannot report must not be shown as
    a spent one."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_ipp_attributes", return_value=None)
    assert driver._read_media() == (None, None)


def test_read_media_survives_an_unparseable_message(mocker):
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_ipp_attributes", return_value={
        "marker-message": "Ribbon status unknown",
    })
    assert driver._read_media() == (None, None)


# ── The low-media threshold ──────────────────────────────────────────────────

def _poll(svc):
    """A status read that actually reaches the driver — get_status caches for
    five seconds and we are testing what happens across polls."""
    svc._status_cache_time = 0
    return svc.get_status()


def test_media_low_is_set_below_the_threshold(mocker):
    svc = _service(_mock_settings(mocker, media_total=10, media_low_threshold=25))
    status = _poll(svc)
    assert status.prints_remaining == 10
    assert status.media_low is True


def test_media_low_is_clear_above_the_threshold(mocker):
    svc = _service(_mock_settings(mocker, media_total=700, media_low_threshold=25))
    assert _poll(svc).media_low is False


def test_media_low_is_never_claimed_without_a_reading(mocker):
    """A driver with no marker reporting leaves prints_remaining None, and the
    threshold must not turn that into 'low'."""
    svc = _service(_mock_settings(mocker))
    mocker.patch.object(MockPrinterDriver, "get_status", return_value=PrinterStatus(
        connected=True, ready=True, printer_name="mock", status_text="Idle",
    ))
    status = _poll(svc)
    assert status.prints_remaining is None
    assert status.media_low is False


def test_low_media_is_logged_once_per_crossing(mocker):
    """The admin panel polls every five seconds. Level-triggered, this would be
    seven hundred identical lines an hour."""
    log = mocker.patch("backend.print_service.log")
    svc = _service(_mock_settings(mocker, media_total=10, media_low_threshold=25))

    for _ in range(5):
        _poll(svc)

    warnings = [c for c in log.warn.call_args_list if c[0][1] == "printer_media_low"]
    assert len(warnings) == 1


def test_an_empty_roll_is_an_error_not_a_warning(mocker):
    log = mocker.patch("backend.print_service.log")
    svc = _service(_mock_settings(mocker, media_total=0, media_low_threshold=25))

    _poll(svc)

    assert [c for c in log.error.call_args_list if c[0][1] == "printer_media_empty"]


def test_reloading_the_roll_is_reported_and_rearms_the_warning(mocker):
    log = mocker.patch("backend.print_service.log")
    settings = _mock_settings(mocker, media_total=10, media_low_threshold=25)
    svc = _service(settings)
    _poll(svc)

    # A fresh roll goes in.
    settings.get.return_value.printer_mock = PrinterMockConfig(
        job_duration_s=0.0, media_total=700)
    assert _poll(svc).media_low is False
    assert [c for c in log.info.call_args_list if c[0][1] == "printer_media_ok"]

    # ...and runs down again. The warning must fire a second time.
    settings.get.return_value.printer_mock = PrinterMockConfig(
        job_duration_s=0.0, media_total=5)
    _poll(svc)

    warnings = [c for c in log.warn.call_args_list if c[0][1] == "printer_media_low"]
    assert len(warnings) == 2


def test_a_cancelled_print_is_not_reported_as_finished(mocker, tmp_path):
    """Clearing the queue removes the job, which looks exactly like finishing.
    Only the service knows it did the cancelling — and an operator clears the
    queue precisely when a print has gone wrong."""
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.print_file.return_value = PrintResult(success=True, job_id="DS-RX1-42")
    driver.cancel_all.return_value = True

    svc = _service_with_driver(mocker, driver)

    def await_and_get_cancelled(job_id, timeout_s):
        svc.cancel_all()                      # the operator, mid-print
        return JobOutcome(JOB_COMPLETED)      # the job has left the queue
    driver.await_job.side_effect = await_and_get_cancelled

    result = svc.print(str(f))

    assert result.success is False
    assert "cleared" in result.error


def test_an_uncancelled_print_still_reports_success(mocker, tmp_path):
    """The epoch guard must not fire on its own."""
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.print_file.return_value = PrintResult(success=True, job_id="DS-RX1-42")
    driver.await_job.return_value = JobOutcome(JOB_COMPLETED)

    assert _service_with_driver(mocker, driver).print(str(f)).success is True


def test_a_completed_print_is_counted_against_the_allowance(mocker, tmp_path):
    from backend.counters import Counters

    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.recover.return_value = None
    driver.print_file.return_value = PrintResult(success=True, job_id="DS-RX1-42")
    driver.await_job.return_value = JobOutcome(JOB_COMPLETED)

    counters = Counters(str(tmp_path / "counters.json"))
    counters.set("prints_used", 41)
    svc = _service_with_driver(mocker, driver)
    svc._counters = counters

    svc.print(str(f))

    assert counters.get("prints_used") == 42


def test_a_failed_print_is_not_counted(mocker, tmp_path):
    """The budget is prints on paper, not attempts."""
    from backend.counters import Counters

    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.recover.return_value = None
    driver.print_file.return_value = PrintResult(success=True, job_id="DS-RX1-42")
    driver.await_job.return_value = JobOutcome(JOB_FAILED, "Paper jam.")

    counters = Counters(str(tmp_path / "counters.json"))
    counters.set("prints_used", 41)
    svc = _service_with_driver(mocker, driver)
    svc._counters = counters

    svc.print(str(f))

    assert counters.get("prints_used") == 41


# ── Startup configuration checks ─────────────────────────────────────────────

LPOPTIONS_STOP = ("copies=1 device-uri=gutenprint53+usb://DNP/DS40 "
                  "printer-error-policy=stop-printer printer-is-shared=false")
LPOPTIONS_ABORT = ("copies=1 device-uri=gutenprint53+usb://DNP/DS40 "
                   "printer-error-policy=abort-job printer-is-shared=false")


def _lpoptions(mocker, stdout, returncode=0):
    mocker.patch("backend.print_service.subprocess.run",
                 return_value=subprocess.CompletedProcess(
                     args=[], returncode=returncode, stdout=stdout, stderr=""))


def test_error_policy_is_read_from_lpoptions(mocker):
    _lpoptions(mocker, LPOPTIONS_STOP)
    assert CupsPrinterDriver("DS-RX1").error_policy() == "stop-printer"


def test_stop_printer_is_reported_as_a_problem(mocker):
    """The whole reason this check exists: stop-printer disables the queue on
    the first fault and never re-enables it."""
    _lpoptions(mocker, LPOPTIONS_STOP)

    problems = CupsPrinterDriver("DS-RX1").preflight()

    assert len(problems) == 1
    assert "stop-printer" in problems[0]
    assert "lpadmin -p DS-RX1 -o printer-error-policy=abort-job" in problems[0]


def test_abort_job_passes_preflight(mocker):
    _lpoptions(mocker, LPOPTIONS_ABORT)
    assert CupsPrinterDriver("DS-RX1").preflight() == []


def test_an_unreadable_queue_is_reported(mocker):
    """A queue that cannot be interrogated is usually one that is not installed,
    which is worth saying at boot rather than at the first guest."""
    _lpoptions(mocker, "", returncode=1)

    problems = CupsPrinterDriver("DS-RX1").preflight()

    assert len(problems) == 1
    assert "Could not read" in problems[0]


def test_missing_lpoptions_does_not_crash_preflight(mocker):
    mocker.patch("backend.print_service.subprocess.run",
                 side_effect=FileNotFoundError())
    assert len(CupsPrinterDriver("DS-RX1").preflight()) == 1


def test_the_mock_has_nothing_to_preflight():
    assert MockPrinterDriver("mock").preflight() == []


def test_service_preflight_logs_each_problem(mocker):
    log = mocker.patch("backend.print_service.log")
    svc = _service_with_driver(mocker, mocker.Mock(
        preflight=mocker.Mock(return_value=["queue is wrong"])))

    assert svc.preflight() == ["queue is wrong"]
    assert [c for c in log.warn.call_args_list if c[0][1] == "printer_preflight"]


def test_service_preflight_survives_a_throwing_driver(mocker):
    """A boot must not die because a configuration check did."""
    mocker.patch("backend.print_service.log")
    svc = _service_with_driver(mocker, mocker.Mock(
        preflight=mocker.Mock(side_effect=RuntimeError("boom"))))

    assert svc.preflight() == []


# ── Recovering a latched queue ───────────────────────────────────────────────

def test_recover_does_nothing_to_a_healthy_queue(mocker):
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_stop_reason", return_value=None)
    enable = mocker.patch.object(driver, "enable_queue")

    assert driver.recover() is None
    enable.assert_not_called()


def test_recover_clears_the_backlog_before_re_enabling(mocker):
    """Order is the point. Every job in a stopped queue belongs to a guest who
    was told it failed and left; re-enabling first prints all of them at once."""
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_stop_reason", return_value="Cover Open")
    mocker.patch.object(driver, "_queued_job_ids",
                        return_value={"DS-RX1-3", "DS-RX1-4"})
    calls = []
    mocker.patch.object(driver, "cancel_all",
                        side_effect=lambda: calls.append("cancel") or True)
    mocker.patch.object(driver, "enable_queue",
                        side_effect=lambda: calls.append("enable") or True)

    note = driver.recover()

    assert calls == ["cancel", "enable"]
    assert "Cover Open" in note
    assert "2 stranded" in note


def test_recover_reports_nothing_when_re_enabling_fails(mocker):
    driver = CupsPrinterDriver("DS-RX1")
    mocker.patch.object(driver, "_stop_reason", return_value="Cover Open")
    mocker.patch.object(driver, "_queued_job_ids", return_value=set())
    mocker.patch.object(driver, "enable_queue", return_value=False)

    assert driver.recover() is None


def test_print_recovers_the_queue_before_submitting(mocker, tmp_path):
    """The cover-open night: the queue latched, and every later session was
    accepted into a dead queue. Recovery has to run before the submission."""
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")
    log = mocker.patch("backend.print_service.log")

    order = []
    driver = mocker.Mock()
    driver.recover.side_effect = lambda: order.append("recover") or "re-enabled it"
    driver.print_file.side_effect = lambda *a: order.append("submit") or PrintResult(
        success=True, job_id="DS-RX1-8")
    driver.await_job.return_value = JobOutcome(JOB_COMPLETED)

    _service_with_driver(mocker, driver).print(str(f))

    assert order == ["recover", "submit"]
    assert [c for c in log.warn.call_args_list if c[0][1] == "printer_queue_recovered"]


def test_a_failed_job_is_cancelled_so_it_cannot_print_later(mocker, tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.recover.return_value = None
    driver.print_file.return_value = PrintResult(success=True, job_id="DS-RX1-9")
    driver.await_job.return_value = JobOutcome(JOB_FAILED, "Cover Open")

    _service_with_driver(mocker, driver).print(str(f))

    driver.cancel_job.assert_called_once_with("DS-RX1-9")


def test_a_shutdown_does_not_cancel_a_print_that_may_be_running(mocker, tmp_path):
    """JOB_ABORTED means we stopped watching, not that the printer stopped."""
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.recover.return_value = None
    driver.print_file.return_value = PrintResult(success=True, job_id="DS-RX1-9")
    driver.await_job.return_value = JobOutcome(JOB_ABORTED, "Stopped waiting")

    _service_with_driver(mocker, driver).print(str(f))

    driver.cancel_job.assert_not_called()


def test_a_completed_job_is_not_cancelled(mocker, tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")

    driver = mocker.Mock()
    driver.recover.return_value = None
    driver.print_file.return_value = PrintResult(success=True, job_id="DS-RX1-9")
    driver.await_job.return_value = JobOutcome(JOB_COMPLETED)

    _service_with_driver(mocker, driver).print(str(f))

    driver.cancel_job.assert_not_called()


def test_a_throwing_recover_does_not_stop_the_print(mocker, tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")
    mocker.patch("backend.print_service.log")

    driver = mocker.Mock()
    driver.recover.side_effect = RuntimeError("cupsenable exploded")
    driver.print_file.return_value = PrintResult(success=True, job_id="DS-RX1-9")
    driver.await_job.return_value = JobOutcome(JOB_COMPLETED)

    assert _service_with_driver(mocker, driver).print(str(f)).success is True


def test_operator_recovery_reports_what_it_did(mocker):
    mocker.patch("backend.print_service.log")
    driver = mocker.Mock()
    driver.recover.return_value = "Queue was stopped (Cover Open); re-enabled it"

    assert "Cover Open" in _service_with_driver(mocker, driver).recover()


def test_operator_recovery_is_honest_about_a_healthy_queue(mocker):
    """An operator pressing a button on a working printer must not be told
    something was fixed."""
    driver = mocker.Mock()
    driver.recover.return_value = None

    assert "already running" in _service_with_driver(mocker, driver).recover()


def test_ipptool_is_asked_to_print_the_attributes(mocker):
    """Without -v, ipptool prints a PASS/FAIL summary and no attributes, so
    every marker read comes back empty and media reports as unknown forever."""
    run = mocker.patch("backend.print_service.subprocess.run",
                       return_value=subprocess.CompletedProcess(
                           args=[], returncode=0, stdout=IPPTOOL_OUTPUT, stderr=""))

    CupsPrinterDriver("DS-RX1")._ipp_attributes()

    argv = run.call_args[0][0]
    assert argv[0] == "ipptool"
    flags = argv[1]
    assert flags.startswith("-") and "v" in flags, flags

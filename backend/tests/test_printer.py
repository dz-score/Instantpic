import os
import subprocess
import pytest
from backend.print_service import PrintService, CupsPrinterDriver, MockPrinterDriver, PrintResult, PrinterStatus


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

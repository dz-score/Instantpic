import os
import subprocess
import pytest
from backend.printer import print_photo

def test_print_photo_missing_file(temp_workspace):
    """It should immediately return False if the file does not exist."""
    missing_filepath = os.path.join(temp_workspace["photos_dir"], "missing.jpg")
    
    result = print_photo(missing_filepath)
    assert result is False

def test_print_photo_success(temp_workspace, mocker):
    """It should correctly format and send the CUPS lp command, returning True on success."""
    # Force Linux mode to trigger actual CUPS logic instead of the dev mock
    mocker.patch("sys.platform", "linux")
    
    # Mock settings to use a specific printer
    mocker.patch("backend.printer.load_settings", return_value=mocker.Mock(printer_name="Canon_Selphy"))
    
    # Mock subprocess.run to simulate a successful CUPS command
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="request id is Canon_Selphy-123")
    
    # Create a dummy file to print
    filepath = os.path.join(temp_workspace["photos_dir"], "test_print.jpg")
    with open(filepath, "w") as f:
        f.write("dummy photo data")
        
    result = print_photo(filepath)
    
    assert result is True
    # Verify the correct command was sent to the shell
    mock_run.assert_called_once_with(
        ["lp", "-d", "Canon_Selphy", filepath],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True
    )

def test_print_photo_cups_failure(temp_workspace, mocker):
    """It should catch CalledProcessError and return False if CUPS fails."""
    # Force Linux mode
    mocker.patch("sys.platform", "linux")
    mocker.patch("backend.printer.load_settings", return_value=mocker.Mock(printer_name="Canon_Selphy"))
    
    # Mock subprocess.run to raise a CalledProcessError (e.g. printer offline)
    mock_run = mocker.patch("subprocess.run")
    mock_run.side_effect = subprocess.CalledProcessError(
        returncode=1, 
        cmd=["lp", "-d", "Canon_Selphy", "test_print.jpg"],
        stderr="lp: Error - The printer or class does not exist."
    )
    
    filepath = os.path.join(temp_workspace["photos_dir"], "test_print_fail.jpg")
    with open(filepath, "w") as f:
        f.write("dummy photo data")
        
    result = print_photo(filepath)
    
    assert result is False

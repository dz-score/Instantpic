import os
import subprocess
import sys
from backend.config import load_settings

def print_photo(filepath: str) -> bool:
    """
    Send a print job to the printer configured in config.json.
    Gracefully mocks execution on Windows/macOS if CUPS or lp is unavailable.
    """
    if not os.path.exists(filepath):
        print(f"Print Error: File not found {filepath}")
        return False
        
    settings = load_settings()
    printer_name = settings.printer_name
    
    # If the printer is configured as 'mock', or we are not on Linux, mock it
    if printer_name == "mock" or sys.platform == "win32":
        print(f"=== MOCK PRINT JOB ===")
        print(f"Printer Target: {printer_name}")
        print(f"Printing File: {filepath}")
        print(f"Status: Success (Development Mock)")
        print(f"======================")
        return True
        
    # Linux / Raspberry Pi execution via CUPS 'lp' command
    try:
        # Command: lp -d <printer_name> <file_path>
        cmd = ["lp", "-d", printer_name, filepath]
        print(f"Executing print command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        print(f"Print job sent successfully: {result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"CUPS lp print failed with exit code {e.returncode}:")
        print(f"Stderr: {e.stderr}")
        return False
    except Exception as e:
        print(f"Failed to invoke print subsystem: {e}")
        return False

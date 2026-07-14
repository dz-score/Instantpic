import os
import sys
import shutil
import subprocess
import glob
from backend.settings import AppSettings
from backend.print_service import PrintService

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTOS_DIR = os.path.join(BASE_DIR, "backend", "photos")

def check_printer(print_svc: PrintService):
    """Check if printer is connected/available via PrintService."""
    status = print_svc.get_status()
    return {
        "connected": status.connected,
        "status": status.status_text,
        "printer_name": status.printer_name,
        "ready": status.ready,
        "error": status.error,
    }

def check_storage(settings: AppSettings):
    """Check disk usage and photo count."""
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    total, used, free = shutil.disk_usage(PHOTOS_DIR)

    pattern = os.path.join(PHOTOS_DIR, "*.[jJ][pP][gG]")
    photo_count = len(glob.glob(pattern))

    return {
        "total_gb": round(total / (1024**3), 1),
        "used_gb": round(used / (1024**3), 1),
        "free_gb": round(free / (1024**3), 1),
        "percentage_used": round((used / total) * 100, 1),
        "photo_count": photo_count,
        "max_photos": settings.max_photos
    }

def get_diagnostics(settings: AppSettings, print_svc: PrintService):
    """Aggregate all diagnostic checks."""
    return {
        "printer": check_printer(print_svc),
        "storage": check_storage(settings)
    }

def execute_emergency(action: str):
    """Execute an emergency control action."""
    if sys.platform == "win32":
        return {
            "status": "mock",
            "detail": f"Emergency action '{action}' simulated (Windows dev mode)"
        }
    
    try:
        if action == "restart_booth":
            # Restart Chromium browser and the backend server
            subprocess.Popen(["sudo", "systemctl", "restart", "chromium-kiosk"], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.Popen(["sudo", "systemctl", "restart", "photobooth"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"status": "success", "detail": "Booth restart initiated"}
        
        elif action == "restart_camera":
            # The frontend will handle camera re-init; this is a backend signal
            return {"status": "success", "detail": "Camera restart signal sent"}
        
        elif action == "restart_printer":
            subprocess.run(["sudo", "systemctl", "restart", "cups"],
                         capture_output=True, timeout=10)
            return {"status": "success", "detail": "CUPS service restarted"}
        
        elif action == "clear_queue":
            subprocess.run(["cancel", "-a"],
                         capture_output=True, timeout=5)
            return {"status": "success", "detail": "Print queue cleared"}
        
        else:
            return {"status": "error", "detail": f"Unknown action: {action}"}
    
    except Exception as e:
        return {"status": "error", "detail": str(e)}

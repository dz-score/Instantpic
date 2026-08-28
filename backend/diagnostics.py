import os
import sys
import shutil
import subprocess
import glob
from backend import storage
from backend.settings import AppSettings
from backend.print_service import PrintService

def check_printer(print_svc: PrintService):
    """Check if printer is connected/available via PrintService.

    Everything PrinterStatus knows goes through, including which driver
    answered and what it can say about media. Re-listing the fields here meant
    a new one had to be added twice to reach the admin panel; `status` is kept
    as an alias because the UI reads it under that name.
    """
    status = print_svc.get_status()
    return {**status.to_dict(), "status": status.status_text}

def check_storage(settings: AppSettings):
    """Check disk usage and photo count."""
    # storage.PHOTOS_DIR is read at call time, not imported: this module used
    # to derive its own copy, which silently escaped the test fixture that
    # redirects photo storage.
    storage.ensure_directories()
    total, used, free = shutil.disk_usage(storage.PHOTOS_DIR)

    pattern = os.path.join(storage.PHOTOS_DIR, "*.[jJ][pP][gG]")
    photo_count = len(glob.glob(pattern))

    return {
        "total_gb": round(total / (1024**3), 1),
        "used_gb": round(used / (1024**3), 1),
        "free_gb": round(free / (1024**3), 1),
        "percentage_used": round((used / total) * 100, 1),
        "photo_count": photo_count,
        "max_photos": settings.max_photos
    }

def get_diagnostics(settings: AppSettings, print_svc: PrintService, led=None):
    """Aggregate all diagnostic checks."""
    diag = {
        "printer": check_printer(print_svc),
        "storage": check_storage(settings)
    }
    # Optional so the aggregate keeps working for callers that predate the ring.
    # The controller is always present in the running app — create_led_controller
    # returns an inert one rather than None when no ring is configured.
    if led is not None:
        diag["led"] = led.health()
    return diag

def execute_emergency(action: str, print_svc: PrintService = None):
    """Execute an emergency control action.

    `print_svc` is required for clear_queue: shelling out to `cancel` from here
    would leave an in-flight await_job reporting the cancelled job as a finished
    print (Rule 5 — the print service owns printer access).
    """
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
            # Deliberately unimplemented, and honest about it. The camera heals
            # itself (worker re-init with backoff, disconnect cascade), and a
            # forced exit+init cycle is camera surgery that must be validated
            # on the real body before it's offered as a button. This used to
            # return success while doing nothing — lying to an operator
            # mid-crisis is worse than admitting there's no lever.
            return {"status": "unsupported",
                    "detail": "Camera reconnects automatically; no manual restart is implemented"}
        
        elif action == "restart_printer":
            subprocess.run(["sudo", "systemctl", "restart", "cups"],
                         capture_output=True, timeout=10)
            return {"status": "success", "detail": "CUPS service restarted"}
        
        elif action == "clear_queue":
            if print_svc is None:
                return {"status": "error",
                        "detail": "Print service unavailable; queue not cleared"}
            ok = print_svc.cancel_all()
            return {"status": "success" if ok else "error",
                    "detail": "Print queue cleared" if ok else "Could not clear the print queue"}
        
        else:
            return {"status": "error", "detail": f"Unknown action: {action}"}
    
    except Exception as e:
        return {"status": "error", "detail": str(e)}

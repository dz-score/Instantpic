import os
import shutil
import glob
from typing import List
from backend.logger import log
from backend.settings import AppSettings

# Re-exported as module attributes so tests can monkeypatch them per-module
# (conftest.temp_workspace); main.py also imports BASE_DIR from here.
from backend.paths import BASE_DIR, PHOTOS_DIR, OVERLAYS_DIR

def ensure_directories():
    """Ensure photos and overlays directories exist."""
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    os.makedirs(OVERLAYS_DIR, exist_ok=True)

def get_all_photos() -> List[str]:
    """Get list of photos sorted by modification time (newest first)."""
    ensure_directories()
    # Find all JPEGs or PNGs in the photos folder
    pattern = os.path.join(PHOTOS_DIR, "*.[jJ][pP][gG]")
    files = glob.glob(pattern)
    # Sort files by modification time (newest first)
    files.sort(key=os.path.getmtime, reverse=True)
    # Return relative or absolute paths (just filenames)
    return [os.path.basename(f) for f in files]

def enforce_circular_storage(settings: AppSettings):
    """
    Check disk usage and total count of photos.
    Delete the oldest photos if limits are exceeded.
    """
    ensure_directories()

    # 1. Enforce photo count limit
    pattern = os.path.join(PHOTOS_DIR, "*.[jJ][pP][gG]")
    files = glob.glob(pattern)
    files.sort(key=os.path.getmtime)  # Oldest first
    
    max_photos = settings.max_photos
    if len(files) > max_photos:
        num_to_delete = len(files) - max_photos
        for i in range(num_to_delete):
            try:
                os.remove(files[i])
                # Deleting a guest's photo is a significant event (Rule 16):
                # it must be reconstructable from the log, not lost on stdout.
                log.info("storage", "storage_photo_deleted",
                         f"Circular storage: deleted oldest photo {os.path.basename(files[i])} (count limit)",
                         data={"filename": os.path.basename(files[i]),
                               "reason": "count_limit", "max_photos": max_photos})
            except Exception as e:
                log.error("storage", "storage_delete_fail",
                          f"Could not delete old photo {os.path.basename(files[i])}: {e}",
                          data={"filename": os.path.basename(files[i]),
                                "reason": "count_limit", "error": str(e)})
        # Refresh the list for the disk space check
        files = glob.glob(pattern)
        files.sort(key=os.path.getmtime)

    # 2. Enforce disk space limit (Free GB)
    min_free_gb = settings.disk_min_free_gb
    total, used, free = shutil.disk_usage(PHOTOS_DIR)
    free_gb = free / (1024**3)

    # Keep deleting oldest until we have enough free space or no files left
    while free_gb < min_free_gb and files:
        oldest_file = files.pop(0)
        try:
            os.remove(oldest_file)
            log.info("storage", "storage_photo_deleted",
                     f"Circular storage: deleted {os.path.basename(oldest_file)} "
                     f"({free_gb:.2f} GB free, threshold {min_free_gb} GB)",
                     data={"filename": os.path.basename(oldest_file),
                           "reason": "disk_space", "free_gb": round(free_gb, 2),
                           "min_free_gb": min_free_gb})
            # Recompute disk space
            total, used, free = shutil.disk_usage(PHOTOS_DIR)
            free_gb = free / (1024**3)
        except Exception as e:
            log.error("storage", "storage_delete_fail",
                      f"Could not delete {os.path.basename(oldest_file)} for space recovery: {e}",
                      data={"filename": os.path.basename(oldest_file),
                            "reason": "disk_space", "error": str(e)})
            break

import os
import shutil
import glob
from typing import List
from backend.settings import AppSettings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTOS_DIR = os.path.join(BASE_DIR, "backend", "photos")
OVERLAYS_DIR = os.path.join(BASE_DIR, "backend", "overlays")

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
                print(f"Circular Storage: Deleted oldest photo {files[i]} due to file count limit ({max_photos}).")
            except Exception as e:
                print(f"Error deleting old photo {files[i]}: {e}")
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
            print(f"Circular Storage: Deleted {oldest_file} due to low disk space ({free_gb:.2f} GB free, threshold {min_free_gb} GB).")
            # Recompute disk space
            total, used, free = shutil.disk_usage(PHOTOS_DIR)
            free_gb = free / (1024**3)
        except Exception as e:
            print(f"Error deleting file {oldest_file} for space recovery: {e}")
            break

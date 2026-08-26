import os
import shutil
import glob
import time
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

def _photo_pool() -> List[str]:
    """Every file circular storage manages, oldest first.

    Deliberately one pool: the disk does not care which of these is a raw
    capture, a screen preview or a print composite, and protecting the disk is
    what this limit is for. See `max_photos` in settings.py for what that
    means in sessions.
    """
    files = glob.glob(os.path.join(PHOTOS_DIR, "*.[jJ][pP][gG]"))
    files.sort(key=os.path.getmtime)
    return files


def _free_gb() -> float:
    return shutil.disk_usage(PHOTOS_DIR)[2] / (1024**3)


def _delete_with_derived(path: str, reason: str, data: dict) -> int:
    """Delete a photo plus anything derived from it. Returns how many files went.

    `generate_previews()` writes `preview_<source>` beside its source. Removing
    one without the other leaves an orphan that still occupies the pool and
    still counts against max_photos, so they go together.
    """
    name = os.path.basename(path)
    try:
        os.remove(path)
    except Exception as e:
        # Deleting a guest's photo is a significant event (Rule 16): it must be
        # reconstructable from the log, not lost on stdout.
        log.error("storage", "storage_delete_fail",
                  f"Could not delete {name}: {e}",
                  data={**data, "filename": name, "reason": reason, "error": str(e)})
        return 0

    removed = 1
    derived = os.path.join(os.path.dirname(path), f"preview_{name}")
    if os.path.exists(derived):
        try:
            os.remove(derived)
            removed += 1
        except Exception as e:
            log.warn("storage", "storage_derived_orphan",
                     f"Deleted {name} but its preview survived: {e}",
                     data={"filename": name, "derived": os.path.basename(derived),
                           "error": str(e)})

    log.info("storage", "storage_photo_deleted",
             f"Circular storage: deleted {name} ({reason})",
             data={**data, "filename": name, "reason": reason, "files_removed": removed})
    return removed


def enforce_circular_storage(settings: AppSettings):
    """Delete the oldest photos until the count and free-space limits are met.

    Never touches a file younger than `storage_protect_recent_s`. Cleanup runs
    after every processing job and deletes strictly oldest-first, but it cannot
    ask what is on screen — storage must not depend on the workflow (Rule 18) —
    so age stands in for "still in use". Without that floor a near-full disk
    would happily delete the raws and composite of the session the guest is
    looking at right now: the FSM state still holds those filenames, so REVEAL
    and PICK_FAVORITE would render broken images and the QR download would 404.
    The window also covers a guest who has walked off with a QR code they have
    not scanned yet.

    Refusing to delete is the safe failure here, so when the limits cannot be
    met without touching protected files it gives up and says so loudly rather
    than taking the session down with it.
    """
    ensure_directories()

    now = time.time()
    protect_s = settings.storage_protect_recent_s
    pool = _photo_pool()

    evictable = []
    for f in pool:
        try:
            if now - os.path.getmtime(f) >= protect_s:
                evictable.append(f)
        except OSError:
            # Vanished under us (another cleanup, or a manual delete) — nothing
            # to evict and nothing to report.
            continue
    protected = len(pool) - len(evictable)

    # 1. Enforce photo count limit
    max_photos = settings.max_photos
    remaining = len(pool)
    survivors = []
    for path in evictable:
        if remaining <= max_photos:
            survivors.append(path)
            continue
        gone = _delete_with_derived(path, "count_limit", {"max_photos": max_photos})
        if gone:
            remaining -= gone
        else:
            # A failed delete must not abort the sweep — the next oldest may
            # well succeed, and giving up here is how a full disk stays full.
            survivors.append(path)
    evictable = survivors

    if remaining > max_photos:
        log.warn("storage", "storage_over_count",
                 f"{remaining} photos exceeds max_photos={max_photos}, but the rest are "
                 f"too recent to delete ({protected} protected)",
                 data={"remaining": remaining, "max_photos": max_photos,
                       "protected": protected, "protect_window_s": protect_s})

    # 2. Enforce disk space limit (Free GB)
    min_free_gb = settings.disk_min_free_gb
    free_gb = _free_gb()
    for path in evictable:
        if free_gb >= min_free_gb:
            break
        if _delete_with_derived(path, "disk_space",
                                {"free_gb": round(free_gb, 2), "min_free_gb": min_free_gb}):
            free_gb = _free_gb()

    if free_gb < min_free_gb:
        # The booth keeps running — captures will fail on write when the disk
        # actually fills — but this is the operator's cue to intervene.
        log.error("storage", "storage_space_low",
                  f"Only {free_gb:.2f} GB free (want {min_free_gb} GB) and nothing older "
                  f"than {protect_s}s left to delete — {protected} recent file(s) protected",
                  data={"free_gb": round(free_gb, 2), "min_free_gb": min_free_gb,
                        "protected": protected, "protect_window_s": protect_s})

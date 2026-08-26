import os
import time
from backend.storage import enforce_circular_storage, get_all_photos

def test_circular_storage_max_photos(temp_workspace, temp_config, mocker):
    """If we exceed max_photos, it should delete the oldest ones."""
    # Mock settings to have a very low limit
    settings = mocker.Mock(max_photos=3, disk_min_free_gb=0.1, storage_protect_recent_s=0)
    
    # Mock disk_usage to return plenty of space so space check doesn't trigger
    mocker.patch("shutil.disk_usage", return_value=(100*(1024**3), 50*(1024**3), 50*(1024**3)))
    
    photos_dir = temp_workspace["photos_dir"]
    
    # Create 5 fake photos, waiting a tiny bit so modification times differ
    for i in range(5):
        filepath = os.path.join(photos_dir, f"photo_{i}.jpg")
        with open(filepath, "w") as f:
            f.write("fake image data")
        time.sleep(0.01)
        
    photos_before = get_all_photos()
    assert len(photos_before) == 5
    
    # Enforce
    enforce_circular_storage(settings)
    
    # Should only have 3 left
    photos_after = get_all_photos()
    assert len(photos_after) == 3
    
    # Because get_all_photos returns newest first, the ones left should be photo_4, photo_3, photo_2
    assert "photo_4.jpg" in photos_after
    assert "photo_0.jpg" not in photos_after
    assert "photo_1.jpg" not in photos_after

def test_circular_storage_disk_space(temp_workspace, temp_config, mocker):
    """If free disk space is below threshold, it should delete the oldest files to recover space."""
    # High photo limit so it doesn't trigger
    settings = mocker.Mock(max_photos=100, disk_min_free_gb=1.0, storage_protect_recent_s=0)
    
    photos_dir = temp_workspace["photos_dir"]
    
    # Create 3 fake photos
    for i in range(3):
        filepath = os.path.join(photos_dir, f"photo_{i}.jpg")
        with open(filepath, "w") as f:
            f.write("fake image data")
        time.sleep(0.01)
        
    assert len(get_all_photos()) == 3
    
    # We will mock shutil.disk_usage to simulate a disk freeing up space as files are deleted.
    # Initial call: 0.5GB free (below 1.0GB threshold) -> deletes photo_0
    # Second call: 0.8GB free (below 1.0GB threshold) -> deletes photo_1
    # Third call: 1.2GB free (above threshold) -> stops deleting
    
    def mock_disk_usage(path):
        # Count how many files exist right now to determine free space
        count = len(os.listdir(photos_dir))
        if count == 3:
            free = 0.5 * (1024**3)
        elif count == 2:
            free = 0.8 * (1024**3)
        else:
            free = 1.2 * (1024**3)
        return (10 * (1024**3), 9 * (1024**3), free)
        
    mocker.patch("shutil.disk_usage", side_effect=mock_disk_usage)
    
    enforce_circular_storage(settings)
    
    # Only photo_2 should remain (count == 1)
    photos_after = get_all_photos()
    assert len(photos_after) == 1
    assert "photo_2.jpg" in photos_after


# --- The age floor: cleanup must not eat the session the guest is looking at ---

def _write(photos_dir, name, age_s=0):
    """Create a photo, optionally backdated so the age floor lets it go."""
    path = os.path.join(photos_dir, name)
    with open(path, "w") as f:
        f.write("fake image data")
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
    return path


def test_recent_photos_are_never_deleted(temp_workspace, temp_config, mocker):
    """Circular storage runs after every processing job and deletes oldest-first.
    It cannot ask what is on screen, so anything inside the protection window is
    off limits -- otherwise a near-full disk deletes the raws and composite the
    FSM is still pointing REVEAL at, and the guest gets broken images."""
    # max_photos=1 against 4 files, so the limit REQUIRES eating into the live
    # session — without the floor the sweep would take live_1 too.
    settings = mocker.Mock(max_photos=1, disk_min_free_gb=0.1, storage_protect_recent_s=900)
    mocker.patch("shutil.disk_usage", return_value=(100*(1024**3), 50*(1024**3), 50*(1024**3)))
    mock_warn = mocker.patch("backend.storage.log.warn")
    photos_dir = temp_workspace["photos_dir"]

    _write(photos_dir, "old_1.jpg", age_s=3600)
    _write(photos_dir, "old_2.jpg", age_s=3000)
    _write(photos_dir, "live_1.jpg")          # the session in progress
    _write(photos_dir, "live_2.jpg")

    enforce_circular_storage(settings)

    left = get_all_photos()
    assert "live_1.jpg" in left and "live_2.jpg" in left, "deleted a photo still in use"
    assert "old_1.jpg" not in left and "old_2.jpg" not in left   # evictable ones did go

    # Staying over the limit is the acceptable outcome — refusing to delete is
    # the safe failure — but it must not be a quiet one.
    assert mock_warn.call_args[0][1] == "storage_over_count"


def test_cannot_free_space_is_reported(temp_workspace, temp_config, mocker):
    """If everything left is protected, the sweep gives up loudly — the operator
    needs to know the disk is filling and cleanup can't help."""
    settings = mocker.Mock(max_photos=100, disk_min_free_gb=5.0, storage_protect_recent_s=900)
    mocker.patch("shutil.disk_usage", return_value=(10*(1024**3), 9*(1024**3), 1*(1024**3)))
    photos_dir = temp_workspace["photos_dir"]
    _write(photos_dir, "live.jpg")

    mock_error = mocker.patch("backend.storage.log.error")
    enforce_circular_storage(settings)

    assert "live.jpg" in get_all_photos()
    assert mock_error.call_args[0][1] == "storage_space_low"


def test_preview_is_deleted_with_its_source(temp_workspace, temp_config, mocker):
    """previews are derived from a capture. Deleting one without the other
    leaves an orphan that still occupies the pool and still counts."""
    # max_photos=2 against 3 files means exactly ONE eviction is required. If the
    # preview did not go with its source it would survive as an orphan, so this
    # is what separates "deleted together" from "deleted because the limit
    # happened to reach it anyway".
    settings = mocker.Mock(max_photos=2, disk_min_free_gb=0.1, storage_protect_recent_s=0)
    mocker.patch("shutil.disk_usage", return_value=(100*(1024**3), 50*(1024**3), 50*(1024**3)))
    photos_dir = temp_workspace["photos_dir"]

    _write(photos_dir, "capture_a.jpg", age_s=3600)
    _write(photos_dir, "preview_capture_a.jpg", age_s=3500)
    _write(photos_dir, "photo_keep.jpg")

    enforce_circular_storage(settings)

    left = get_all_photos()
    assert "capture_a.jpg" not in left
    assert "preview_capture_a.jpg" not in left, "orphaned preview survived its source"
    assert "photo_keep.jpg" in left


def test_a_failed_delete_does_not_abort_the_sweep(temp_workspace, temp_config, mocker):
    """The disk-space loop used to `break` on the first failure, so one locked
    file (Windows, or one being served over /download/) meant no space was ever
    recovered. It must move on to the next oldest instead."""
    settings = mocker.Mock(max_photos=100, disk_min_free_gb=1.0, storage_protect_recent_s=0)
    photos_dir = temp_workspace["photos_dir"]

    locked = _write(photos_dir, "a_locked.jpg", age_s=3600)
    _write(photos_dir, "b_deletable.jpg", age_s=3500)

    real_remove = os.remove
    def remove(path, *a, **k):
        if os.path.basename(path) == "a_locked.jpg":
            raise PermissionError("file is in use")
        return real_remove(path, *a, **k)
    mocker.patch("backend.storage.os.remove", side_effect=remove)

    def disk_usage(_path):
        free = 1.5 if not os.path.exists(os.path.join(photos_dir, "b_deletable.jpg")) else 0.5
        return (10*(1024**3), 9*(1024**3), free*(1024**3))
    mocker.patch("shutil.disk_usage", side_effect=disk_usage)

    enforce_circular_storage(settings)

    left = get_all_photos()
    assert "a_locked.jpg" in left            # could not be removed
    assert "b_deletable.jpg" not in left     # but the sweep carried on and freed space

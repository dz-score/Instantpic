import os
import time
from backend.storage import enforce_circular_storage, get_all_photos

def test_circular_storage_max_photos(temp_workspace, temp_config, mocker):
    """If we exceed max_photos, it should delete the oldest ones."""
    # Mock settings to have a very low limit
    mocker.patch("backend.storage.get_settings", return_value=mocker.Mock(max_photos=3, disk_min_free_gb=0.1))
    
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
    enforce_circular_storage()
    
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
    mocker.patch("backend.storage.get_settings", return_value=mocker.Mock(max_photos=100, disk_min_free_gb=1.0))
    
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
    
    enforce_circular_storage()
    
    # Only photo_2 should remain (count == 1)
    photos_after = get_all_photos()
    assert len(photos_after) == 1
    assert "photo_2.jpg" in photos_after

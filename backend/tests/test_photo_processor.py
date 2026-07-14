import os
from PIL import Image
from backend.config import AppSettings
from backend.photo_processor import process_photo_layout

# The overlay catalogue is passed in now rather than read from a global.
OVERLAYS = AppSettings().overlays

def test_process_photo_layout_single(temp_workspace, temp_config, mock_base64_image):
    """It should correctly process a single image layout and save a jpeg."""
    filename = process_photo_layout(
        images_base64=[mock_base64_image],
        layout_type="single",
        text="Test Wedding 2026",
        overlay_id="none",
        overlays=OVERLAYS,
    )
    
    assert filename.endswith(".jpg")
    
    filepath = os.path.join(temp_workspace["photos_dir"], filename)
    assert os.path.exists(filepath)
    
    # Verify it created an 1800x1200 image
    img = Image.open(filepath)
    assert img.size == (1800, 1200)
    assert img.format == "JPEG"

def test_process_photo_layout_collage(temp_workspace, temp_config, mock_base64_image):
    """It should correctly stitch 3 images into a collage layout."""
    # Pass 3 identical images
    images = [mock_base64_image, mock_base64_image, mock_base64_image]
    
    filename = process_photo_layout(
        images_base64=images,
        layout_type="collage",
        text="Test Collage",
        overlay_id="none",
        overlays=OVERLAYS,
    )
    
    filepath = os.path.join(temp_workspace["photos_dir"], filename)
    assert os.path.exists(filepath)
    
    img = Image.open(filepath)
    assert img.size == (1800, 1200)

def test_process_photo_layout_overlay_resilience(temp_workspace, temp_config, mock_base64_image):
    """If an invalid overlay ID is provided, it should gracefully ignore it and still produce a photo."""
    filename = process_photo_layout(
        images_base64=[mock_base64_image],
        layout_type="single",
        text="",
        overlay_id="this_overlay_does_not_exist",
        overlays=OVERLAYS,
    )
    
    filepath = os.path.join(temp_workspace["photos_dir"], filename)
    assert os.path.exists(filepath)

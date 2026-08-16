import os
from PIL import Image
from backend.settings import AppSettings
from backend.photo_processor import PREVIEW_MAX_EDGE, generate_previews, process_photo_layout

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


def test_generate_previews_downscales_and_names(temp_workspace):
    """Previews are screen-sized copies of the raws, named off the source."""
    photos_dir = temp_workspace["photos_dir"]
    # Stand in for a capture straight off the camera: far larger than the panel.
    raw = Image.new("RGB", (6000, 4000), (120, 90, 60))
    raw.save(os.path.join(photos_dir, "capture_abc.jpg"), "JPEG")

    previews = generate_previews(["capture_abc.jpg"])

    assert previews == ["preview_capture_abc.jpg"]
    out = Image.open(os.path.join(photos_dir, previews[0]))
    assert max(out.size) == PREVIEW_MAX_EDGE
    # Aspect ratio preserved — these are shown inside a frame that hugs them.
    assert abs((out.size[0] / out.size[1]) - 1.5) < 0.01


def test_generate_previews_skips_unreadable_sources(temp_workspace):
    """A raw that cannot be converted is skipped, not fatal: the screens fall
    back to the raw itself, which is slow to paint but correct."""
    photos_dir = temp_workspace["photos_dir"]
    raw = Image.new("RGB", (800, 600), (10, 10, 10))
    raw.save(os.path.join(photos_dir, "capture_ok.jpg"), "JPEG")

    previews = generate_previews(["missing.jpg", "capture_ok.jpg"])

    assert previews == ["preview_capture_ok.jpg"]


def test_generate_previews_does_not_upscale(temp_workspace):
    """A capture already smaller than the cap is copied at its own size."""
    photos_dir = temp_workspace["photos_dir"]
    Image.new("RGB", (640, 480), (200, 200, 200)).save(
        os.path.join(photos_dir, "capture_small.jpg"), "JPEG"
    )

    previews = generate_previews(["capture_small.jpg"])

    out = Image.open(os.path.join(photos_dir, previews[0]))
    assert out.size == (640, 480)

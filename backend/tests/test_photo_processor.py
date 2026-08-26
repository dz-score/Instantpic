import os
import pytest
from PIL import Image
from backend import paths
from backend.settings import AppSettings, OverlayConfig
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


# --- Overlays: a frame the guest picked either gets printed or gets logged ---

def test_every_default_overlay_names_a_file_that_ships():
    """The guard that was missing. The defaults named blush_floral.png and
    gold_glitter.png; neither was ever committed, and the real artwork is
    frame_floral.png / frame_gold_elegant.png. It went unnoticed for as long as
    photo_processor quietly drew a substitute for anything missing, so a booth
    on defaults printed placeholders over the designed frames."""
    for overlay in AppSettings().overlays:
        if not overlay.filename:
            continue    # "none" is the deliberate no-frame entry
        shipped = os.path.join(paths.OVERLAYS_DIR, overlay.filename)
        assert os.path.exists(shipped), (
            f"default overlay '{overlay.id}' names {overlay.filename}, "
            f"which is not in {paths.OVERLAYS_DIR}"
        )


def test_missing_overlay_is_reported_not_invented(temp_workspace, temp_config,
                                                  mock_base64_image, mocker):
    """A missing asset must degrade to an unframed photo AND say so. It must not
    fabricate a frame: a drawn stand-in looks deliberate, so nobody ever finds
    out the real one is missing."""
    mock_error = mocker.patch("backend.photo_processor.log.error")
    overlays = [OverlayConfig(id="ghost", name="Ghost", filename="not_shipped.png")]

    filename = process_photo_layout(
        images_base64=[mock_base64_image], layout_type="single",
        text="", overlay_id="ghost", overlays=overlays,
    )

    # The guest still gets their photo.
    assert os.path.exists(os.path.join(temp_workspace["photos_dir"], filename))
    # It was reported...
    assert mock_error.call_args[0][1] == "overlay_missing"
    # ...and nothing was conjured into the overlays dir to cover it up.
    assert os.listdir(temp_workspace["overlays_dir"]) == []


def test_a_real_overlay_is_actually_applied(temp_workspace, temp_config, mock_base64_image):
    """Counterpart to the above: when the file is there it must reach the canvas,
    so 'no frame' and 'frame applied' are distinguishable outcomes."""
    overlays_dir = temp_workspace["overlays_dir"]
    # Fully opaque red covers the whole canvas, so its presence is unambiguous.
    Image.new("RGBA", (1800, 1200), (255, 0, 0, 255)).save(
        os.path.join(overlays_dir, "solid.png"), "PNG")
    overlays = [OverlayConfig(id="solid", name="Solid", filename="solid.png")]

    filename = process_photo_layout(
        images_base64=[mock_base64_image], layout_type="single",
        text="", overlay_id="solid", overlays=overlays,
    )

    out = Image.open(os.path.join(temp_workspace["photos_dir"], filename)).convert("RGB")
    assert out.getpixel((900, 600)) == (254, 0, 0) or out.getpixel((900, 600))[0] > 200,         "the overlay never reached the canvas"


# --- A layout that cannot be built must not masquerade as success ---

@pytest.mark.parametrize("layout,count", [("collage", 1), ("collage", 2), ("single", 0)])
def test_unsatisfiable_layout_raises(temp_workspace, temp_config, mock_base64_image,
                                     layout, count):
    """Falling through saved a blank cream canvas with the couple's names on it
    and returned the filename as though it had worked — the guest collects an
    empty keepsake and nothing in the log explains it."""
    with pytest.raises(ValueError):
        process_photo_layout(
            images_base64=[mock_base64_image] * count, layout_type=layout,
            text="Names", overlay_id="none", overlays=OVERLAYS,
        )

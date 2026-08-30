import os
import pytest
from PIL import Image, ImageDraw
from backend import paths
from backend.settings import AppSettings, OverlayConfig
from backend.photo_processor import (
    CANVAS_H, CANVAS_W, CAPTION_MAX_SIZE, CAPTION_MIN_SIZE,
    COLLAGE_CAPTION_POS, COLLAGE_CELL, COLLAGE_CELLS,
    PREVIEW_MAX_EDGE, PRINT_DPI, _fit_font,
    decode_base64_image, generate_alignment_card, generate_previews,
    process_photo_layout,
)

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
    
    # Verify it created a canvas-sized image
    img = Image.open(filepath)
    assert img.size == (CANVAS_W, CANVAS_H)
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
    assert img.size == (CANVAS_W, CANVAS_H)

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


# --- Print geometry: portrait 4x6, matching the panel and the rotated camera ---

def _solid_base64(rgb):
    """A distinctly-coloured capture, so we can tell which cell it landed in."""
    import base64
    from io import BytesIO
    buf = BytesIO()
    Image.new("RGB", (400, 600), rgb).save(buf, format="JPEG", quality=95)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def test_canvas_is_portrait():
    """The print follows the panel and the camera. A silent flip back to
    landscape would put every shot through a 90-degree crop."""
    assert CANVAS_H > CANVAS_W
    assert (CANVAS_W, CANVAS_H) == (1200, 1800)


def test_collage_fills_three_cells_and_leaves_the_fourth_for_the_caption(
        temp_workspace, temp_config):
    """Three shots on a 2x2 grid. The fourth cell carries the names, so it must
    stay background — a photo there would print over the caption."""
    reds = [(220, 40, 40), (40, 200, 40), (40, 40, 220)]
    filename = process_photo_layout(
        images_base64=[_solid_base64(c) for c in reds],
        layout_type="collage",
        text="",
        overlay_id="none",
        overlays=OVERLAYS,
    )
    img = Image.open(os.path.join(temp_workspace["photos_dir"], filename)).convert("RGB")

    # Each of the first three cells shows its own photo, in order.
    for expected, (cx, cy) in zip(reds, COLLAGE_CELLS[:3]):
        centre = (cx + COLLAGE_CELL[0] // 2, cy + COLLAGE_CELL[1] // 2)
        got = img.getpixel(centre)
        assert max(abs(a - b) for a, b in zip(got, expected)) < 40, (
            f"cell at {(cx, cy)} should hold {expected}, got {got}"
        )

    # The caption cell is untouched cream, not a fourth photo. Compared loosely
    # because the canvas is saved as JPEG, which shifts flat colour by a point.
    cx, cy = COLLAGE_CAPTION_POS
    centre = (cx + COLLAGE_CELL[0] // 2, cy + COLLAGE_CELL[1] // 2)
    got = img.getpixel(centre)
    assert max(abs(a - b) for a, b in zip(got, (253, 251, 247))) < 5, got


def test_a_long_caption_is_shrunk_rather_than_overrunning_its_box():
    """couple_names is operator-editable free text and the collage's caption
    cell is only ~519px wide, so a fixed size would run off the print. The
    shipped default is the realistic worst case."""
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H))
    draw = ImageDraw.Draw(canvas)
    default_caption = "Michael & Sarah · June 14, 2030"

    box = COLLAGE_CELL[0] - 48
    font = _fit_font(draw, default_caption, box, CAPTION_MAX_SIZE)
    bbox = draw.textbbox((0, 0), default_caption, font=font)

    assert (bbox[2] - bbox[0]) <= box
    assert font.size < CAPTION_MAX_SIZE, "should have shrunk to fit the cell"
    # A short caption still gets the full size — the fit only shrinks on demand.
    assert _fit_font(draw, "M & S", box, CAPTION_MAX_SIZE).size == CAPTION_MAX_SIZE


def test_an_unreasonably_long_caption_stops_at_the_legibility_floor():
    """Past CAPTION_MIN_SIZE the names are unreadable anyway, so _fit_font stops
    shrinking and lets the text clip. Documented here so the overrun is a known
    limit rather than a surprise on the print."""
    draw = ImageDraw.Draw(Image.new("RGB", (CANVAS_W, CANVAS_H)))
    absurd = "Bartholomew Fitzwilliam & Anastasia Konstantinova - June 14, 2030"

    font = _fit_font(draw, absurd, COLLAGE_CELL[0] - 48, CAPTION_MAX_SIZE)

    assert font.size == CAPTION_MIN_SIZE


# --- Orientation: the camera is mounted rotated, so every capture is tagged ---

def _sideways_capture(path):
    """A landscape-stored frame tagged Orientation=6, as a camera on its side
    writes. Displayed correctly it is portrait, with the stored top-left corner
    ending up top-RIGHT. The red block lets a correct rotation be told apart
    from a flip or a rotation the wrong way."""
    img = Image.new("RGB", (200, 100), (30, 30, 30))
    for x in range(60):
        for y in range(30):
            img.putpixel((x, y), (255, 0, 0))
    exif = img.getexif()
    exif[274] = 6
    img.save(path, "JPEG", quality=95, exif=exif)


def test_captures_are_uprighted_before_compositing(temp_workspace):
    """The booth's camera is mounted rotated 90°, so its captures are stored
    sideways with an Orientation tag. PIL ignores that tag; browsers honour it.
    Unless we apply it here, the guest sees an upright photo on the reveal
    screen and collects a sideways print — a split that is invisible on screen,
    which is exactly why it needs a test."""
    photos_dir = temp_workspace["photos_dir"]
    _sideways_capture(os.path.join(photos_dir, "capture_tilted.jpg"))

    img = decode_base64_image("capture_tilted.jpg")

    assert img.size == (100, 200), "stored landscape, should load as portrait"
    w, _ = img.size
    assert img.getpixel((w - 5, 5))[0] > 200, "rotated 90° CW: mark belongs top-right"
    assert img.getpixel((5, 5))[0] < 100, "top-left should be the dark background"


def test_previews_are_uprighted_before_the_exif_is_dropped(temp_workspace):
    """Previews are re-saved through PIL, which drops the EXIF block. Uprighting
    has to happen before that save: a preview written from sideways pixels
    would lose the only thing telling the browser to rotate it, and REVEAL and
    PICK_FAVORITE show the preview, not the raw."""
    photos_dir = temp_workspace["photos_dir"]
    _sideways_capture(os.path.join(photos_dir, "capture_tilted.jpg"))

    previews = generate_previews(["capture_tilted.jpg"])

    out = Image.open(os.path.join(photos_dir, previews[0]))
    assert out.size == (100, 200)
    assert not out.getexif().get(274), "pixels are upright; a stale tag would re-rotate"


def test_untagged_captures_are_left_alone(temp_workspace):
    """The mock camera and the fixtures write no Orientation tag. Uprighting is
    applied unconditionally, so it has to be a no-op for them."""
    photos_dir = temp_workspace["photos_dir"]
    Image.new("RGB", (200, 100), (10, 10, 10)).save(
        os.path.join(photos_dir, "capture_plain.jpg"), "JPEG"
    )

    assert decode_base64_image("capture_plain.jpg").size == (200, 100)


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


def test_composite_carries_a_300_dpi_tag(temp_workspace, temp_config, mock_base64_image):
    """An untagged bitmap leaves CUPS free to pick its own scale, which is how a
    6x4 canvas ends up letterboxed or squeezed on a dye-sub. Tagged at 300,
    1800x1200 IS 6x4 inches."""
    filename = process_photo_layout(
        images_base64=[mock_base64_image],
        layout_type="single",
        text="",
        overlay_id="none",
        overlays=OVERLAYS,
    )
    img = Image.open(os.path.join(temp_workspace["photos_dir"], filename))
    assert img.size == (CANVAS_W, CANVAS_H)
    assert img.info.get("dpi") == (PRINT_DPI, PRINT_DPI)


def test_alignment_card_is_a_tagged_print_page(temp_workspace, temp_config):
    """The card only means anything if it is exactly the geometry it claims to
    measure."""
    filename = generate_alignment_card("DS-RX1", "media=w288h432 scaling=100")

    # photo_ so circular storage sweeps it up like anything else; printtest so
    # an operator can tell test cards from guests' photos in the folder.
    assert filename.startswith("photo_")
    assert "printtest" in filename

    img = Image.open(os.path.join(temp_workspace["photos_dir"], filename))
    assert img.size == (CANVAS_W, CANVAS_H)
    assert img.info.get("dpi") == (PRINT_DPI, PRINT_DPI)

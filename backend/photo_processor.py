import os
import base64
import uuid
from datetime import datetime
from typing import List
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageOps
from backend.logger import log
from backend.settings import OverlayConfig

# Re-exported as module attributes so tests can monkeypatch them per-module
# (conftest.temp_workspace).
from backend.paths import PHOTOS_DIR, OVERLAYS_DIR
# Playfair Display is bundled with the app (committed alongside this module).
# The booth must run fully offline (no venue internet / captive portals), so
# the font is NEVER fetched at runtime — a missing file falls back to PIL's
# built-in default rather than blocking the job worker on a network call.
from backend.paths import FONT_PATH

# The fixed output geometry. Changing either is a Docs/CONSTRAINTS.md §6 change.
# Portrait 4x6, matching the panel and the rotated camera.
CANVAS_W, CANVAS_H = 1200, 1800
PRINT_DPI = 300

# ── Layout geometry ──
# Derived rather than written out as magic numbers, because the caption has to
# land in whatever space the photos leave and the two drifted apart last time.

# Single: one shot at the camera's native 2:3, so nothing is cropped away, with
# a caption band beneath it.
SINGLE_BOX = (960, 1440)
SINGLE_POS = (120, 80)
SINGLE_CAPTION_TOP = SINGLE_POS[1] + SINGLE_BOX[1]
SINGLE_CAPTION_BOX = (CANVAS_W, CANVAS_H - SINGLE_CAPTION_TOP)

# Collage: three 3:4 shots on a 2x2 grid, the free fourth cell carrying the
# caption. A single row of three would cap each shot at 340px wide on a 1200px
# canvas and leave the bottom two thirds of the print empty; the grid fills the
# page and crops less (3:4 against a 2:3 source is a mild trim).
COLLAGE_GAP = 54
COLLAGE_CELL = (519, 692)
_grid_h = 2 * COLLAGE_CELL[1] + COLLAGE_GAP
COLLAGE_ORIGIN = (COLLAGE_GAP, (CANVAS_H - _grid_h) // 2)
COLLAGE_CELLS = [
    (COLLAGE_ORIGIN[0] + col * (COLLAGE_CELL[0] + COLLAGE_GAP),
     COLLAGE_ORIGIN[1] + row * (COLLAGE_CELL[1] + COLLAGE_GAP))
    for row in (0, 1) for col in (0, 1)
]
# The fourth cell holds the caption instead of a photo.
COLLAGE_CAPTION_POS = COLLAGE_CELLS[3]
COLLAGE_CAPTION_BOX = COLLAGE_CELL

def get_font(size: int):
    """Load the bundled Playfair Display font, or fall back to PIL's default.

    Never touches the network: the font ships with the app so photo
    processing works with no external connection."""
    if os.path.exists(FONT_PATH):
        try:
            return ImageFont.truetype(FONT_PATH, size)
        except Exception:
            pass
    return ImageFont.load_default()

def _upright(img: Image.Image) -> Image.Image:
    """Apply the EXIF Orientation tag, so downstream code sees the picture the
    way the photographer framed it.

    The booth's camera is mounted rotated 90°, so every capture off it carries
    an Orientation tag and its pixels are stored on their side. PIL does not
    honour that tag on its own — `Image.open` hands back the raw sensor
    orientation — but browsers DO honour it when rendering an <img>. Left
    unapplied, the two disagree: the guest sees an upright photo on the reveal
    screen and collects a sideways print. Normalising here, at the one place
    every layout path loads its input, keeps the print and the screen agreeing.

    A no-op for images with no tag (the mock camera, the test fixtures), which
    is why it is safe to apply unconditionally.
    """
    return ImageOps.exif_transpose(img)


# Caption sizing. The collage's caption cell is only ~519px wide, and
# couple_names is operator-editable free text, so a fixed size would overrun the
# box — off the side of the print for a long pair of names.
CAPTION_MAX_SIZE = 52
CAPTION_MIN_SIZE = 22
CAPTION_PADDING = 24


def _fit_font(draw: "ImageDraw.ImageDraw", text: str, max_width: int, max_size: int):
    """Largest bundled-font size at which `text` fits `max_width`, floored at
    CAPTION_MIN_SIZE — past that the names are unreadable anyway and clipping is
    the more honest failure."""
    for size in range(max_size, CAPTION_MIN_SIZE - 1, -2):
        font = get_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            return font
    return get_font(CAPTION_MIN_SIZE)


def decode_base64_image(base64_str: str) -> Image.Image:
    """Decode a base64 data URI to a PIL Image, or load directly from disk if it's a filename."""
    if base64_str.startswith("data:image"):
        if "," in base64_str:
            base64_str = base64_str.split(",")[1]
        image_data = base64.b64decode(base64_str)
        return _upright(Image.open(BytesIO(image_data)))
    else:
        # A local filename produced by the gphoto2 capture. Reads this module's
        # PHOTOS_DIR, same as every other function here — it used to re-import
        # the name from backend.storage at call time, so one module answered to
        # two authorities. Harmless while both point at backend.paths and the
        # fixture patches both, which is exactly why it would have gone unnoticed
        # until one of them moved (see diagnostics.py, which did).
        filepath = os.path.join(PHOTOS_DIR, base64_str)
        return _upright(Image.open(filepath))

# Longest edge of a screen preview, in pixels. The booth panel is 1080x1920 and
# the reveal photo is capped at 62vh, so 1400 is already more than it can show.
PREVIEW_MAX_EDGE = 1400


def generate_previews(raw_filenames: List[str]) -> List[str]:
    """Write a screen-sized copy of each raw capture; return their filenames.

    REVEAL and PICK_FAVORITE show the guest their actual shots rather than the
    print composite, because the composite bakes in the matte and the names/date
    caption. But a raw off the M50 is 24MP / ~7MB, and the Pi's browser needs
    1-2s to decode one — long enough that the gold frame paints empty and white
    before the picture lands. These previews are the same picture at screen size
    and carry no matte or caption either. The print and the download still use
    the full-resolution composite.

    `draft()` is what makes this cheap enough to run inside the job the guest is
    already waiting on: on a JPEG it lets libjpeg downscale in the DCT domain
    while decoding (1/2, 1/4, 1/8), so the full 24MP is never expanded into
    memory at all.

    Best-effort by design — a capture that fails to convert is skipped rather
    than failing the whole job, and callers fall back to showing the raw.
    """
    previews: List[str] = []
    for raw in raw_filenames:
        try:
            source = os.path.join(PHOTOS_DIR, raw)
            with Image.open(source) as img:
                img.draft("RGB", (PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE))
                # Upright before the re-save, not after: saving through PIL
                # drops the EXIF block, so a preview written from sideways
                # pixels would lose the Orientation tag that was the only thing
                # telling the browser to rotate it. The raw keeps its tag and
                # renders fine; the preview would not, and the preview is what
                # REVEAL and PICK_FAVORITE actually show. `draft` only
                # configures the decoder, so it still applies to the load that
                # the transpose triggers here.
                preview = _upright(img).convert("RGB")
            preview.thumbnail((PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE), Image.LANCZOS)
            name = f"preview_{raw}"
            preview.save(os.path.join(PHOTOS_DIR, name), "JPEG", quality=82, optimize=True)
            previews.append(name)
        except Exception as e:
            # Not fatal: the screens fall back to the raw, which is slow to
            # paint but correct.
            log.warn("photo_processor", "preview_failed",
                     f"Could not build a preview for {raw}: {e}")
    return previews


def process_photo_layout(images_base64: list, layout_type: str, text: str, overlay_id: str,
                         overlays: List[OverlayConfig]) -> str:
    """
    Process single or multi-photo layouts into the fixed print canvas,
    overlay selected template and draw custom branding text.
    Returns the filename of the saved image.

    `overlays` is the configured catalogue, passed in rather than read from a global
    so this stays a pure function of its arguments.
    """
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    os.makedirs(OVERLAYS_DIR, exist_ok=True)
    
    # 1. Create Canvas (landscape 4x6 print aspect ratio)
    # Using a soft off-white/cream background suitable for weddings
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), (253, 251, 247))
    
    # Decode input base64 images
    decoded_images = [decode_base64_image(img_str) for img_str in images_base64]
    
    if layout_type == "single" and decoded_images:
        # One portrait shot, caption band beneath. The box is 2:3, which is what
        # the rotated camera delivers, so ImageOps.fit trims nothing.
        img_resized = ImageOps.fit(decoded_images[0], SINGLE_BOX)
        canvas.paste(img_resized, SINGLE_POS)

    elif layout_type == "collage" and len(decoded_images) >= 3:
        # Three shots on a 2x2 grid; the fourth cell is left for the caption.
        for img, pos in zip(decoded_images[:3], COLLAGE_CELLS):
            canvas.paste(ImageOps.fit(img, COLLAGE_CELL), pos)

    else:
        # Neither layout can be satisfied. Falling through would save a blank
        # cream canvas with the couple's names printed on it and return the
        # filename as though nothing had gone wrong — the guest collects an
        # empty keepsake and the log says nothing. The FSM only enqueues once
        # capturedImages >= totalShots, so this is a guard rather than a live
        # path; it exists to fail loudly if that ever stops being true. The job
        # queue turns the raise into the submitter's on_failure.
        raise ValueError(
            f"Cannot build a '{layout_type}' layout from {len(decoded_images)} image(s)"
        )

    # 3. Apply Overlay Template
    overlay_filename = ""
    for o in overlays:
        if o.id == overlay_id:
            overlay_filename = o.filename
            break
            
    if overlay_filename:
        overlay_path = os.path.join(OVERLAYS_DIR, overlay_filename)
        if not os.path.exists(overlay_path):
            # Print without the frame, and say so. This used to call
            # create_mock_overlay_png(), which DREW a substitute out of ellipses
            # and rectangles and printed that on the guest's keepsake. It read
            # as dev scaffolding but it was live: the default catalogue named
            # blush_floral.png / gold_glitter.png, neither of which ships, so
            # any booth running on defaults — a fresh install, or a boot after
            # _quarantine_bad_config moves a corrupt config aside to "start
            # clean" — silently printed fabrications instead of the real
            # artwork. A missing asset is now visible rather than papered over.
            log.error("photo_processor", "overlay_missing",
                      f"Overlay '{overlay_id}' names {overlay_filename}, which is not in "
                      f"{OVERLAYS_DIR} — printing this photo with no frame",
                      data={"overlay_id": overlay_id, "filename": overlay_filename})
        else:
            try:
                overlay_img = Image.open(overlay_path).convert("RGBA")
                # Resize overlay to match canvas dimensions
                overlay_img = overlay_img.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
                # Paste overlay using its own alpha channel as a mask
                canvas.paste(overlay_img, (0, 0), overlay_img)
            except Exception as e:
                # Was a bare print(), invisible to the log — the same Rule 16
                # hole the audit closed in storage.py. The guest's print coming
                # out unframed has to be explicable afterwards.
                log.error("photo_processor", "overlay_apply_fail",
                          f"Could not apply overlay {overlay_filename}: {e} — "
                          f"printing this photo with no frame",
                          data={"overlay_id": overlay_id, "filename": overlay_filename,
                                "error": str(e)})
                
    # 4. Render Customizable Text
    if text:
        draw = ImageDraw.Draw(canvas)

        # Which box the caption sits in depends on the layout: a full-width band
        # under a single shot, or the free fourth cell of the collage grid.
        if layout_type == "single":
            box_x, box_y = 0, SINGLE_CAPTION_TOP
            box_w, box_h = SINGLE_CAPTION_BOX
        else:
            box_x, box_y = COLLAGE_CAPTION_POS
            box_w, box_h = COLLAGE_CAPTION_BOX

        font = _fit_font(draw, text, box_w - 2 * CAPTION_PADDING, CAPTION_MAX_SIZE)
        bbox = draw.textbbox((0, 0), text, font=font)

        # Centre in the box on both axes. bbox carries the glyphs' own offset
        # from the origin, so it has to come off the position or the text sits
        # low by its ascent.
        text_x = box_x + (box_w - (bbox[2] - bbox[0])) // 2 - bbox[0]
        text_y = box_y + (box_h - (bbox[3] - bbox[1])) // 2 - bbox[1]

        # Draw elegant dark rose/gold text
        draw.text((text_x, text_y), text, fill=(50, 30, 40), font=font)

    # 5. Save the photo
    filename = f"photo_{uuid.uuid4().hex[:10]}.jpg"
    filepath = os.path.join(PHOTOS_DIR, filename)
    
    # Keep the dpi tag. Untagged, CUPS picks its own scale for the bitmap and
    # the print comes out letterboxed or squeezed (CONSTRAINTS.md §6).
    canvas.save(filepath, "JPEG", quality=95, dpi=(PRINT_DPI, PRINT_DPI))
    return filename


def generate_alignment_card(printer_name: str, options: str) -> str:
    """Draw a 4x6 geometry target and save it like any other print.

    A photo only answers "did paper come out". This answers whether the whole
    6x4 reached the paper, at the right scale, the right way round. Permanent
    bench instrument, the paper counterpart to the LED strip test — so Rule 24
    does not apply to it.

    Docs/PRINTER_NOTES.md explains how to read what comes out. Every mark below
    exists to make one specific distortion visible; none of it is decoration.
    """
    os.makedirs(PHOTOS_DIR, exist_ok=True)

    ink = (20, 20, 20)
    faint = (150, 150, 150)
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    # Edge rule, one pixel in from each edge so it renders at all.
    draw.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline=ink, width=3)

    # The scale check: these must land on real inch marks under a ruler.
    half = PRINT_DPI // 2
    tick_font = get_font(28)
    for x in range(half, CANVAS_W, half):
        long = (x % PRINT_DPI == 0)
        draw.line([x, 0, x, 40 if long else 22], fill=ink, width=3)
        draw.line([x, CANVAS_H, x, CANVAS_H - (40 if long else 22)], fill=ink, width=3)
        if long:
            draw.text((x + 8, 44), f"{x // PRINT_DPI}\"", fill=ink, font=tick_font)
    for y in range(half, CANVAS_H, half):
        long = (y % PRINT_DPI == 0)
        draw.line([0, y, 40 if long else 22, y], fill=ink, width=3)
        draw.line([CANVAS_W, y, CANVAS_W - (40 if long else 22), y], fill=ink, width=3)
        if long:
            draw.text((44, y + 8), f"{y // PRINT_DPI}\"", fill=ink, font=tick_font)

    # The aspect check. Must stay a circle, not an ellipse-by-accident.
    cx, cy = CANVAS_W // 2, CANVAS_H // 2
    r = PRINT_DPI  # 1 inch radius
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=faint, width=3)
    draw.line([cx - r - 60, cy, cx + r + 60, cy], fill=faint, width=2)
    draw.line([cx, cy - r - 60, cx, cy + r + 60], fill=faint, width=2)

    # The orientation check.
    draw.text((70, 110), "TOP LEFT", fill=ink, font=get_font(40))

    # Provenance, so a stack of test cards stays readable. Kept clear of the
    # circle: a label crossing it hides the distortion the circle reveals.
    lines = [
        (f'{CANVAS_W} x {CANVAS_H} px  |  6" x 4" at {PRINT_DPI} dpi', get_font(34), ink),
        (f"queue: {printer_name}", get_font(26), faint),
        (f"options: {options or '(none)'}", get_font(26), faint),
        (datetime.now().strftime("%Y-%m-%d %H:%M"), get_font(26), faint),
    ]
    y = cy + r + 70
    for text, font, colour in lines:
        w = draw.textbbox((0, 0), text, font=font)[2]
        draw.text((cx - w // 2, y), text, fill=colour, font=font)
        y += font.size + 8

    # Named so circular storage sweeps it up with everything else, and so an
    # operator can tell test cards from guests' photos in the folder.
    filename = f"photo_printtest_{uuid.uuid4().hex[:8]}.jpg"
    filepath = os.path.join(PHOTOS_DIR, filename)
    canvas.save(filepath, "JPEG", quality=95, dpi=(PRINT_DPI, PRINT_DPI))
    log.info("photo_processor", "print_test_card",
             f"Alignment card generated: {filename}",
             data={"printer_name": printer_name, "options": options})
    return filename

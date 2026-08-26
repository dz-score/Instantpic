import os
import base64
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

def decode_base64_image(base64_str: str) -> Image.Image:
    """Decode a base64 data URI to a PIL Image, or load directly from disk if it's a filename."""
    if base64_str.startswith("data:image"):
        if "," in base64_str:
            base64_str = base64_str.split(",")[1]
        image_data = base64.b64decode(base64_str)
        return Image.open(BytesIO(image_data))
    else:
        # A local filename produced by the gphoto2 capture. Reads this module's
        # PHOTOS_DIR, same as every other function here — it used to re-import
        # the name from backend.storage at call time, so one module answered to
        # two authorities. Harmless while both point at backend.paths and the
        # fixture patches both, which is exactly why it would have gone unnoticed
        # until one of them moved (see diagnostics.py, which did).
        filepath = os.path.join(PHOTOS_DIR, base64_str)
        return Image.open(filepath)

# Longest edge of a screen preview, in pixels. The booth panel is 1920x1080 and
# the reveal photo is capped at 58vh, so 1400 is already more than it can show.
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
                preview = img.convert("RGB")
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
    Process single or multi-photo layouts into an 1800x1200 canvas,
    overlay selected template and draw custom branding text.
    Returns the filename of the saved image.

    `overlays` is the configured catalogue, passed in rather than read from a global
    so this stays a pure function of its arguments.
    """
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    os.makedirs(OVERLAYS_DIR, exist_ok=True)
    
    # 1. Create Canvas (1800x1200 landscape - 4x6 print aspect ratio)
    # Using a soft off-white/cream background suitable for weddings
    canvas = Image.new("RGB", (1800, 1200), (253, 251, 247))
    
    # Decode input base64 images
    decoded_images = [decode_base64_image(img_str) for img_str in images_base64]
    
    if layout_type == "single" and decoded_images:
        # Single Landscape photo placement
        img = decoded_images[0]
        # Crop or resize to fit 3:2 nicely with elegant borders
        # We want to fit it to 1440x960, centered horizontally
        img_resized = ImageOps.fit(img, (1440, 960))
        canvas.paste(img_resized, (180, 80)) # x = 180, y = 80. Bottom is 1040.
        
    elif layout_type == "collage" and len(decoded_images) >= 3:
        # 3-Photo horizontal collage
        # Crop each photo to portrait 3:4 aspect ratio (540x720)
        collage_width = 540
        collage_height = 720
        gap = 45 # (1800 - (3 * 540)) / 4 = 45
        
        for idx, img in enumerate(decoded_images[:3]):
            cropped_img = ImageOps.fit(img, (collage_width, collage_height))
            x_pos = gap + idx * (collage_width + gap)
            y_pos = 80
            canvas.paste(cropped_img, (x_pos, y_pos)) # Bottom is 800

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
                overlay_img = overlay_img.resize((1800, 1200), Image.LANCZOS)
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
        font = get_font(52)
        
        # Calculate text dimensions using textbbox
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        
        # Centered horizontally
        text_x = (1800 - text_width) // 2
        
        # Y position: vertical alignment in the bottom area
        # For single, bottom area is 1040 -> 1200. Center is 1120.
        # For collage, bottom area is 800 -> 1200. Center is 1000.
        if layout_type == "single":
            text_y = 1040 + (160 - (bbox[3] - bbox[1])) // 2 - 10
        else:
            text_y = 800 + (400 - (bbox[3] - bbox[1])) // 2 - 20
            
        # Draw elegant dark rose/gold text
        draw.text((text_x, text_y), text, fill=(50, 30, 40), font=font)
        
    # 5. Save the photo
    import uuid
    filename = f"photo_{uuid.uuid4().hex[:10]}.jpg"
    filepath = os.path.join(PHOTOS_DIR, filename)
    
    # Save as high-quality JPEG
    canvas.save(filepath, "JPEG", quality=95)
    return filename

import os
from io import BytesIO

import qrcode
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from backend import storage

router = APIRouter(tags=["photos"])


@router.get("/api/photos")
async def list_photos():
    """Get list of all saved photos, newest first."""
    try:
        return storage.get_all_photos()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/qrcode")
async def get_qrcode(text: str):
    """Generate a QR code dynamically for the local download link."""
    try:
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"QR generation failed: {str(e)}")


@router.get("/download/{filename}")
async def download_photo(filename: str):
    """Serve a photo as a downloadable file for guest phones (they reach this
    by scanning the QR code shown on the review screen)."""
    # Read PHOTOS_DIR off the module at call time, not at import — the test
    # fixture redirects it to a tmp dir after this module is imported.
    filepath = os.path.join(storage.PHOTOS_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(
        filepath,
        media_type="image/jpeg",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

import os
import shutil
from io import BytesIO
import qrcode
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional

from backend.config import load_settings, update_settings, AppSettings
from backend.storage import (
    ensure_directories,
    get_all_photos,
    enforce_circular_storage,
    PHOTOS_DIR,
    OVERLAYS_DIR,
    BASE_DIR
)
from backend.photo_processor import process_photo_layout
from backend.printer import print_photo

app = FastAPI(title="Embedded Photo Booth API", version="1.0.0")

# CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure folders exist
ensure_directories()

# Request schemas
class ConfigUpdateRequest(BaseModel):
    printer_name: Optional[str] = None
    max_photos: Optional[int] = None
    disk_min_free_gb: Optional[float] = None
    default_text: Optional[str] = None
    selected_overlay: Optional[str] = None

class SavePhotoRequest(BaseModel):
    images: List[str]  # Base64 data URIs
    layout: str        # 'single' or 'collage'
    text: str          # Custom banner text
    overlay_id: str    # Selected overlay ID

# Endpoints
@app.get("/api/config", response_model=AppSettings)
async def get_config():
    """Retrieve current application settings."""
    return load_settings()

@app.post("/api/config", response_model=AppSettings)
async def post_config(updates: ConfigUpdateRequest):
    """Update configurations."""
    try:
        updated = update_settings(updates.model_dump(exclude_unset=True))
        return updated
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/photos")
async def list_photos():
    """Get list of all saved photos, newest first."""
    try:
        return get_all_photos()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/save-photo")
async def save_photo(req: SavePhotoRequest, background_tasks: BackgroundTasks):
    """
    Stitches base64 images into a collage or formats a single photo,
    adds overlay/text, saves to disk, and runs circular space enforcement.
    """
    if not req.images:
        raise HTTPException(status_code=400, detail="No images provided")
    if req.layout not in ("single", "collage"):
        raise HTTPException(status_code=400, detail="Invalid layout type")
        
    try:
        # Run Pillow processor
        filename = process_photo_layout(
            images_base64=req.images,
            layout_type=req.layout,
            text=req.text,
            overlay_id=req.overlay_id
        )
        
        # Enforce FIFO limits in background to avoid blocking API response
        background_tasks.add_task(enforce_circular_storage)
        
        return {
            "status": "success",
            "filename": filename,
            "url": f"/photos/{filename}"
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Image processing failed: {str(e)}")

@app.post("/api/print/{filename}")
async def trigger_print(filename: str):
    """Trigger a print job for a specific saved photo."""
    filepath = os.path.join(PHOTOS_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Photo not found")
        
    success = print_photo(filepath)
    if success:
        return {"status": "success", "detail": f"Printed {filename}"}
    else:
        raise HTTPException(status_code=500, detail="Printing failed. Check CUPS setup.")

@app.get("/api/health")
async def health_check():
    """Health check endpoint for connection watchdog."""
    return {"status": "ok"}

@app.get("/api/qrcode")
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

# Serve Static Folders
app.mount("/photos", StaticFiles(directory=PHOTOS_DIR), name="photos")
app.mount("/overlays", StaticFiles(directory=OVERLAYS_DIR), name="overlays")

# Serve Frontend App (Dist directory)
FRONTEND_DIST = os.path.join(BASE_DIR, "frontend", "dist")

# If the frontend is built, serve it statically.
# Else serve a simple message for backend testing.
if os.path.exists(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")
    
    # Catch-all route to serve the built index.html for SPA routing (e.g. /download/:id, /admin, etc.)
    @app.get("/{catchall:path}")
    async def serve_frontend(catchall: str):
        # Allow static endpoints to pass through if they start with api/photos/overlays
        if catchall.startswith("api/") or catchall.startswith("photos/") or catchall.startswith("overlays/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
else:
    @app.get("/")
    async def serve_backend_index():
        return {
            "message": "FastAPI Photo Booth backend is running! Frontend is not built yet.",
            "endpoints": ["/api/config", "/api/photos", "/photos", "/overlays"]
        }

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
from backend.logger import log

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

# Log startup
log.info("system", "system_boot", f"Backend started", data={"version": "1.0.0"})

# Request schemas
class ConfigUpdateRequest(BaseModel):
    printer_name: Optional[str] = None
    max_photos: Optional[int] = None
    disk_min_free_gb: Optional[float] = None
    couple_names: Optional[str] = None
    event_date: Optional[str] = None
    default_text: Optional[str] = None
    selected_overlay: Optional[str] = None
    welcome_message: Optional[str] = None
    thank_you_message: Optional[str] = None
    countdown_duration: Optional[int] = None
    flash_enabled: Optional[bool] = None
    max_photos_per_session: Optional[int] = None
    session_timeout: Optional[int] = None
    show_names_on_photo: Optional[bool] = None
    wifi_network_name: Optional[str] = None

class SavePhotoRequest(BaseModel):
    images: List[str]  # Base64 data URIs
    layout: str        # 'single' or 'collage'
    text: str          # Custom banner text
    overlay_id: str    # Selected overlay ID

# Endpoints
@app.get("/api/config", response_model=AppSettings)
async def get_config():
    """Retrieve current application settings."""
    settings = load_settings()
    return settings

@app.post("/api/config", response_model=AppSettings)
async def post_config(updates: ConfigUpdateRequest):
    """Update configurations."""
    try:
        changed = {k: v for k, v in updates.model_dump(exclude_unset=True).items() if v is not None}
        updated = update_settings(changed)
        log.info("config", "config_updated", f"Config updated: {list(changed.keys())}", data=changed)
        return updated
    except Exception as e:
        log.error("config", "config_update_fail", f"Config update failed: {e}")
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

    import time as _time
    t0 = _time.monotonic()
    log.info("photo", "photo_process_start", f"Processing {req.layout} photo", data={"layout": req.layout, "overlay": req.overlay_id, "image_count": len(req.images)})

    try:
        filename = process_photo_layout(
            images_base64=req.images,
            layout_type=req.layout,
            text=req.text,
            overlay_id=req.overlay_id
        )
        dur = int((_time.monotonic() - t0) * 1000)
        log.info("photo", "photo_process_done", f"Photo processed: {filename}", dur=dur, data={"filename": filename, "layout": req.layout, "overlay": req.overlay_id})

        background_tasks.add_task(enforce_circular_storage)

        return {
            "status": "success",
            "filename": filename,
            "url": f"/photos/{filename}"
        }
    except Exception as e:
        dur = int((_time.monotonic() - t0) * 1000)
        log.error("photo", "photo_process_fail", f"Photo processing failed: {e}", dur=dur, data={"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Image processing failed: {str(e)}")

@app.post("/api/print/{filename}")
async def trigger_print(filename: str):
    """Trigger a print job for a specific saved photo."""
    filepath = os.path.join(PHOTOS_DIR, filename)
    if not os.path.exists(filepath):
        log.warn("printer", "printer_file_missing", f"Print requested for missing file: {filename}")
        raise HTTPException(status_code=404, detail="Photo not found")

    log.info("printer", "printer_sent", f"Print job sent: {filename}", data={"filename": filename})
    success = print_photo(filepath)
    if success:
        log.info("printer", "printer_done", f"Print completed: {filename}", data={"filename": filename})
        return {"status": "success", "detail": f"Printed {filename}"}
    else:
        log.error("printer", "printer_fail", f"Print failed: {filename}", data={"filename": filename})
        raise HTTPException(status_code=500, detail="Printing failed. Check CUPS setup.")

@app.get("/api/health")
async def health_check():
    """Health check endpoint for connection watchdog."""
    return {"status": "ok"}

# --- Diagnostics ---
@app.get("/api/diagnostics")
async def get_diagnostics():
    from backend.diagnostics import get_diagnostics
    return get_diagnostics()

# --- Emergency Controls ---
class EmergencyRequest(BaseModel):
    action: str  # 'restart_booth', 'restart_camera', 'restart_printer', 'clear_queue'

@app.post("/api/emergency")
async def emergency_action(req: EmergencyRequest):
    from backend.diagnostics import execute_emergency
    log.warn("system", "system_emergency", f"Emergency action triggered: {req.action}", data={"action": req.action})
    result = execute_emergency(req.action)
    return result

# --- Change PIN ---
class ChangePinRequest(BaseModel):
    current_pin: str
    new_pin: str

@app.post("/api/change-pin")
async def change_pin(req: ChangePinRequest):
    settings = load_settings()
    if req.current_pin != settings.admin_pin:
        log.warn("config", "config_pin_fail", "PIN change attempted with wrong current PIN")
        raise HTTPException(status_code=403, detail="Invalid current PIN")
    if len(req.new_pin) < 6:
        raise HTTPException(status_code=400, detail="PIN must be at least 6 digits")
    updated = update_settings({"admin_pin": req.new_pin})
    log.info("config", "config_pin_changed", "Admin PIN changed")
    return {"status": "success", "detail": "PIN updated"}

# --- Frontend Log Ingestion ---
class FrontendLogBatch(BaseModel):
    lines: List[str]  # Pre-formatted JSONL lines from the frontend

@app.post("/api/logs")
async def receive_frontend_logs(batch: FrontendLogBatch):
    """Receive a batch of JSONL log lines from the frontend and write to frontend.log."""
    for line in batch.lines:
        log.write_frontend_line(line)
    return {"status": "ok", "count": len(batch.lines)}

@app.get("/api/logs/recent")
async def get_recent_logs(count: int = 50, source: str = "both"):
    """Tail the last N lines from log files. source: 'backend', 'frontend', or 'both'."""
    import json as _json
    from backend.logger import BACKEND_LOG, FRONTEND_LOG

    def tail_file(filepath, n):
        """Read last n lines from a file efficiently."""
        try:
            with open(filepath, "rb") as f:
                # Seek to end
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return []
                # Read last chunk (generous: 1KB per line estimate)
                chunk_size = min(size, n * 1024)
                f.seek(max(0, size - chunk_size))
                data = f.read().decode("utf-8", errors="replace")
                lines = data.strip().split("\n")
                return lines[-n:]
        except FileNotFoundError:
            return []

    entries = []

    if source in ("backend", "both"):
        for line in tail_file(BACKEND_LOG, count):
            try:
                entries.append(_json.loads(line))
            except _json.JSONDecodeError:
                pass

    if source in ("frontend", "both"):
        for line in tail_file(FRONTEND_LOG, count):
            try:
                entries.append(_json.loads(line))
            except _json.JSONDecodeError:
                pass

    # Sort by timestamp descending (newest first) and cap
    entries.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return entries[:count]


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

# --- Network Info (for QR code URLs) ---
def _get_lan_ip():
    """Get the machine's LAN IP address."""
    import socket
    try:
        # Connect to an external address to determine the outbound interface
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

@app.get("/api/network-info")
async def get_network_info():
    """Return the booth's LAN IP and port for QR code URL generation."""
    settings = load_settings()
    ip = _get_lan_ip()
    port = getattr(settings, "port", 8000)
    return {
        "ip": ip,
        "port": port,
        "base_url": f"http://{ip}:{port}",
    }

# --- Photo Download (for guests scanning QR) ---
@app.get("/download/{filename}")
async def download_photo(filename: str):
    """Serve a photo as a downloadable file for guest phones."""
    filepath = os.path.join(PHOTOS_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(
        filepath,
        media_type="image/jpeg",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

# Serve Static Folders
app.mount("/photos", StaticFiles(directory=PHOTOS_DIR), name="photos")
app.mount("/overlays", StaticFiles(directory=OVERLAYS_DIR), name="overlays")

# Serve Frontend App (Dist directory)
FRONTEND_DIST = os.path.join(BASE_DIR, "frontend", "dist")

# If the frontend is built, serve it statically.
# Else serve a simple message for backend testing.
if os.path.exists(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")
    
    # Catch-all route: serve static files from dist/ if they exist,
    # otherwise fall back to index.html for SPA routing.
    @app.get("/{catchall:path}")
    async def serve_frontend(catchall: str):
        # Skip API / photo / overlay routes
        if catchall.startswith("api/") or catchall.startswith("photos/") or catchall.startswith("overlays/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        
        # Check if the file exists in dist/ (e.g. bg-wedding.png, preview-single.png)
        file_path = os.path.join(FRONTEND_DIST, catchall)
        if catchall and os.path.isfile(file_path):
            return FileResponse(file_path)
        
        # SPA fallback
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
else:
    @app.get("/")
    async def serve_backend_index():
        return {
            "message": "FastAPI Photo Booth backend is running! Frontend is not built yet.",
            "endpoints": ["/api/config", "/api/photos", "/photos", "/overlays"]
        }

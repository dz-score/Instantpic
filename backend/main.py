import os
import signal
import threading
from contextlib import asynccontextmanager
from types import FrameType

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from backend.storage import ensure_directories, PHOTOS_DIR, OVERLAYS_DIR, BASE_DIR
from backend.settings import SettingsService
from backend.print_service import PrintService
from backend.sse_service import sse_svc
from backend.logger import log
from backend.state_machine import state_machine
from backend.job_queue import JobQueue
from backend.camera_factory import create_camera

from backend.routers import booth, camera, config, logs, photos, sse, system


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Composition root (Rule 19) ────────────────────────────────────────────
    # Every service is constructed here and handed its collaborators. Nothing below
    # this point reaches for a module global to find a dependency; routes receive
    # what they need via backend/deps.py, which reads app.state.
    ensure_directories()

    log.info("system", "system_boot", f"Backend started", data={"version": "1.0.0"})

    settings_svc = SettingsService()
    settings_svc.load()

    print_svc = PrintService(settings_svc)
    job_queue = JobQueue(print_svc, settings_svc)
    camera_svc = create_camera(settings_svc.get())

    app.state.settings = settings_svc
    app.state.print_svc = print_svc
    app.state.camera = camera_svc

    # Bind the event loop so camera threads can dispatch SSE events safely.
    # Must happen before camera_svc.init() starts emitting from its threads.
    sse_svc.bind_loop()

    # Initialize State Machine and Job Queue
    state_machine.set_job_queue(job_queue)
    state_machine.set_sse(sse_svc)
    if camera_svc:
        # FIRE_SHOT capture completion returns to the FSM via callbacks,
        # mirroring set_job_queue — the camera never imports the FSM.
        state_machine.set_camera(camera_svc)
        camera_svc.set_sse(sse_svc)
    job_queue.start()

    # Eagerly init camera
    if camera_svc:
        camera_svc.init()

    # Workaround for Uvicorn hanging on shutdown due to active streaming responses
    # https://github.com/encode/uvicorn/issues/1579
    default_sigint_handler = signal.getsignal(signal.SIGINT)
    default_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def terminate_now(signum: int, frame: FrameType | None = None):
        log.info("system", "signal_shutdown", "Shutting down active streams via signal handler")
        sse_svc.request_shutdown()
        if camera_svc:
            camera_svc.shutdown()
        print_svc.shutdown()

        if signum == signal.SIGINT and callable(default_sigint_handler):
            default_sigint_handler(signum, frame)
        elif signum == signal.SIGTERM and callable(default_sigterm_handler):
            default_sigterm_handler(signum, frame)

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, terminate_now)
        signal.signal(signal.SIGTERM, terminate_now)

    yield

    # Clean shutdown (fallback for tests where signals aren't used)
    log.info("system", "system_shutdown", "Backend shutting down...")
    sse_svc.request_shutdown()

    await job_queue.stop()

    if camera_svc:
        camera_svc.shutdown()
    print_svc.shutdown()


app = FastAPI(title="Embedded Photo Booth API", version="1.0.0", lifespan=lifespan)

# CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes. These must be registered before the SPA catch-all below, which
# would otherwise swallow every path.
app.include_router(config.router)
app.include_router(booth.router)
app.include_router(camera.router)
app.include_router(photos.router)
app.include_router(system.router)
app.include_router(logs.router)
app.include_router(sse.router)

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

# Gravity Booth — Architecture Overview

> A self-contained wedding photo booth running on a Raspberry Pi (or any Linux host).
> A FastAPI Python backend serves a React/Vite SPA frontend, both on the same process at `localhost:8000`.

---

## Table of Contents

1. [High-Level Architecture](#1-high-level-architecture)
2. [Entry Points](#2-entry-points)
3. [Backend Modules](#3-backend-modules)
4. [Frontend Modules](#4-frontend-modules)
5. [State Machine — Screens & Transitions](#5-state-machine--screens--transitions)
6. [Data Flow — Complete Session Walkthrough](#6-data-flow--complete-session-walkthrough)
7. [Real-Time Communication (SSE)](#7-real-time-communication-sse)
8. [Camera Architecture](#8-camera-architecture)
9. [Photo Processing Pipeline](#9-photo-processing-pipeline)
10. [Print Service Architecture](#10-print-service-architecture)
11. [Storage & Circular Buffer](#11-storage--circular-buffer)
12. [Configuration System](#12-configuration-system)
13. [Logging System](#13-logging-system)
14. [Admin System](#14-admin-system)
15. [Testing](#15-testing)
16. [Deployment](#16-deployment)
17. [Dependency Map](#17-dependency-map)

---

## 1. High-Level Architecture

```
+----------------------------------------------------------+
|                    Raspberry Pi / Linux Host             |
|                                                          |
|  +-----------------------------------------------------+ |
|  |  Chromium (Kiosk Mode)  -  localhost:8000           | |
|  |                                                     | |
|  |  +----------------------------------------------+   | |
|  |  |  React SPA (Vite, served as static dist/)    |   | |
|  |  |                                              |   | |
|  |  |  App.jsx  <--SSE--+   <--REST--+            |   | |
|  |  |  Screen Router    |            |             |   | |
|  |  |  8 Screens        |            |             |   | |
|  |  +----------------------------------------------+   | |
|  |                      |            |                  | |
|  |  +-------------------|------------|---------------+  | |
|  |  |  FastAPI Backend (uvicorn)     |               |  | |
|  |  |                  |            |               |  | |
|  |  |  main.py  -------+-----------+               |  | |
|  |  |   +-- state_machine.py  (booth FSM)          |  | |
|  |  |   +-- job_queue.py      (async worker)       |  | |
|  |  |   +-- sse_service.py    (push events)        |  | |
|  |  |   +-- camera_service.py (gphoto2 wrapper)    |  | |
|  |  |   +-- photo_processor.py (Pillow compositing)|  | |
|  |  |   +-- print_service.py  (CUPS / mock)        |  | |
|  |  |   +-- storage.py        (circular buffer)    |  | |
|  |  |   +-- config.py         (config.json r/w)    |  | |
|  |  |   +-- logger.py         (structured JSONL)   |  | |
|  |  |   +-- diagnostics.py   (health checks)       |  | |
|  |  +---------------------------------------------+   | |
|  +-----------------------------------------------------+ |
|                                                          |
|  USB --> Canon M50 (gphoto2)                             |
|  USB --> Dye-sub Printer (CUPS)                          |
+----------------------------------------------------------+
```

The backend **serves the frontend** as static files (`/frontend/dist/`). There is no separate web server — everything runs through a single uvicorn process.

---

## 2. Entry Points

| Entry Point | Path | Role |
|---|---|---|
| **Backend process** | `backend/main.py` | FastAPI app + lifespan manager |
| **Frontend SPA** | `frontend/src/main.jsx` | React root mount |
| **Run script** | `run.sh` | Production startup: build frontend -> start uvicorn -> open Chromium kiosk |
| **Camera init** | `camera_service.py -> init()` | Called on startup by `main.py` lifespan |
| **Job queue start** | `job_queue.py -> start()` | Called on startup by `main.py` lifespan |

### `run.sh` boot sequence
```
1. Activate Python venv
2. cd frontend && npm run build       -> produces frontend/dist/
3. uvicorn backend.main:app &         -> starts API + serves frontend
4. chromium-browser --kiosk http://localhost:8000
```

### `main.py` lifespan (startup)
```python
ensure_directories()          # photos/ and overlays/
state_machine.set_job_queue() # inject job_queue into state machine
job_queue.start()             # launch asyncio worker task
camera_svc.init()             # connect to gphoto2 camera
# SIGINT/SIGTERM -> graceful shutdown
```

---

## 3. Backend Modules

### `main.py` — API Gateway
**Responsibility:** FastAPI application definition. Declares all HTTP routes, wires services together via the lifespan context manager, serves the frontend SPA.

**Key routes:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/config` | Read all settings |
| `POST` | `/api/config` | Update settings |
| `GET` | `/api/state` | Read current FSM state |
| `POST` | `/api/events` | Send an event to the FSM |
| `GET` | `/api/sse` | Server-Sent Events stream |
| `GET` | `/api/camera/preview` | MJPEG live preview stream |
| `POST` | `/api/camera/capture` | Enqueue a camera capture job |
| `POST` | `/api/camera/standby` | Pause preview worker |
| `POST` | `/api/camera/resume` | Resume preview worker |
| `GET` | `/api/camera/status` | Camera health |
| `GET/POST` | `/api/camera/config` | Camera EXIF/gphoto2 settings |
| `POST` | `/api/print/{filename}` | Trigger a print job |
| `GET` | `/api/printer/status` | Printer health |
| `GET` | `/api/diagnostics` | System diagnostics |
| `POST` | `/api/emergency` | Emergency actions |
| `POST` | `/api/logs` | Frontend log ingestion |
| `GET` | `/api/logs/recent` | Tail recent logs |
| `GET` | `/api/qrcode` | Generate QR code PNG |
| `GET` | `/api/network-info` | LAN IP/port for QR URLs |
| `POST` | `/api/change-pin` | Change admin PIN |
| `GET` | `/download/{filename}` | Guest photo download |
| `GET` | `/*` | SPA catch-all -> `index.html` |

---

### `state_machine.py` — Booth FSM
**Responsibility:** Manages the booth's logical state as a pure event-driven finite state machine. Does **not** issue hardware commands directly.

**State model (`BoothState`):**
```python
screen: str                        # Current UI screen name
layoutMode: str                    # "single" | "collage"
totalShots: int                    # Shots this layout requires (FSM-owned, from SHOTS_PER_LAYOUT)
capturedImages: List[str]          # Raw image references for this take
finalPhoto: Optional[str]          # Filename of processed photo
retakeCount: int                   # Number of retakes this session
allSessionPhotos: List[Dict]       # All processed photos this session
isProcessing: bool                 # True while job_queue is processing
```

**Valid transitions:**
```
ATTRACT       -> [START_SESSION]
CHOOSE_STYLE  -> [SELECT_LAYOUT]
COUNTDOWN     -> [SHOT_CAPTURED]   # one event per shot; FSM stays in COUNTDOWN
                                   # until capturedImages reaches totalShots
REVEAL        -> [RETAKE, PRINT_FROM_REVEAL]
PICK_FAVORITE -> [FAVORITE_SELECT]
FRAME_PICKER  -> [FRAME_SELECT, FRAME_SKIP]
PRINTING      -> [FINISH, ANOTHER]

Global events (from any state): TIMEOUT, FINISH
```

**Job queue callbacks** (called by `job_queue.py` when async work completes):
- `job_photo_processed(filename, images)` -> updates `finalPhoto`, sets `isProcessing=False`
- `job_frame_processed(filename)` -> moves to `PRINTING` screen
- `job_failed(error)` -> clears `isProcessing`

Each state change broadcasts via `sse_svc.dispatch_event("state_update", ...)`.

---

### `job_queue.py` — Async Work Queue
**Responsibility:** Offloads CPU-bound image processing from the asyncio event loop to a thread pool, then calls back into the state machine.

**Architecture:**
```
asyncio.Queue -> _worker() task
                    |
                    +-- PROCESS_PHOTO / PROCESS_FRAME
                    |     +-- loop.run_in_executor(process_photo_layout)
                    |           +-- state_machine.job_photo_processed()
                    |               state_machine.job_frame_processed()
                    |
                    +-- After each job: asyncio.create_task(_run_cleanup())
                                           +-- enforce_circular_storage()
```

**Job types:**
- `PROCESS_PHOTO` — initial capture processing; result goes to `REVEAL` screen
- `PROCESS_FRAME` — frame re-processing after user picks an overlay; result goes to `PRINTING`

---

### `camera_service.py` — Camera Service (gphoto2)
**Responsibility:** Manages a Canon M50 (or compatible) DSLR via `python-gphoto2`. Runs a **dedicated background thread** for live preview, completely decoupled from HTTP consumers.

**Architecture:**
```
+----------------------------------------+
|  _worker_thread (camera-worker)        |
|                                        |
|  Loop:                                 |
|    wait _preview_allowed (Event)       |
|    camera.capture_preview()            |
|    write -> _latest_frame             |
|    notify _frame_condition            |
|    check _cmd_queue for commands       |
+----------------------------------------+
         | frame buffer (Condition)
+------------------------------------------+
|  preview_generator()  <- HTTP consumer  |
|  Waits on _frame_condition              |
|  Yields MJPEG boundary chunks           |
+------------------------------------------+
         | SSE events
+------------------------------------------+
|  _diagnostic_monitor (camera-monitor)   |
|  Periodically dispatches camera_status  |
+------------------------------------------+
```

**Key behaviors:**
- **Decoupled frame buffer**: HTTP is never blocked by camera; old frames are silently overwritten if consumers are slow.
- **Auto-standby watchdog**: If no preview request arrives within `_preview_idle_timeout` (10s), the camera enters standby to avoid overheating/disconnection. An attached MJPEG viewer counts as a preview request on every poll, even while no frames are arriving. The watchdog is deferred while the session is wedged (`_warmup_failed`) so the re-init heal below can complete.
- **Wedged-session fast heal**: If the init-time warmup preview fails (the signature of stale live-view state left by an unclean process kill — config reads work but every preview stalls ~3s then errors `[-1]`), the worker gives up after 2 consecutive errors instead of 6 and re-inits, which cleanly exits the wedged session and restores live view — typically ~13s after launch, while the booth is still on the attract screen.
- **Command queue (`_cmd_queue`)**: Allows thread-safe commands (`STANDBY`, `RESUME`, `CAPTURE`) from the asyncio thread without locks.
- **Capture flow**: `enqueue_capture()` -> worker thread runs `_execute_capture_job()`, which emits granular `camera_job` SSE states (`started` -> `fired` -> `downloading` -> `completed`/`failed`) as it triggers the shutter and downloads the file.
- **Capture retry-once policy**: If a trigger or download attempt fails, `_execute_capture_job()` waits `CAPTURE_RETRY_DELAY_S` (1.5s), reconnects if needed, and retries exactly once before emitting a terminal `failed` event. This mirrors `PrintService`'s retry-once pattern and is a workflow decision — it must never live in the frontend (Rule 14). A shot that fails both attempts stays `failed`; the frontend surfaces a retry/home affordance rather than silently hanging.
- **Exponential backoff**: On init failure, waits `_init_backoff` seconds (doubles up to 60s) before retrying.

**Mock mode:** `mock_camera.py` provides `MockCameraService` — same API, generates synthetic frames for dev/Windows environments (selected via `config.json -> camera_backend: "mock"`).

**Hardware field knowledge:** [CAMERA_NOTES.md](CAMERA_NOTES.md) documents how this specific M50 body behaves (wedged sessions, stall cycles, widgets that must never be written, diagnostic log signatures). Read it before changing `camera_service.py`.

---

### `photo_processor.py` — Image Compositor
**Responsibility:** Composites captured images into a final 1800x1200 px canvas using Pillow, applies overlays, and renders branding text.

**Pipeline:**
```
1. Decode images (base64 data URI or filename from disk)
2. Create 1800x1200 RGB canvas (cream background #fdfbf7)
3. Place images:
   - "single"  -> one photo at 1440x960, centered with margins
   - "collage" -> three photos at 540x720, evenly spaced
4. Load overlay PNG from backend/overlays/ (RGBA, full-canvas)
   -> composited with its own alpha channel
5. Render Playfair Display text in the bottom margin
6. Save as JPEG quality=95 -> backend/photos/photo_<uuid>.jpg
7. Return filename
```

**Font handling:** Downloads `PlayfairDisplay-Regular.ttf` from GitHub on first use; falls back to system default.

---

### `print_service.py` — Print Service
**Responsibility:** Abstracts printer hardware behind a driver interface. Handles retries, status caching, and structured logging.

**Class hierarchy:**
```
PrinterDriver (ABC)
   +-- CupsPrinterDriver  -> lp / lpstat CLI (Linux production)
   +-- MockPrinterDriver  -> no-op (Windows dev / printer_name="mock")

PrintService (singleton print_svc)
   +-- wraps driver with: retry logic, status caching, structured logs
```

**Printer selection:** determined at runtime by `config.json -> printer_name`.
`"mock"` -> `MockPrinterDriver`; any other string -> `CupsPrinterDriver` with that CUPS queue name.

---

### `sse_service.py` — Server-Sent Events
**Responsibility:** Fan-out real-time events from backend to all connected frontend clients using SSE.

**Architecture:**
```
SseService (singleton sse_svc)
   _clients: List[SseClient]          <- one per browser tab

   dispatch_event(event_type, data)   <- called by any module
     +-- put_nowait() into each client's asyncio.Queue(maxsize=100)
         (drops silently if queue full -- stale connections)

   event_iterator(client)             <- async generator for EventSourceResponse
     +-- pops from client queue, yields SSE payloads
         auto-removes client on disconnect or shutdown
```

**Events emitted:**

| Event | Emitted by | Content |
|---|---|---|
| `state_update` | `state_machine.py` | Full `BoothState` dict |
| `camera_status` | `camera_service.py` (monitor) | `{connected, is_capturing, error, ...}` |
| `camera_job` | `camera_service.py` | `{job_id, status, filename?, error?}` — status: `started`/`fired`/`downloading`/`completed`/`failed`, one or more per capture (retried attempts re-emit `fired`) |
| `printer_status` | `print_service.py` | `{connected, ready, status_text}` |

---

### `storage.py` — File Storage
**Responsibility:** Directory management and circular storage enforcement.

**Functions:**
- `ensure_directories()` — creates `photos/` and `overlays/` if absent
- `get_all_photos()` — returns filenames sorted by mtime descending
- `enforce_circular_storage()` — called after each job:
  1. Delete oldest photos if count exceeds `max_photos`
  2. Delete oldest photos if free disk space < `disk_min_free_gb`

---

### `config.py` — Configuration
**Responsibility:** Read/write `config.json` at the project root.

**Key settings:**

| Key | Default | Description |
|---|---|---|
| `camera_backend` | `"gphoto2"` | `"gphoto2"` or `"mock"` |
| `admin_pin` | `"123456"` | PIN for admin panel |
| `couple_names` | — | Printed on photos |
| `event_date` | — | Printed on photos |
| `countdown_duration` | `3` | Seconds per countdown |
| `shot_interval_ms` | `3000` | Pacing (ms) between shots in a multi-shot layout — backend-owned per Rule 14 |
| `max_photos_per_session` | `3` | Retake limit |
| `session_timeout` | `120` | Inactivity timeout (seconds) |
| `printer_name` | `"mock"` | CUPS queue name |
| `max_photos` | `1000` | Circular storage photo limit |
| `disk_min_free_gb` | `2.0` | Circular storage disk limit |
| `selected_overlay` | `"none"` | Default overlay ID |
| `overlays` | `[...]` | Array of `{id, name, filename}` |
| `wifi_network_name` | — | Shown on Download screen QR |

---

### `logger.py` — Structured Logger
**Responsibility:** JSONL logging to rotating files, with dual output (file + stdout).

**Log files:**
- `logs/backend_<startup-timestamp>.log` — structured JSONL from backend, one new file per process startup (5MB x 3 backups within a run)
- `logs/frontend_<startup-timestamp>.log` — pre-formatted JSONL lines forwarded from the frontend via `POST /api/logs`, same per-startup naming

**JSONL schema:**
```json
{
  "ts": "2026-06-29T08:00:00.000Z",
  "level": "INFO",
  "source": "backend",
  "module": "camera",
  "event": "capture_completed",
  "msg": "Photo captured: photo_abc123.jpg",
  "sid": null,
  "dur": null,
  "data": { "filename": "photo_abc123.jpg" }
}
```

---

### `diagnostics.py` — Diagnostics & Emergency Controls
**Responsibility:** System health checks and operator emergency actions.

**Diagnostics endpoint** (`GET /api/diagnostics`):
- `printer` — queries `print_svc.get_status()`
- `storage` — disk usage, photo count, limit thresholds

**Emergency actions** (`POST /api/emergency`):

| Action | Effect |
|---|---|
| `restart_booth` | `systemctl restart chromium-kiosk && photobooth` |
| `restart_camera` | Signal only (camera re-initializes automatically) |
| `restart_printer` | `systemctl restart cups` |
| `clear_queue` | `cancel -a` (clear all CUPS jobs) |

> On Windows: all actions return a mock success response.

---

## 4. Frontend Modules

### `main.jsx` — React Entry Point
Mounts `<App />` into `#root`. Minimal boilerplate.

---

### `App.jsx` — Root Orchestrator
**Responsibility:** The single source of truth for the frontend. Owns the screen router, inactivity timer, admin unlock gesture, and all event handlers.

**Hooks consumed:**

| Hook | Purpose |
|---|---|
| `useSse()` | Receives backend state, camera/printer status, online flag |
| `useCamera(cameraStatus)` | Preview URL, capture, standby, resume |
| `useApi(isOnline)` | All REST calls, config, events |

**Screen routing:** driven entirely by `appState.screen` from the backend FSM. The frontend has **no independent state machine** — it reflects what the backend tells it.

**Inactivity timer:** `pointerdown` resets a `setTimeout` of `config.session_timeout` seconds. On expiry, fires `api.sendEvent('TIMEOUT')`.

**Admin unlock:** 5 rapid taps on the hidden "L'Etoile" branding button (within 2 seconds) opens the Admin Panel.

---

### Screens (`src/screens/`)

| Screen | File | Shown when `state.screen ==` | Responsibility |
|---|---|---|---|
| **AttractScreen** | `AttractScreen.jsx` | `ATTRACT` | Idle welcome. Language picker. Start button. |
| **ChooseStyleScreen** | `ChooseStyleScreen.jsx` | `CHOOSE_STYLE` | Single vs. collage layout selection. |
| **CountdownScreen** | `CountdownScreen.jsx` | `COUNTDOWN` | Live MJPEG preview. Per-shot countdown. Triggers captures and reports each one via `SHOT_CAPTURED`. Owns no retry, pacing, or completion logic — those are backend-owned (Rule 14); on a permanently failed shot it shows a retry/home overlay rather than deciding what to do next. |
| **RevealScreen** | `RevealScreen.jsx` | `REVEAL` | Shows processed photo. Retake / proceed to print. |
| **PickFavoriteScreen** | `PickFavoriteScreen.jsx` | `PICK_FAVORITE` | Choose best photo from multi-retake session. |
| **FramePickerScreen** | `FramePickerScreen.jsx` | `FRAME_PICKER` | Choose decorative overlay frame. |
| **PrintingScreen** | `PrintingScreen.jsx` | `PRINTING` | Triggers print, shows QR download code, finish/another. |
| **DownloadScreen** | `DownloadScreen.jsx` | URL `/download/*` | Mobile guest download page (served directly). |

---

### Shared Components (`src/components/`)

| Component | Description |
|---|---|
| `Button` | Styled button primitive |
| `ScreenShell` | Full-screen container wrapper |
| `CountdownRing` | SVG circular countdown animation |
| `ProgressDots` | Shot progress indicator (1/3, 2/3, 3/3) |
| `PhotoFrame` | Image display with frame chrome |
| `ConfettiOverlay` | CSS confetti burst animation |
| `Toast` | Ephemeral notification banner |
| `AdminModal` | PIN entry modal |
| `admin/AdminPanel` | Full-page operator config panel |

---

### Hooks (`src/hooks/`)

#### `useSse.js`
Opens an `EventSource` to `/api/sse`. Reconnects automatically on error (3-second delay). Parses three event types:
- `state_update` -> `backendState`
- `camera_status` -> `cameraStatus`
- `printer_status` -> `printerStatus`

Exposes `isOnline` (true when EventSource is open).

#### `useCamera.js`
Thin wrapper over camera REST endpoints:
- `previewUrl` -> `/api/camera/preview` (MJPEG stream, used as `<img src>`)
- `captureFrame()` -> `POST /api/camera/capture`
- `resumePreview()` -> `POST /api/camera/resume`
- `standbyPreview()` -> `POST /api/camera/standby`

#### `useApi.js`
Centralises all REST interactions:
- Fetches config on mount; caches in `configRef`
- `sendEvent(type, payload)` -> `POST /api/events`
- `printPhoto(filename)` -> `POST /api/print/{filename}`
- `saveConfig(updates)` -> `POST /api/config`
- `getQrUrl(downloadUrl)` -> `/api/qrcode?text=...`
- `getDownloadUrl(filename)` -> `http://{LAN_IP}:{port}/download/{filename}`
- `getDiagnostics()`, `emergencyAction()`, `changePin()`, `getRecentLogs()`

---

### Utils (`src/utils/`)

| File | Description |
|---|---|
| `i18n.js` | Translation map `{en, fr}` with `t(key, lang)` helper |
| `logger.js` | Frontend structured logger; batches JSONL lines, flushes to `POST /api/logs` |
| `sounds.js` | Web Audio API sound effects (countdown beep, shutter click, success chime) |
| `compliments.js` | Random compliment strings shown on RevealScreen |

---

### CSS Architecture (`src/`)

| File | Role |
|---|---|
| `design-tokens.css` | CSS custom properties: colors, spacing, typography, radii, shadows |
| `global.css` | Reset, base typography, utility classes |
| `animations.css` | Keyframe animations (fade, slide, pulse, confetti) |
| `App.css` | App shell, offline overlay, admin trigger |
| `index.css` | Entry import aggregator |
| Per-screen `*.css` | Screen-specific layout styles co-located with each component |

---

## 5. State Machine — Screens & Transitions

```
                    +---------------+
                    |    ATTRACT    |<-------- boot / FINISH / TIMEOUT
                    +---------------+
                           |
                    START_SESSION
                           |
                           v
                    +---------------+
                    | CHOOSE_STYLE  |
                    +---------------+
                           |
                    SELECT_LAYOUT
                           |
                           v
                    +---------------+
               +----|   COUNTDOWN   |<---- RETAKE
               |    +---------------+
               |           |
               |    SHOT_CAPTURED x totalShots
               |           |
          FINISH            v
               |    +---------------+
               |    |    REVEAL     |
               |    +---------------+
               |           |
               |    PRINT_FROM_REVEAL
               |           |
               |    +-------+-------+
               |    |               |
               |    v               v
               |  (multiple   (overlays
               |   retakes)    available)
               |    |               |
               |    v               v
               | PICK_FAVORITE  FRAME_PICKER <-- FAVORITE_SELECT
               |    |               |
               |    +-------+-------+
               |            |
               |     FRAME_SELECT / FRAME_SKIP
               |            |
               |            v
               |    +---------------+
               |    |   PRINTING    |
               |    +---------------+
               |       |        |
               |    FINISH    ANOTHER
               |       |        |
               +-------+        +------> CHOOSE_STYLE
               v
          ATTRACT
```

---

## 6. Data Flow — Complete Session Walkthrough

```
Guest touches screen
        |
        v
App.jsx: handleStart()
        |  api.sendEvent("START_SESSION")
        v
POST /api/events  {type: "START_SESSION"}
        |
        v
state_machine.handle_event("START_SESSION")
        |  state -> CHOOSE_STYLE
        |  sse_svc.dispatch_event("state_update", {screen: "CHOOSE_STYLE"})
        v
SSE -> useSse -> backendState -> App.jsx -> renders ChooseStyleScreen

Guest selects "Collage"
        |  api.sendEvent("SELECT_LAYOUT", {mode: "collage"})
        v
state_machine: state -> COUNTDOWN (layoutMode="collage")
        |  SSE state_update
        v
CountdownScreen renders with live MJPEG preview at /api/camera/preview

[Repeat per shot, up to totalShots=3] Countdown fires
        |  camera.standbyPreview()  -> POST /api/camera/standby
        |  camera.captureFrame()    -> POST /api/camera/capture
        |  camera_svc.enqueue_capture() -> worker thread runs _execute_capture_job()
        |  SSE: camera_job {job_id, status: "fired"}       -> flash/shutter sound
        |  SSE: camera_job {job_id, status: "downloading"}
        |  SSE: camera_job {job_id, status: "completed", filename}
        |      (on failure: CameraService retries once internally after
        |       CAPTURE_RETRY_DELAY_S before emitting a terminal "failed";
        |       the frontend never orchestrates the retry — Rule 14)
        |  camera.resumePreview()   -> POST /api/camera/resume
        v
CountdownScreen: onShotCaptured(filename)
        |  api.sendEvent("SHOT_CAPTURED", {filename})
        v
state_machine: capturedImages.append(filename)
        |  len(capturedImages) < totalShots?
        |    -> stay in COUNTDOWN, SSE state_update (capturedImages/totalShots)
        |    -> CountdownScreen shows "BETWEEN" interstitial for shot_interval_ms
        |       (backend config), then fires the next shot
        |  len(capturedImages) >= totalShots?
        |    -> state -> REVEAL, isProcessing=true
        |    -> job_queue.enqueue({type:"PROCESS_PHOTO", images, layout,
        |         text: _compose_banner_text(settings), overlay_id})
        |    -> SSE state_update -> RevealScreen renders with spinner
        v
job_queue._worker() [thread pool]
        |  process_photo_layout(images, "collage", text, overlay_id)
        |    -> decode images -> composite canvas -> apply overlay -> render text
        |    -> save photo_abc123.jpg -> return filename
        |  state_machine.job_photo_processed("photo_abc123.jpg", images)
        |    -> state.finalPhoto = "photo_abc123.jpg", isProcessing=false
        |    -> SSE state_update
        v
RevealScreen: shows /photos/photo_abc123.jpg

Guest taps "Print"
        |  api.sendEvent("PRINT_FROM_REVEAL", {overlays: [...]})
        v
state_machine: overlays.length > 1 -> state -> FRAME_PICKER
        |  SSE -> FramePickerScreen

Guest picks "Blush Floral"
        |  api.sendEvent("FRAME_SELECT", {overlay_id: "blush_floral", text})
        v
state_machine: isProcessing=true
        |  job_queue.enqueue({type:"PROCESS_FRAME", ...})
        v
job_queue: re-processes with overlay -> state_machine.job_frame_processed()
        |  state -> PRINTING, finalPhoto = "photo_xyz456.jpg"
        |  SSE -> PrintingScreen

PrintingScreen:
        |  api.printPhoto("photo_xyz456.jpg")
        |    -> POST /api/print/photo_xyz456.jpg
        |    -> print_svc.print(filepath) -> lp -d <printer_name> ...
        |  Displays QR code -> /api/qrcode?text=http://192.168.x.x:8000/download/photo_xyz456.jpg
        v
Guest scans QR on phone -> hits /download/photo_xyz456.jpg -> FileResponse download

Guest taps "Finish"
        |  api.sendEvent("FINISH")
        v
state -> ATTRACT (full reset)
```

---

## 7. Real-Time Communication (SSE)

The SSE channel at `GET /api/sse` is the **backbone** of the frontend-backend contract. The frontend never polls for state — it only reacts to SSE events.

```
Backend Module          Event Type          Triggered When
----------------------------------------------------------
state_machine.py   ->   state_update      -> Any FSM transition
camera_service.py  ->   camera_status     -> Periodic monitor (~5s)
camera_service.py  ->   camera_job        -> Per capture, granular: started/fired/downloading/completed/failed
print_service.py   ->   printer_status    -> On print attempt
```

**Reconnection:** `useSse.js` auto-reconnects after 3 seconds on error. On reconnect, the frontend also calls `GET /api/state` to catch up on missed state.

---

## 8. Camera Architecture

```
                    +---------------------------+
                    |  CameraService            |
                    |                           |
HTTP GET            |  preview_generator()      |
/api/camera/preview |    +- waits Condition     |
------------------> |       reads _latest_frame |----> MJPEG stream
                    |                           |
HTTP POST           |  enqueue_capture()        |
/api/camera/capture |    +- _cmd_queue.put()   |
------------------> |                           |
                    |                           |
HTTP POST           |  standby()                |
/api/camera/standby |    +- _preview_allowed.clear() |
------------------> |                           |
                    |                           |
HTTP POST           |  resume_preview()         |
/api/camera/resume  |    +- _preview_allowed.set()   |
------------------> |                           |
                    |                           |
  [Thread: camera-worker]                       |
                    |  while not shutdown:      |
                    |    wait _preview_allowed  |
                    |    frame = capture_preview()|
                    |    _latest_frame = frame  |
                    |    notify Condition       |
                    |    check _cmd_queue:      |
                    |      CAPTURE -> _execute_capture_job() |
                    |        -> fired/downloading/completed |
                    |           SSE camera_job events        |
                    |        -> retry once after             |
                    |           CAPTURE_RETRY_DELAY_S on any  |
                    |           failure, then "failed"        |
                    |      STANDBY -> sleep     |
                    |                           |
  [Thread: camera-monitor]                      |
                    |  Every 5s: get_status()   |
                    |  dispatch camera_status   |
                    +---------------------------+
```

---

## 9. Photo Processing Pipeline

```
process_photo_layout(images_base64, layout_type, text, overlay_id)
          |
          +- 1. Decode: base64 data URI  --> PIL Image
          |           OR filename        --> open from photos/
          |
          +- 2. Canvas: 1800x1200 px, cream (#fdfbf7)
          |
          +- 3. Layout composition:
          |     "single"  -> fit 1440x960 @ offset (180, 80)
          |     "collage" -> 3x fit 540x720 @ evenly spaced + gap=45
          |
          +- 4. Overlay: load RGBA PNG from overlays/
          |              resize to 1800x1200
          |              composite with alpha mask
          |
          +- 5. Text: Playfair Display font, size 52
          |           centered horizontally
          |           positioned in bottom margin
          |           color: dark rose (#321e28)
          |
          +- 6. Save: JPEG quality=95
                      -> backend/photos/photo_{uuid10}.jpg
                      return filename
```

---

## 10. Print Service Architecture

```
PrintService.print(filepath)
    |
    +- load config -> printer_name
    +- select driver:
    |    "mock"  -> MockPrinterDriver.print_file() -> noop, returns success
    |    other   -> CupsPrinterDriver.print_file()
    |                 +- lp -d <printer_name> <printer_options> <filepath>
    |
    +- on failure: retry up to N times with backoff
    +- log structured result
    +- return PrintResult {success, job_id, error, duration_ms}

PrintService.get_status()
    +- CupsPrinterDriver: lpstat -p <printer_name>
       parse output -> PrinterStatus {connected, ready, status_text, error}
```

---

## 11. Storage & Circular Buffer

```
backend/photos/          <- all processed JPEGs live here
backend/overlays/        <- overlay PNG frames

After every job completion (job_queue._run_cleanup):
    enforce_circular_storage()
        |
        +- Count photos; if > max_photos:
        |     delete oldest (by mtime) until within limit
        |
        +- Check disk free space; if < disk_min_free_gb:
              delete oldest until space freed
```

Photos are also accessible via HTTP:
- `GET /photos/{filename}` — served as static files (StaticFiles mount)
- `GET /download/{filename}` — served as `Content-Disposition: attachment`

---

## 12. Configuration System

```
config.json  (project root)
     |
     +-- read:  load_settings() -> AppSettings (Pydantic)
     +-- write: save_settings(settings)
     +-- patch: update_settings(dict) = load -> merge -> validate -> save

AppSettings is a Pydantic BaseModel with defaults.
All settings are flat key-value except:
    overlays: List[OverlayConfig]   {id, name, filename}

Runtime camera backend selection (at import time in main.py):
    if settings.camera_backend == "mock":
        from backend.mock_camera import MockCameraService
    else:
        from backend.camera_service import camera_svc
```

---

## 13. Logging System

```
backend/logger.py
    BoothLogger (singleton: log)
        log.info(module, event, msg, data={})
        log.warn(...)
        log.error(...)
        log.debug(...)
        log.write_frontend_line(json_line)  <- from POST /api/logs

    Output:
        logs/backend_<startup-timestamp>.log   (new file per run; RotatingFileHandler, 5MB x3, JSONL)
        stdout             (INFO+, human-readable for systemd journal)

frontend/src/utils/logger.js
    logger.info(module, event, msg, data, dur)
    Batches lines in-memory, flushes every 10s (or on 20 lines)
    -> POST /api/logs  {lines: ["..."]}
    -> backend writes to logs/frontend_<startup-timestamp>.log
```

---

## 14. Admin System

**Access:** 5 rapid taps on the hidden "L'Etoile" watermark -> PIN entry modal -> Admin Panel.

**Admin Panel capabilities:**
- Edit all `AppSettings` (couple names, dates, overlay, timeouts, etc.)
- View system diagnostics (printer status, disk usage, photo count)
- View recent backend + frontend logs (last 50 lines, mixed/filtered)
- Emergency actions: restart booth/camera/printer, clear print queue
- Change admin PIN

**Security:** PIN is stored in plaintext in `config.json`. The backend validates `current_pin` before accepting `POST /api/change-pin`.

---

## 15. Testing

Tests live in `backend/tests/`. Run with `pytest`.

| Test File | Coverage |
|---|---|
| `conftest.py` | Shared fixtures (FastAPI test client, mock camera, etc.) |
| `test_api.py` | Happy-path API endpoint tests |
| `test_api_errors.py` | Error handling / 4xx / 5xx responses |
| `test_camera.py` | CameraService unit tests |
| `test_camera_worker.py` | Worker thread behavior |
| `test_config.py` | Config load/save/update |
| `test_job_queue.py` | Queue enqueue, worker processing, cleanup |
| `test_photo_processor.py` | Image compositing output |
| `test_printer.py` | PrintService driver logic |
| `test_sse.py` | SSE event dispatch and client management |
| `test_state_machine.py` | FSM transition correctness |
| `test_storage.py` | Circular buffer enforcement |

---

## 16. Deployment

**Production (Raspberry Pi / Linux):**
```bash
./run.sh
# 1. Activates venv at backend/.venv
# 2. Builds frontend: npm run build -> frontend/dist/
# 3. Starts uvicorn on 0.0.0.0:8000 (background)
# 4. Launches Chromium in kiosk mode at http://localhost:8000
```

**Systemd services (expected):**
- `photobooth.service` -> runs `uvicorn backend.main:app`
- `chromium-kiosk.service` -> runs Chromium kiosk

**Development:**
```bash
# Backend
uvicorn backend.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm run dev   # Vite dev server on :5173 with proxy to :8000
```

Vite proxies `/api/*` and `/photos/*` to `localhost:8000` in dev mode (configured in `vite.config.js`).

---

## 17. Dependency Map

```
main.py
  +-- config.py          (no backend imports)
  +-- storage.py         <- config.py
  +-- logger.py          (no backend imports)
  +-- sse_service.py     <- logger.py
  +-- state_machine.py   <- logger.py, sse_service.py
  +-- job_queue.py       <- logger.py, photo_processor.py,
  |                         storage.py, state_machine.py
  +-- camera_service.py  <- logger.py, storage.py, sse_service.py
  +-- mock_camera.py     <- logger.py, storage.py, sse_service.py
  +-- photo_processor.py <- config.py
  +-- print_service.py   <- config.py, logger.py
  +-- diagnostics.py     <- config.py, print_service.py

Circular import avoidance:
  state_machine <- job_queue (job_queue imports state_machine)
  job_queue injected into state_machine at startup via set_job_queue()
  -> breaks the cycle at definition time
```

---

*Generated: 2026-06-29*

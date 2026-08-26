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
|  |  |   +-- job_queue.py      (2 lane workers)     |  | |
|  |  |   +-- sse_service.py    (push events)        |  | |
|  |  |   +-- camera/           (gphoto2 package)    |  | |
|  |  |   +-- photo_processor.py (Pillow compositing)|  | |
|  |  |   +-- print_service.py  (CUPS / mock)        |  | |
|  |  |   +-- storage.py        (circular buffer)    |  | |
|  |  |   +-- settings.py       (AppSettings + svc)  |  | |
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
| **Camera init** | `camera/service.py -> init()` | Called on startup by `main.py` lifespan |
| **Job queue start** | `job_queue.py -> start()` | Called on startup by `main.py` lifespan |

### `run.sh` boot sequence
```
1. Activate Python venv
2. cd frontend && npm run build       -> produces frontend/dist/
3. uvicorn backend.main:app &         -> starts API + serves frontend
4. chromium-browser --kiosk http://localhost:8000
```

### `main.py` lifespan (startup) — the composition root
```python
# The composition root. Construction order is the dependency order, no cycles:
ensure_directories()                                  # photos/ and overlays/
sse_svc      = SseService()
settings_svc = SettingsService(); settings_svc.load()
print_svc    = PrintService(settings_svc)
job_queue    = JobQueue(print_svc, settings_svc)
camera_svc   = create_camera(settings_svc.get(), sse_svc)
state_machine = StateMachine(sse_svc, job_queue, camera_svc)   # last: needs the other three

app.state.sse / .settings / .print_svc / .camera / .state_machine = ...   # routes read these

sse_svc.bind_loop()   # bind event loop BEFORE camera threads start dispatching SSE
job_queue.start()     # launch both lane workers
camera_svc.init()     # connect to gphoto2 camera
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
| `POST` | `/api/camera/resume` | Resume preview worker (no capture/standby routes: the shutter fires only via the FSM's FIRE_SHOT; standby is the idle watchdog's call) |
| `GET` | `/api/camera/status` | Camera health |
| `GET/POST` | `/api/camera/config` | Camera EXIF/gphoto2 settings |
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
printStatus: str                   # "idle" | "printing" | "printed" | "failed" — real printer outcome, FSM-owned
```

**Valid transitions:**
```
ATTRACT       -> [START_SESSION]
CHOOSE_STYLE  -> [SELECT_LAYOUT]
COUNTDOWN     -> [FIRE_SHOT]       # one event per shot fires the shutter;
                                   # completion returns via camera->FSM callback
                                   # (never via the browser) and the FSM stays in
                                   # COUNTDOWN until capturedImages reaches totalShots
REVEAL        -> [RETAKE, PRINT_FROM_REVEAL]
PICK_FAVORITE -> [FAVORITE_SELECT]
FRAME_PICKER  -> [FRAME_SELECT, FRAME_SKIP]
PRINTING      -> [FINISH, ANOTHER]

Global events (from any state): TIMEOUT, FINISH
```

**Job completion callbacks** — the FSM supplies these as `on_success`/`on_failure` when it enqueues a job; the queue invokes them blindly and knows nothing about the FSM:
- `job_photo_processed(filename, images)` -> updates `finalPhoto`, sets `isProcessing=False`
- `job_frame_processed(filename)` -> moves to `PRINTING` screen
- `job_failed(error)` -> clears `isProcessing`
- `job_print_done(filename)` / `job_print_failed(error)` -> set `printStatus` to the real printer outcome

Each state change broadcasts via `sse_svc.dispatch_event("state_update", ...)`. The broadcast payload is a snapshot taken under the handler lock, so concurrent job callbacks can never leak later mutations into an earlier broadcast.

**Capture callbacks:** `FIRE_SHOT` (from the UI when its countdown ends) makes the FSM call `camera_svc.enqueue_capture(on_complete, on_failure)` — the camera worker delivers the terminal outcome straight back to the FSM on the event loop (`shot_completed` appends the shot first-hand; `shot_failed` releases the in-flight guard and leaves COUNTDOWN for the UI's retry overlay). Same submitter-owned-callback inversion as the job queue; the browser is never the courier and client-supplied filenames are never trusted. A `_shot_in_flight` guard makes double `FIRE_SHOT`s no-ops.

**COUNTDOWN stall watchdog:** the floor for a browser or camera that died mid-session (no `FIRE_SHOT` ever arrives, or a capture callback never lands). Armed whenever a transition lands in COUNTDOWN, re-armed per shot, cancelled elsewhere; on expiry with no shot progress it resets to ATTRACT (also clearing the in-flight guard) and broadcasts. Window: `capture_stall_timeout` (config, default 75s). Same backend-owned recovery idiom as `TIMEOUT` and `printStatus` (Rule 14).

---

### `job_queue.py` — Async Work Queue
**Responsibility:** Offloads blocking work (CPU-bound image processing, shelling out to CUPS) from the asyncio event loop to a thread pool, then reports the result through per-job `on_success`/`on_failure` coroutines supplied by the submitter. The queue does not know or import whoever consumes the results — the submitter owns that wiring.

**Two lanes.** Printing and processing have opposite latency profiles, and
processing is the one the guest is watching. A print blocks its worker for up
to ~63s (CUPS 30s timeout + retry delay + 30s retry); on a shared lane a guest
who tapped "Another" could queue their processing job behind the *previous*
guest's retrying print and watch the REVEAL spinner through someone else's
paper jam. Each lane is still strictly serial in itself.

**Architecture:**
```
enqueue(job) -> _lane_for(type)
    |
    +-- process lane: asyncio.Queue -> _worker("process")
    |     +-- PROCESS_PHOTO / PROCESS_FRAME  (and any unknown type)
    |     |     +-- loop.run_in_executor(process_photo_layout)
    |     |           +-- await on_success(filename)   # supplied by submitter
    |     |               await on_failure(error)
    |     +-- After each processing job: asyncio.create_task(_run_cleanup())
    |
    +-- print lane:   asyncio.Queue -> _worker("print")
          +-- PRINT_PHOTO
                +-- loop.run_in_executor(print_svc.print)
                      +-- await on_success / on_failure with the real outcome
                                                      +-- enforce_circular_storage()
```

**Job types:**
- `PROCESS_PHOTO` — initial capture processing; result goes to `REVEAL` screen
- `PROCESS_FRAME` — frame re-processing after user picks an overlay; result goes to `PRINTING`
- `PRINT_PHOTO` — sends the final photo to `print_svc` and reports the real success/failure back to the submitter (the FSM projects it as `printStatus`)

---

### `camera/` — Camera Package (gphoto2)
**Responsibility:** Manages a Canon M50 (or compatible) DSLR via `python-gphoto2`. Runs a **dedicated background thread** for live preview, completely decoupled from HTTP consumers.

Split by responsibility (Rule 20) — one module per reason to change:

| Module | Owns |
|---|---|
| `service.py` | `CameraService` — the facade the app talks to; public surface unchanged from the pre-split single class |
| `device.py` | The gphoto2 handle, the camera lock, init/backoff, every locked USB primitive. **Sole importer of `gphoto2`.** |
| `preview.py` | The worker thread, frame buffer, MJPEG generator, idle watchdog, metrics monitor |
| `capture.py` | Capture job queue, retry-once policy, `camera_job` SSE states, FSM callback marshalling |
| `gate.py` | `CaptureGate` — the ONE owner of the "no preview while a capture is pending/in flight" rule (previously duplicated between the worker loop and `resume_preview()`) |
| `factory.py` | `create_camera()` — builds the backend named by the config |
| `mock.py` | `MockCameraService` — same facade contract, no hardware |

**Architecture:**
```
+----------------------------------------+
|  worker thread (camera-worker)         |   preview.py
|                                        |
|  Loop:                                 |
|    runner.run_pending()  (captures     |   capture.py runs ON this
|      execute here — one thread owns    |   thread via the job queue
|      all camera USB I/O)               |
|    gate.preview_may_run()?             |   gate.py decides
|    device.read_preview_frame_locked()  |   device.py touches USB
|    publish -> frame buffer             |
+----------------------------------------+
         | frame buffer (Condition)
+------------------------------------------+
|  generator()  <- HTTP consumer           |  preview.py
|  Waits on the frame condition            |
|  Yields MJPEG boundary chunks            |
+------------------------------------------+
         | SSE events
+------------------------------------------+
|  monitor thread (camera-monitor)         |  preview.py
|  Dispatches camera_metrics every ~1s     |
+------------------------------------------+
```

**Key behaviors:**
- **Decoupled frame buffer**: HTTP is never blocked by camera; old frames are silently overwritten if consumers are slow.
- **Auto-standby watchdog**: If no preview request arrives within `_preview_idle_timeout` (10s), the camera enters standby to avoid overheating/disconnection. An attached MJPEG viewer counts as a preview request on every poll, even while no frames are arriving.
- **Disconnect cascade**: 6 consecutive preview failures mark the camera disconnected, handing recovery to `init()`'s exponential backoff. (There used to be a "wedged-session heal" here — a 1-error hair trigger driving an `exit()+init()` cascade, to clear a wedge the M50 supposedly entered after any run that took a photo. That wedge was a **libgphoto2 2.5.34 bug**, not camera behavior; it does not happen on the system 2.5.30. Removed 2026-07-14 — see CAMERA_NOTES §2 and the `--no-binary gphoto2` rule in CONSTRAINTS.md.)
- **Capture job queue**: capture jobs are enqueued from the asyncio thread and executed by the worker thread (`CaptureRunner.run_pending()` at the top of every loop iteration) — one thread owns all camera USB I/O, no locks needed for the handoff.
- **Capture flow**: `enqueue_capture()` -> worker thread runs `CaptureRunner.execute()`, which emits granular `camera_job` SSE states (`started` -> `fired` -> `downloading` -> `completed`/`failed`) as it triggers the shutter and downloads the file.
- **Capture retry-once policy**: If a trigger or download attempt fails, `CaptureRunner` waits `CAPTURE_RETRY_DELAY_S` (1.5s), reconnects if needed, and retries exactly once before emitting a terminal `failed` event. This mirrors `PrintService`'s retry-once pattern and is a workflow decision — it must never live in the frontend (Rule 14). A shot that fails both attempts stays `failed`; the frontend surfaces a retry/home affordance rather than silently hanging.
- **Exponential backoff**: On init failure, waits `_init_backoff` seconds (doubles up to 60s) before retrying.

**Mock mode:** `camera/mock.py` provides `MockCameraService` — same API, generates synthetic frames for dev/Windows environments (selected via `config.json -> camera_backend: "mock"`).

**Hardware field knowledge:** [CAMERA_NOTES.md](CAMERA_NOTES.md) documents how this specific M50 body behaves (wedged sessions, stall cycles, widgets that must never be written, diagnostic log signatures). Read it before changing the camera package.

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

   bind_loop()                        <- called once at startup (lifespan)
     captures the event loop for cross-thread dispatch

   dispatch_event(event_type, data)   <- callable from ANY thread
     +-- on the event loop: put_nowait() into each client's
     |   asyncio.Queue(maxsize=100) directly
     +-- from a foreign thread (camera worker/monitor):
     |   marshalled via loop.call_soon_threadsafe()
     +-- before the loop is bound (import-time threads): dropped —
     |   nothing can be listening yet
     (drops silently if a client queue is full -- stale connections)

   send_to_client(client, ...)        <- seed one client (config on connect);
                                          same thread-safety contract

   event_iterator(client)             <- async generator for EventSourceResponse
     +-- pops from client queue, yields SSE payloads
         auto-removes client on disconnect or shutdown
```

**Thread-safety contract:** client queues are `asyncio.Queue`s, which are not thread-safe. `SseService` owns the marshalling — callers never need to know which thread they're on. This matters because the camera package dispatches from its worker and monitor threads.

**Events emitted:**

| Event | Emitted by | Content |
|---|---|---|
| `state_update` | `state_machine.py` | Full `BoothState` dict (snapshot taken under the FSM handler lock) |
| `camera_status` | `camera/` (device + preview/standby) | `{connected, is_capturing, error}` on connect/disconnect/standby/resume |
| `camera_metrics` | `camera/preview.py` (monitor thread, ~1s) | `{fps, latency_ms, time_since_last_frame_ms, connected, ...}` |
| `camera_job` | `camera/capture.py` (worker thread) | `{job_id, status, filename?, error?}` — status: `started`/`fired`/`downloading`/`completed`/`failed`, one or more per capture (retried attempts re-emit `fired`) |
| `printer_status` | `print_service.py` | `{connected, ready, status_text}` |
| `config_update` | `main.py` | Full `AppSettings` dict — broadcast on `POST /api/config` / PIN change, and seeded per-client on every SSE (re)connect |

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

### `settings.py` — Configuration
**Responsibility:** The `AppSettings` schema, atomic read/write of `config.json` at the
project root, and `SettingsService`, which owns the live settings for one process.

`SettingsService` is constructed and loaded by the composition root (`main.py`'s
lifespan) and reaches everything else by injection — routes via `deps.py`, services as
a constructor argument (`PrintService`, `JobQueue`) or a plain `AppSettings` parameter
(`storage`, `photo_processor`, `diagnostics`). There is no module-level settings global.

Memory is the source of truth; `config.json` is the persisted mirror. `update()`
rebinds rather than mutating, so an `AppSettings` snapshot already handed to a running
capture sequence cannot change under it. An unreadable `config.json` is quarantined and
the booth boots from defaults rather than failing to start.

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
| `capture_stall_timeout` | `75.0` | Seconds without shot progress in COUNTDOWN before the FSM's stall watchdog resets to ATTRACT |
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
| `restart_camera` | Returns `unsupported` — the camera reconnects automatically; the admin button surfaces that answer honestly |
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
| **CountdownScreen** | `CountdownScreen.jsx` | `COUNTDOWN` | Live MJPEG preview. Per-shot countdown. Fires each shot via `FIRE_SHOT`; completion is backend-owned (camera->FSM callback) and `camera_job` SSE events are presentation-only here (flash, thumbnail, failure overlay). Owns no retry, pacing, or completion logic (Rule 14). |
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
| `ProgressDots` | Shot progress indicator (1/3, 2/3, 3/3) |
| `PhotoFrame` | Image display with frame chrome |
| `ConfettiOverlay` | CSS confetti burst animation |
| `Toast` | Ephemeral notification banner |
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
- `resumePreview()` -> `POST /api/camera/resume`
- `standbyPreview()` -> `POST /api/camera/standby`

(Capture is not triggered here: the countdown fires the shutter through the
FSM via the `FIRE_SHOT` event, and completion returns camera->FSM by callback.)

#### `useApi.js`
Centralises all REST interactions:
- Fetches config on mount; caches in `configRef`
- `sendEvent(type, payload)` -> `POST /api/events`
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
               |    FIRE_SHOT x totalShots
               |    (completion: camera->FSM callback)
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
        |  api.sendEvent("FIRE_SHOT") -> FSM (guards one-shot-in-flight) calls
        |    camera_svc.enqueue_capture(on_complete=shot_completed, on_failure=shot_failed)
        |    -> worker thread runs _execute_capture_job()
        |  SSE: camera_job {job_id, status: "fired"}       -> flash/shutter sound
        |  SSE: camera_job {job_id, status: "downloading"}
        |  SSE: camera_job {job_id, status: "completed", filename}
        |      (on failure: CameraService retries once internally after
        |       CAPTURE_RETRY_DELAY_S before emitting a terminal "failed";
        |       the frontend never orchestrates the retry — Rule 14)
        |  camera.resumePreview()   -> POST /api/camera/resume
        v
camera worker -> FSM (run_coroutine_threadsafe): shot_completed(filename)
        |  (the camera_job SSE events above are presentation-only; the
        |   browser no longer reports the shot back)
        v
state_machine: capturedImages.append(filename)
        |  len(capturedImages) < totalShots?
        |    -> stay in COUNTDOWN, SSE state_update (capturedImages/totalShots)
        |    -> CountdownScreen shows "BETWEEN" interstitial for shot_interval_ms
        |       (backend config), then fires the next shot
        |  len(capturedImages) >= totalShots?
        |    -> state -> REVEAL, isProcessing=true
        |    -> job_queue.enqueue(jobs.process_photo_job(images, layout,
        |         settings, ...))   # text: jobs.compose_banner_text(settings)
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
        |  _enter_printing(): state -> PRINTING, printStatus = "printing",
        |                     finalPhoto = "photo_xyz456.jpg"
        |  job_queue.enqueue({type:"PRINT_PHOTO", filename:"photo_xyz456.jpg"})
        |  SSE (state_update) -> PrintingScreen projects printStatus
        v
job_queue: print_svc.print(filepath) -> lp -d <printer_name> ...
        |  success -> state_machine.job_print_done()   -> printStatus = "printed"
        |  failure -> state_machine.job_print_failed()  -> printStatus = "failed"
        |  SSE (state_update) -> PrintingScreen

PrintingScreen (pure projection of printStatus — never triggers the print):
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
camera/ (package)  ->   camera_status     -> Connect/disconnect/standby/resume
camera/preview.py  ->   camera_metrics    -> Periodic monitor thread (~1s)
camera/capture.py  ->   camera_job        -> Per capture, granular: started/fired/downloading/completed/failed
print_service.py   ->   printer_status    -> On print attempt
main.py            ->   config_update     -> Config/PIN change; also seeded per-client on every SSE (re)connect
```

Dispatch is thread-safe: `sse_svc.bind_loop()` runs at startup, and events emitted from camera threads are marshalled onto the event loop by `SseService` itself (see [sse_service.py](#sse_servicepy--server-sent-events)).

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
                    |  Every 1s: fps/latency    |
                    |  dispatch camera_metrics  |
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
SettingsService  (backend/settings.py — one instance, built by the lifespan)
     |
     +-- load():        read config.json into memory once, at startup
     +-- get():         the current AppSettings — memory only, never disk
     +-- update(dict):  merge -> validate -> REBIND in memory -> atomic write

Memory is the source of truth; config.json is where it is persisted
(write-temp + os.replace, fsync'd). update() rebinds a new AppSettings
instead of mutating, so an in-flight holder (e.g. the FSM mid-capture-
sequence) keeps the snapshot it started with. Hand-editing config.json
while the booth runs requires a restart.

AppSettings is a Pydantic BaseModel with defaults.
All settings are flat key-value except:
    overlays: List[OverlayConfig]   {id, name, filename}

Camera backend selection (camera/factory.py's create_camera, called once by
the lifespan — importing the factory picks nothing and opens nothing):
    if settings.camera_backend == "mock":
        return MockCameraService(sse)
    else:
        return CameraService(sse)   # or None if gphoto2 isn't installed
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
| `test_settings.py` | AppSettings read/write, corrupt-config fallback, SettingsService |
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
main.py                  (composition root, Rule 19 — the lifespan constructs
  |                       SettingsService, PrintService, JobQueue and the camera,
  |                       puts them on app.state, and wires the FSM)
  +-- logger.py          (no backend imports)
  +-- settings.py        <- logger.py
  +-- deps.py            <- settings.py, print_service.py  (FastAPI dependencies;
  |                         reads app.state, so routes never import a service)
  +-- storage.py         <- settings.py  (AppSettings passed in)
  +-- sse_service.py     <- logger.py
  +-- state_machine.py   <- logger.py, sse_service.py, settings.py, jobs.py
  +-- jobs.py            <- settings.py  (job payload builders — the
  |                         FSM<->job_queue payload schema in one place)
  +-- job_queue.py       <- logger.py, settings.py, photo_processor.py,
  |                         storage.py, print_service.py
  +-- camera/            (package — see §8; only device.py imports gphoto2)
  |     factory.py       <- settings.py, logger.py  (create_camera();
  |     |                   importing it must not pick or open a camera)
  |     service.py       <- device.py, preview.py, capture.py, gate.py (facade)
  |     device.py        <- logger.py
  |     preview.py       <- logger.py
  |     capture.py       <- logger.py, storage.py
  |     mock.py          <- logger.py, storage.py
  +-- photo_processor.py <- settings.py
  +-- print_service.py   <- settings.py, logger.py
  +-- diagnostics.py     <- settings.py, print_service.py

No module below main.py holds a service singleton. Every service — SSE, settings,
printer, job queue, camera, FSM — is constructed once in the lifespan and handed to
whoever needs it; routes receive them via deps.py. Modules import service *classes*
for type annotations, never instances. Importing any backend module has no side
effects: it constructs nothing and opens nothing. The only module-level singleton
left is the logger — Rule 19's sanctioned exception (see BACKEND_RULES).

state_machine <-> job_queue wiring (no import in either direction beyond
the above): main.py passes the queue to the FSM at construction. The FSM
enqueues jobs carrying its own bound methods as
on_success/on_failure callbacks; the queue invokes them blindly and
never imports the state machine. The camera package uses the same
inversion: enqueue_capture(on_complete, on_failure) delivers the capture
outcome straight to FSM-supplied callbacks without importing the FSM,
and the COUNTDOWN stall watchdog remains the floor for a dead browser
or a callback that never lands.
```

---

*Generated: 2026-06-29 · Updated: 2026-07-12 (SSE thread-safety, snapshot broadcasts, COUNTDOWN stall watchdog, corrected job_queue/state_machine dependency map)*

# CONTEXT — File Responsibilities

Every important file in the project, grouped by layer. Use this as a quick-reference map when navigating the codebase.

---

## Root

| File | Responsibility |
|---|---|
| [`config.json`](config.json) | Runtime configuration store (couple names, overlay, printer, timeouts). Read and written by the backend; the single source of truth for all operator settings. |
| [`run.sh`](run.sh) | Production startup script: builds the frontend, starts uvicorn in the background, then launches Chromium in kiosk mode pointing to `localhost:8000`. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Full architecture reference: modules, data flow, FSM diagram, deployment, and dependency map. |
| [`CONTEXT.md`](CONTEXT.md) | This file. One-liner responsibility for every important file. |
| [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) | Step-by-step guide for deploying the booth on a Raspberry Pi / Linux host. |

---

## Backend (`backend/`)

| File | Responsibility |
|---|---|
| [`main.py`](backend/main.py) | FastAPI app entry point. Declares all HTTP/SSE routes, runs the startup lifespan (init camera, start job queue), and serves the built frontend as static files. |
| [`state_machine.py`](backend/state_machine.py) | Pure event-driven FSM representing the booth's logical state (`BoothState`). Validates transitions, updates state, and broadcasts changes via SSE. Issues no hardware commands directly. |
| [`job_queue.py`](backend/job_queue.py) | Async work queue that offloads CPU-bound photo processing to a thread pool, then calls back into the state machine when done. Also triggers circular storage cleanup after each job. |
| [`camera_service.py`](backend/camera_service.py) | gphoto2 wrapper managing a dedicated background worker thread for live MJPEG preview (decoupled frame buffer) and shutter capture, with auto-standby watchdog, exponential-backoff reconnection, and a retry-once policy for a failed high-res capture. |
| [`mock_camera.py`](backend/mock_camera.py) | Drop-in replacement for `camera_service.py` that generates synthetic preview frames; used on Windows/dev when `camera_backend: "mock"` is set in config. |
| [`photo_processor.py`](backend/photo_processor.py) | Composites captured images into a 1800×1200 px canvas using Pillow: arranges single/collage layouts, blends RGBA overlay PNGs, renders Playfair Display branding text, and saves as JPEG. |
| [`print_service.py`](backend/print_service.py) | Abstracts printer hardware behind a `PrinterDriver` ABC with `CupsPrinterDriver` (Linux, `lp` CLI) and `MockPrinterDriver` (dev). The `PrintService` singleton adds retry logic, status caching, and structured logging. |
| [`sse_service.py`](backend/sse_service.py) | Fan-out Server-Sent Events service. Any backend module calls `sse_svc.dispatch_event()` to push state updates, camera status, and capture results to all connected browser clients simultaneously. |
| [`storage.py`](backend/storage.py) | Ensures `photos/` and `overlays/` directories exist, lists photos by recency, and enforces circular storage (deletes oldest files when count or disk-space limits are exceeded). |
| [`config.py`](backend/config.py) | Pydantic `AppSettings` model with `load_settings()`, `save_settings()`, and `update_settings()` helpers that read/write `config.json` at the project root. |
| [`logger.py`](backend/logger.py) | Structured JSONL logger (`BoothLogger` singleton `log`) writing to a fresh `logs/backend_<startup-timestamp>.log` (5 MB × 3) each process start, plus stdout. Also accepts pre-formatted frontend log lines for `logs/frontend_<startup-timestamp>.log`. |
| [`diagnostics.py`](backend/diagnostics.py) | Aggregates system health (printer status, disk usage, photo count) for the admin panel, and executes emergency actions (`restart_booth`, `restart_printer`, `clear_queue`) via systemd/CUPS CLI. |
| [`generate_sound.py`](backend/generate_sound.py) | One-off utility script to generate audio asset files (beeps, shutter click) used by the frontend sound system. |
| [`requirements.txt`](backend/requirements.txt) | Python dependencies: FastAPI, uvicorn, gphoto2, Pillow, pydantic, sse-starlette, qrcode. |
| [`PlayfairDisplay-Regular.ttf`](backend/PlayfairDisplay-Regular.ttf) | Embedded font file used by `photo_processor.py` to render branding text on finished photos. |

### `backend/overlays/`

| File | Responsibility |
|---|---|
| `frame_floral.png` | "Chic Blush Floral" decorative overlay — RGBA PNG composited over the finished photo canvas. |
| `frame_gold_elegant.png` | "Elegant Gold Frame" decorative overlay — RGBA PNG composited over the finished photo canvas. |

### `backend/tests/`

| File | Responsibility |
|---|---|
| [`conftest.py`](backend/tests/conftest.py) | Shared pytest fixtures: FastAPI `TestClient`, mock camera instance, temp directories, and pre-wired app overrides. |
| [`test_api.py`](backend/tests/test_api.py) | Happy-path integration tests for all REST endpoints. |
| [`test_api_errors.py`](backend/tests/test_api_errors.py) | Error-handling tests: 404, 400, 500 responses across endpoints. |
| [`test_camera.py`](backend/tests/test_camera.py) | Unit tests for `CameraService` initialization, standby, resume, status, and the capture retry-once policy (succeeds on retry / fails after retry). |
| [`test_camera_worker.py`](backend/tests/test_camera_worker.py) | Tests for the background worker thread: frame production, command queue processing, capture flow. |
| [`test_config.py`](backend/tests/test_config.py) | Tests for `load_settings`, `save_settings`, and `update_settings` round-trips. |
| [`test_job_queue.py`](backend/tests/test_job_queue.py) | Tests for job enqueue, worker dispatch, state machine callbacks, and cleanup task triggering. |
| [`test_photo_processor.py`](backend/tests/test_photo_processor.py) | Tests that `process_photo_layout` produces a valid JPEG file for single and collage layouts. |
| [`test_printer.py`](backend/tests/test_printer.py) | Tests for `PrintService` retry logic, mock driver, and CUPS driver status parsing. |
| [`test_sse.py`](backend/tests/test_sse.py) | Tests for SSE client registration, event dispatch, overflow drop behavior, and disconnect cleanup. |
| [`test_state_machine.py`](backend/tests/test_state_machine.py) | Tests that all FSM transitions accept valid events and reject invalid ones; verifies state field mutations. |
| [`test_storage.py`](backend/tests/test_storage.py) | Tests for `enforce_circular_storage` count eviction and disk-space eviction logic. |

---

## Frontend (`frontend/`)

| File | Responsibility |
|---|---|
| [`package.json`](frontend/package.json) | NPM manifest: declares React, Vite, and eslint dependencies; defines `dev` and `build` scripts. |
| [`vite.config.js`](frontend/vite.config.js) | Vite config: sets React plugin and proxies `/api/*`, `/photos/*`, `/overlays/*` to `localhost:8000` in dev mode. |
| [`index.html`](frontend/index.html) | HTML shell: single `<div id="root">` mount point and the Vite script entry. |
| [`eslint.config.js`](frontend/eslint.config.js) | ESLint rules for the frontend codebase (React hooks, JSX). |

### `frontend/src/`

| File | Responsibility |
|---|---|
| [`main.jsx`](frontend/src/main.jsx) | React entry point: mounts `<App />` into `#root`. |
| [`App.jsx`](frontend/src/App.jsx) | Root orchestrator: owns the screen router (driven by backend FSM state), inactivity timer, admin unlock gesture (5 taps), and all session event handlers. |
| [`design-tokens.css`](frontend/src/design-tokens.css) | CSS custom properties for the entire design system: color palette, spacing scale, typography, border-radii, and shadows. |
| [`global.css`](frontend/src/global.css) | CSS reset, base typography rules, and shared utility classes used across all screens. |
| [`animations.css`](frontend/src/animations.css) | All `@keyframes` definitions: fade-in/out, slide-up, pulse, flash, and confetti burst used throughout the UI. |
| [`App.css`](frontend/src/App.css) | Styles for the app shell, offline reconnecting overlay, and the hidden admin trigger button. |
| [`index.css`](frontend/src/index.css) | CSS entry point: imports `design-tokens`, `global`, and `animations` in the correct cascade order. |

### `frontend/src/screens/`

| File | Responsibility |
|---|---|
| [`AttractScreen.jsx`](frontend/src/screens/AttractScreen.jsx) | Idle/attract loop: displays welcome message, language picker (EN/FR), and the "Start" button that fires `START_SESSION`. |
| [`ChooseStyleScreen.jsx`](frontend/src/screens/ChooseStyleScreen.jsx) | Lets the guest pick Single photo or 3-photo Collage layout; fires `SELECT_LAYOUT` with the chosen mode. |
| [`CountdownScreen.jsx`](frontend/src/screens/CountdownScreen.jsx) | Shows the live MJPEG camera preview, runs per-shot countdowns, triggers captures via `captureFrame()`, and reports each completed shot via `SHOT_CAPTURED`. Owns no retry, pacing, or completion logic (Rule 14) — the backend retries a failed capture once and drives round pacing/advancement via `shot_interval_ms`/`capturedCount`. |
| [`RevealScreen.jsx`](frontend/src/screens/RevealScreen.jsx) | Displays the processed photo (or a spinner while `isProcessing`); offers Retake (up to limit) or Print actions. |
| [`PickFavoriteScreen.jsx`](frontend/src/screens/PickFavoriteScreen.jsx) | Shows thumbnails of all photos from multi-retake sessions so the guest can choose their favourite before printing. |
| [`FramePickerScreen.jsx`](frontend/src/screens/FramePickerScreen.jsx) | Presents available overlay frames side-by-side; fires `FRAME_SELECT` (with overlay id) or `FRAME_SKIP`. |
| [`PrintingScreen.jsx`](frontend/src/screens/PrintingScreen.jsx) | Triggers the print job, displays a QR code for the guest's phone download link, and offers "Finish" or "Take Another" actions. |
| [`DownloadScreen.jsx`](frontend/src/screens/DownloadScreen.jsx) | Minimal standalone page served at `/download/{filename}` for guests scanning the QR code on their phones. |

### `frontend/src/components/`

| File | Responsibility |
|---|---|
| [`Button.jsx`](frontend/src/components/Button.jsx) | Reusable styled button primitive with variant and size props. |
| [`ScreenShell.jsx`](frontend/src/components/ScreenShell.jsx) | Full-viewport container wrapper providing consistent padding, background, and entry animation for every screen. |
| [`CountdownRing.jsx`](frontend/src/components/CountdownRing.jsx) | SVG animated circular ring that visually counts down seconds for each shot. |
| [`ProgressDots.jsx`](frontend/src/components/ProgressDots.jsx) | Row of dots indicating how many shots have been taken out of the total (e.g. 2/3). |
| [`PhotoFrame.jsx`](frontend/src/components/PhotoFrame.jsx) | Image display component that wraps a `<img>` in a styled decorative frame chrome. |
| [`ConfettiOverlay.jsx`](frontend/src/components/ConfettiOverlay.jsx) | Full-screen confetti burst animation triggered on photo reveal. |
| [`Toast.jsx`](frontend/src/components/Toast.jsx) | Auto-dismissing notification banner for transient feedback messages. |
| [`AdminModal.jsx`](frontend/src/components/AdminModal.jsx) | PIN entry modal dialog: validates input before granting access to the Admin Panel. |

### `frontend/src/components/admin/`

| File | Responsibility |
|---|---|
| [`AdminPanel.jsx`](frontend/src/components/admin/AdminPanel.jsx) | Full-page operator control panel with tab navigation (Event, Booth, Camera, System); renders the correct tab component based on selection. |
| [`AdminPinGate.jsx`](frontend/src/components/admin/AdminPinGate.jsx) | PIN entry gate component: sits in front of `AdminPanel` and requires the correct PIN before revealing the controls. |
| [`controls/ToggleSwitch.jsx`](frontend/src/components/admin/controls/ToggleSwitch.jsx) | Reusable iOS-style toggle switch used within admin form fields. |
| [`tabs/EventTab.jsx`](frontend/src/components/admin/tabs/EventTab.jsx) | Admin tab for editing event-specific settings: couple names, event date, welcome/thank-you messages, banner text. |
| [`tabs/BoothTab.jsx`](frontend/src/components/admin/tabs/BoothTab.jsx) | Admin tab for booth behaviour: countdown duration, flash, retake limit, session timeout, overlay selection, names-on-photo toggle. |
| [`tabs/CameraTab.jsx`](frontend/src/components/admin/tabs/CameraTab.jsx) | Admin tab showing live camera connection status and allowing gphoto2 settings (ISO, aperture, shutter speed) to be adjusted. |
| [`tabs/SystemTab.jsx`](frontend/src/components/admin/tabs/SystemTab.jsx) | Admin tab for system operations: diagnostics (disk, printer), recent log viewer, emergency actions (restart, clear queue), and PIN change. |

### `frontend/src/hooks/`

| File | Responsibility |
|---|---|
| [`useSse.js`](frontend/src/hooks/useSse.js) | Opens and maintains the `EventSource` connection to `/api/sse`; parses `state_update`, `camera_status`, `config_update`, and `camera_job` events; auto-reconnects on error. |
| [`useCamera.js`](frontend/src/hooks/useCamera.js) | Provides `previewUrl` (MJPEG stream), `captureFrame()`, `standbyPreview()`, and `resumePreview()` — thin wrappers over the camera REST endpoints. |
| [`useApi.js`](frontend/src/hooks/useApi.js) | Centralises REST writes/reads: exposes `sendEvent`, `saveConfig`, `getQrUrl`, `getDownloadUrl`, `getDiagnostics`, `emergencyAction`, `changePin`, `getRecentLogs`, and `fetchState` (config arrives via SSE, not here). |

### `frontend/src/utils/`

| File | Responsibility |
|---|---|
| [`i18n.js`](frontend/src/utils/i18n.js) | English/French translation map with a `t(key, lang)` lookup helper used by every screen for bilingual support. |
| [`logger.js`](frontend/src/utils/logger.js) | Frontend structured logger: buffers JSONL entries in memory and periodically flushes them to `POST /api/logs` so they land in the current run's `logs/frontend_<startup-timestamp>.log`. |
| [`sounds.js`](frontend/src/utils/sounds.js) | Web Audio API sound effect helpers: countdown beep, shutter click, and success chime, with graceful no-op if audio is unavailable. |
| [`compliments.js`](frontend/src/utils/compliments.js) | Small array of compliment strings randomly shown on the RevealScreen to delight guests. |

---

## Logs (`logs/`)

| File | Responsibility |
|---|---|
| `backend_<startup-timestamp>.log` | Rotating JSONL log written by the backend (`logger.py`), one new file per process start. 5 MB × 3 backups within a run. Each line is a structured JSON object with `ts`, `level`, `module`, `event`, `msg`, and optional `data`. |
| `frontend_<startup-timestamp>.log` | Rotating JSONL log for frontend events, forwarded via `POST /api/logs` and written as-is by the backend logger. Same per-run naming and schema as the backend log, with `source: "frontend"`. |

---

*Generated: 2026-06-29*

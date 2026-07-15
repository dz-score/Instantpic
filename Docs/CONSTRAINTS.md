# CONSTRAINTS — Architectural Decisions, Design Rules & Constraints

Decisions that shape the codebase and must be respected when making changes.
Each entry explains **what the rule is**, **why it exists**, and **what breaks if you violate it**.

---

## Table of Contents

1. [Architecture Boundaries](#1-architecture-boundaries)
2. [State Machine Rules](#2-state-machine-rules)
3. [Camera Service Rules](#3-camera-service-rules)
4. [Concurrency & Threading Model](#4-concurrency--threading-model)
5. [Frontend–Backend Contract](#5-frontendbackend-contract)
6. [Photo Processing Constraints](#6-photo-processing-constraints)
7. [Storage Constraints](#7-storage-constraints)
8. [Configuration Constraints](#8-configuration-constraints)
9. [Logging Rules](#9-logging-rules)
10. [Print Service Rules](#10-print-service-rules)
11. [Security Constraints](#11-security-constraints)
12. [Deployment Constraints](#12-deployment-constraints)
13. [Testing Rules](#13-testing-rules)
14. [Frontend Design Rules](#14-frontend-design-rules)

---

## 1. Architecture Boundaries

### Rule: The state machine must stay hardware-free
The `StateMachine` class in `state_machine.py` is a **pure data/event-driven structure**. It must never directly call camera, printer, or any hardware API.

> **Why:** Coupling hardware commands inside the FSM makes it brittle, hard to test, and impossible to unit-test without real hardware. Hardware services manage themselves via watchdog timers or by observing state externally.

> **What breaks:** If you add `camera_svc.standby()` or `print_svc.print()` inside `handle_event()`, you introduce direct hardware coupling that can cause deadlocks, race conditions, and un-testable state transitions.

---

### Rule: Single-process, single-port deployment
The entire application (backend API + frontend static files) runs in **one uvicorn process on one port** (`8000`). There is no nginx, no CDN, no reverse proxy in production.

> **Why:** The booth runs on a Raspberry Pi with no internet. Minimal infrastructure means fewer failure points. FastAPI's `StaticFiles` mount is sufficient.

> **What breaks:** Adding a second process or separate port requires updating CORS config, `run.sh`, the Chromium kiosk URL, and all SSE/API URL handling in the frontend.

---

### Rule: No circular imports at module definition time
`state_machine.py` and `job_queue.py` import **neither each other nor any service module**. The FSM receives the queue through its constructor at the composition root, and the queue reports results through per-job `on_success`/`on_failure` callbacks the submitter supplies — it never learns who consumes them.

> **Why:** Python raises `ImportError` on circular imports at module load time. Constructor injection plus submitter-owned callbacks keeps both modules independently importable and independently testable.

> **What breaks:** Adding a `from backend.state_machine import ...` to `job_queue.py` (or vice versa) recreates the cycle and crashes uvicorn startup.

---

### Rule: Services are built once, at the composition root
Every service — `SettingsService`, `PrintService`, `JobQueue`, the camera, `SseService`, `StateMachine` — is constructed exactly once in `main.py`'s lifespan, handed its collaborators, and parked on `app.state`; routes reach them via `backend/deps.py` (BACKEND_RULES Rule 19). The only module-level singleton is the logger (`log`), the sanctioned exception.

> **Why:** One instance per process still holds (two camera workers or two print queues would be undefined behavior), but import-time singletons made services unreplaceable: tests had to monkeypatch module globals, and importing a module could grab a device handle.

> **What breaks:** Reaching a service through a module import instead of `deps.py` reintroduces the second wiring mechanism Rule 19 forbids — and it will silently target a *different instance* than the one the lifespan built.

---

## 2. State Machine Rules

### Rule: Only one valid transition per event per state
The `VALID_TRANSITIONS` dict is the authoritative list. Any event not in the list for the current screen is **silently dropped** (logged as a warning, not raised as an error).

> **Why:** The booth is a kiosk in a public setting. Stale browser state, network retries, or double-taps must never crash the FSM or corrupt state.

> **What breaks:** Raising an exception on invalid events would crash the handler, leaving `isProcessing=True` forever and the booth stuck.

---

### Rule: State broadcasts happen outside the asyncio lock
`broadcast_state()` is called **after** `_get_lock()` is released, not while holding it.

> **Why:** `sse_svc.dispatch_event()` puts items onto per-client `asyncio.Queue`s. If the lock is held during broadcast, any slow SSE consumer that causes the event loop to await could deadlock the FSM.

> **What breaks:** Moving the `await self.broadcast_state()` call inside the `async with self._get_lock():` block can cause deadlocks under load.

---

### Rule: `TIMEOUT` and `FINISH` are global events
These two events bypass `VALID_TRANSITIONS` and are accepted from **any** screen state.

> **Why:** `TIMEOUT` must always be able to reset the booth regardless of what screen is active. `FINISH` must always be reachable as an emergency exit.

> **What breaks:** Removing them from `GLOBAL_EVENTS` means a guest stuck on, say, `FRAME_PICKER` with a broken camera can never reset the booth via inactivity.

---

### Rule: `isProcessing` must always be cleared by job callbacks
The job queue is the **only** entity allowed to set `isProcessing=False`. The FSM sets it to `True` when the `shot_completed` camera callback completes the sequence (`capturedImages` reaches `totalShots`) and in `FRAME_SELECT`, then waits for `job_photo_processed()`, `job_frame_processed()`, or `job_failed()`.

> **Why:** If something else clears `isProcessing`, the frontend might proceed while the job is still running, showing a stale `finalPhoto`.

---

## 3. Camera Service Rules

### Rule: The preview worker thread owns the gphoto2 camera object
Only `_worker_thread` calls `camera.capture_preview()` or `camera.capture()`. The main asyncio thread **never** touches the gphoto2 camera object directly.

> **Why:** `python-gphoto2` is not thread-safe. Concurrent calls from different threads corrupt internal gphoto2 state and cause `CameraError` or segfaults.

> **What breaks:** Calling `camera.capture()` from an async route handler (in the asyncio thread) while the worker thread is also active causes undefined gphoto2 behavior.

---

### Rule: HTTP consumers never receive backpressure from the camera
The frame buffer (`_latest_frame`) is a **single slot** that the worker thread overwrites unconditionally. Old frames are silently discarded.

> **Why:** If HTTP consumers were slow (e.g., browser on a lossy WiFi connection), a backpressure queue would accumulate frames faster than they're consumed, eventually stalling the camera thread and blocking gphoto2.

> **What breaks:** Replacing the single-slot buffer with a bounded `Queue` reintroduces backpressure and will cause the camera worker to block when consumers are slow.

---

### Rule: Build python-gphoto2 from source — never install the wheel
`backend/requirements.txt` carries a **`--no-binary gphoto2`** line. Keep it. The python-gphoto2 **wheel** bundles its own **libgphoto2 2.5.34**, and that build stalls M50 live view: a ~3.0s dead preview grab every ~6s, forever. The preview worker holds the camera lock across each grab, so a stalled grab **blocks the shutter** and the guest's photo fires ~3s after the countdown hits zero. The system libgphoto2 (2.5.30, apt) has neither problem.

> **Why:** measured on this rig — same code, same camera, same 60s, only the library swapped. Bundled 2.5.34: 1693 frames, 28.2 fps, **10 stalls**. System 2.5.30: 3598 frames, 60.0 fps, **0 stalls**. App-shaped (`--contention --gap-s 6`): 2.5.34 blocked **3/14** shutters (mean lock wait 642 ms); 2.5.30 blocked **0/15** (mean 3 ms). The boot wedge is a 2.5.34 artifact too.

> **What breaks:** `pip install gphoto2` by hand, or dropping the `--no-binary` line, silently installs the wheel and every symptom returns — invisibly, until someone measures millisecond timings on real hardware. Verify with `python3 -c "import gphoto2 as gp; print(gp.gp_library_version(gp.GP_VERSION_VERBOSE)[0])"` — it must **not** print 2.5.34. Requires `libgphoto2-dev` + `pkg-config` (see DEPLOYMENT_GUIDE).

---

### Rule: The capture is authoritative over live view
`enqueue_capture()` sets `_capture_in_progress` **and** calls `standby()` **before** queuing the CAPTURE job. While that flag is set the worker starts no new preview grabs and `resume_preview()` is a no-op, so nothing can re-arm live-view polling between enqueue and the shutter. Do **not** "simplify" this back to a bare `standby()` — that flag is only advisory: `preview_generator()` calls `resume_preview()` on every preview-stream (re)connect and can un-park the worker.

> **Why:** live view and capture share one PTP/USB session and one lock. Keeping the worker parked across a capture means the shutter never queues behind an in-flight preview grab. This stands on its own — it is correct regardless of the (now-fixed) library stall.

> **What breaks:** Removing the gate lets a preview grab re-arm between enqueue and shutter, so the capture waits on the lock.

> **⚠️ SUPERSEDED (2026-07-14) — the shot-spacing half of this rule is DEAD.** This rule used to also mandate `shot_interval_ms + countdown_duration < ~5s`, on the theory that the M50 ran a free-running ~6s stall clock that only a real capture could reset, so shot N+1 had to fire inside the window shot N opened. **There is no such clock.** That was libgphoto2 2.5.34 (see the rule above). On 2.5.30 live view runs 60s+ at 60fps clean, and captures at 6s spacing block **0/15** shutters. **Shot spacing is now a free design choice** — set the countdown and the interval to whatever reads best.

---

### Rule: Camera init uses exponential backoff, not immediate retry
On failure, `_init_backoff` starts at 5 seconds and doubles up to a cap of 60 seconds.

> **Why:** The Canon M50 takes ~3 seconds to enumerate over USB after being connected. Hammering gphoto2 with rapid retries during this window worsens the error state and delays successful connection.

---

### Rule: Capture retry-once policy lives in CameraService, not the frontend
On a failed trigger or download, `_execute_capture_job()` waits `CAPTURE_RETRY_DELAY_S` (1.5s), reconnects if needed, and retries **exactly once** before emitting a terminal `failed` `camera_job` event. The frontend (`CountdownScreen.jsx`) only ever observes one `completed` or `failed` outcome per shot — it never counts attempts or schedules its own retry delay.

> **Why:** Retry count and backoff timing for hardware I/O are workflow/business decisions (Rule 14) and must be owned by the backend so every consumer (frontend, future integrations) sees the same behavior, not a client-specific reimplementation.

> **What breaks:** Reintroducing a retry loop in `CountdownScreen.jsx` (as before this fix) duplicates a decision the backend already makes, and desyncs UI feedback (flash/shutter-sound) from which physical attempt actually succeeded, since CameraService may itself have already retried once.

---

## 4. Concurrency & Threading Model

### Rule: Photo processing runs in a thread pool executor, not inline
`process_photo_layout()` is always called via `loop.run_in_executor(None, ...)` inside the job queue worker.

> **Why:** Pillow image operations (decode, resize, composite, save) are CPU-bound and can take 200–800 ms. Running them inline in the asyncio event loop would block all other requests (SSE keep-alives, health checks) for that duration.

> **What breaks:** Calling `process_photo_layout()` directly (without `run_in_executor`) in an `async` function blocks the entire event loop, making the SSE stream stall and the frontend appear frozen.

---

### Rule: All inter-thread communication goes through thread-safe primitives
- Camera commands: `queue.Queue` (`_cmd_queue`)
- Frame delivery: `threading.Condition` (`_frame_condition`)
- Standby gate: `threading.Event` (`_preview_allowed`)
- Shutdown signal: `threading.Event` (`_shutdown_event`)

> **Why:** The asyncio event loop and camera worker thread share no mutable state except through these primitives. This avoids race conditions without needing explicit locks on every access.

> **What breaks:** Accessing `camera_svc.connected` or `camera_svc._latest_frame` from the asyncio thread without coordination can yield torn reads.

---

### Rule: The asyncio lock in `StateMachine` is lazy-initialised
`_lock = None` at `__init__` time; `_get_lock()` creates it on first call inside a running event loop.

> **Why:** `asyncio.Lock()` must be created inside a running event loop. Creating it at module import time (before uvicorn starts the loop) raises `DeprecationWarning` on Python 3.10+ and `RuntimeError` on 3.12+.

> **What breaks:** Moving `self._lock = asyncio.Lock()` into `__init__` breaks test setups that import the module before starting an event loop.

---

## 5. Frontend–Backend Contract

### Rule: The frontend has no independent state machine
`App.jsx` renders whatever `appState.screen` says. The only source of truth for the current screen is the **backend FSM**, delivered via SSE. The frontend does not maintain its own screen transition logic.

> **Why:** Having two state machines (one in Python, one in React) would diverge under any failure condition (network hiccup, backend restart). A single authoritative FSM ensures the booth always has a consistent, recoverable state.

> **What breaks:** Adding `if/else` logic in `App.jsx` to advance the screen without sending an event to the backend creates a split-brain scenario where the frontend and backend disagree on the current state.

---

### Rule: All session-advancing actions go through `POST /api/events`
Screens must not call any endpoint that mutates state as a side-effect. The **only** way to advance the FSM is `api.sendEvent(type, payload)`.

> **Why:** This enforces a clean unidirectional data flow: frontend sends event → backend mutates state → SSE pushes new state → frontend re-renders.

---

### Rule: SSE is the delivery mechanism; REST is for queries and commands
- **SSE** (`/api/sse`): receives state, camera status, printer status, capture results
- **REST**: sends events, reads config, triggers prints, fetches diagnostics

Do not poll REST endpoints to track state. Do not put state mutation logic on GET endpoints.

---

### Rule: The frontend logs to the backend, not to the browser console alone
All `logger.info/warn/error()` calls in the frontend use `utils/logger.js`, which batches and forwards lines to `POST /api/logs`. `console.log` is only for debug-only output.

> **Why:** The booth runs in Chromium kiosk mode with no developer tools accessible to operators. All logs must be inspectable from the Admin Panel or SSH.

---

## 6. Photo Processing Constraints

### Rule: Canvas is always 1800×1200 px (6×4 inch at 300 DPI)
This is the fixed output resolution. It must not be changed without also updating overlay PNGs, layout coordinates, and printer options.

> **Why:** Dye-sublimation printers for 4×6 inch prints expect exactly this aspect ratio. All overlay frames are authored at 1800×1200.

---

### Rule: Overlays must be RGBA PNG at 1800×1200
Overlay files in `backend/overlays/` must have an **alpha channel**. They are composited using their own alpha as a mask (`canvas.paste(overlay, (0,0), overlay)`).

> **Why:** Without an alpha channel, the overlay would be a fully opaque rectangle that covers the entire photo.

> **What breaks:** Saving an overlay as RGB JPEG (no alpha) causes a crash in `photo_processor.py` at the `paste()` call.

---

### Rule: Images can be passed as base64 data URIs or as filenames
`decode_base64_image()` handles both cases. Filenames are resolved relative to `backend/photos/`. This dual-mode exists because:
- The **mock camera** generates synthetic base64 frames in JavaScript
- The **real gphoto2 camera** saves files to disk and passes filenames via SSE

> **What breaks:** Passing an arbitrary filesystem path (outside `photos/`) as a filename is a path traversal risk. All filenames must be bare names (no `/` or `..`).

---

### Rule: Output is always saved as JPEG quality=95
PNG output is not used for final photos (file size too large for printing workflows). JPEG at 95 is visually lossless for photographic content at this resolution.

---

## 7. Storage Constraints

### Rule: Photos are stored flat in `backend/photos/` — no subdirectories
All processed JPEGs land directly in `photos/`. There is no per-session folder, no date-based hierarchy.

> **Why:** Simplicity. The `enforce_circular_storage()` function uses a single glob and mtime sort. Subdirectories would complicate both the glob and the `StaticFiles` mount.

---

### Rule: Circular storage runs after every job, not on a schedule
Cleanup is triggered as a `asyncio.create_task()` immediately after each successful photo job.

> **Why:** A cron-style schedule could allow the disk to fill completely between runs if many sessions happen in rapid succession. Post-job cleanup bounds the overshoot to at most one extra photo.

---

### Rule: The `photos/` directory is served as public static files
Anyone with network access to the booth can enumerate `/photos/` filenames. There is no authentication on the `StaticFiles` mount.

> **Why:** Guests are expected to view and download their photos. The booth is on a private event WiFi network, not the public internet.

---

## 8. Configuration Constraints

### Rule: `config.json` is the only persistent config store
There is no database, no environment variables for runtime settings, and no `.env` file for app settings. Everything editable by the operator lives in `config.json`.

> **Why:** The booth must be configurable by non-technical operators via the Admin Panel UI. A single JSON file is easy to back up, inspect, and restore.

---

### Rule: `camera_backend` is read once at startup
The camera backend (`"gphoto2"` or `"mock"`) is chosen by `camera_factory.create_camera()`, which the lifespan calls exactly once. Changing this setting requires a server restart to take effect.

> **Why:** The gphoto2 import happens at module level inside `camera_service.py` and cannot be hot-swapped. This is a startup-time decision, not a runtime one.

---

### Rule: settings live in memory; `config.json` is persistence, not input
`SettingsService` (backend/settings.py) reads `config.json` once at startup and serves every later read from memory. `update()` rebinds a new `AppSettings` and writes the file atomically. Hand-editing `config.json` on a running booth has no effect until restart — the next admin save overwrites it.

> **Why:** One in-memory authority (BACKEND_RULES Rule 21). Admin edits still take effect immediately — every request asks the service, and long-running work (a capture sequence) deliberately holds the snapshot it started with so a mid-session save can't retime shots already underway.

---

## 9. Logging Rules

### Rule: All log calls use the structured `BoothLogger` API
```python
log.info("module_name", "event_slug", "Human readable message", data={...})
```
Never use `print()` for operational logging in production code. `print()` is acceptable only in `photo_processor.py` for developer-facing font download messages.

---

### Rule: Log events use `snake_case` slugs
`event` field examples: `camera_init_ok`, `capture_completed`, `job_error`, `config_updated`.

> **Why:** The Admin Panel log viewer and any future log analysis tooling rely on filterable, machine-readable event slugs.

---

### Rule: Frontend logs are forwarded to the backend, not kept in `localStorage`
The frontend `logger.js` flushes to `POST /api/logs` every 10 seconds or after 20 buffered lines. Log data is never written to `localStorage` or `sessionStorage`.

> **Why:** Kiosk mode clears browser state on restart. Persistent logs must live on the filesystem.

---

## 10. Print Service Rules

### Rule: The printer is selected by CUPS queue name in config, not by driver class
`printer_name: "mock"` → `MockPrinterDriver`. Any other string → `CupsPrinterDriver` with that name. No code changes are needed to swap printers.

> **Why:** Different events may use different printers (Epson, DNP, HiTi). The CUPS abstraction means the booth works with any printer that has a CUPS driver, without modifying application code.

---

### Rule: Print calls are synchronous (blocking) within the print service
`print_svc.print()` blocks the calling thread until the `lp` subprocess exits. It is called from the FastAPI async route via a normal `await`-free call.

> **Why:** Print jobs are fire-and-forget from the operator's perspective. The HTTP response only needs to confirm that the job was accepted by CUPS, which happens within the `lp` call duration (~50–200 ms).

> **Note:** If print times become a bottleneck, wrap `print_svc.print()` in `run_in_executor`.

---

## 11. Security Constraints

### Rule: The admin PIN is stored in plaintext in `config.json`
There is no hashing, no salting, and no bcrypt. The 6-character numeric PIN is stored as a plain string.

> **Why:** The booth operates on a local network with physical access control (locked venue). The PIN's threat model is preventing casual guest access to operator settings, not protecting against an attacker with filesystem access.

> **Risk:** Anyone with SSH access to the Pi or who can read `config.json` can see the PIN.

---

### Rule: CORS is set to `allow_origins=["*"]` in development
In production (frontend served by the same origin), CORS is effectively irrelevant. The wildcard CORS is acceptable because the booth is not exposed to the internet.

> **What breaks if changed:** Tightening CORS in a multi-origin dev setup (Vite on `:5173`, uvicorn on `:8000`) would block all API calls from the dev frontend.

---

### Rule: There is no authentication on any API endpoint
Any device on the same network can call `POST /api/events` or `POST /api/config`.

> **Why:** The booth is on a private event WiFi. Guests are expected to interact with the booth only via the kiosk UI. Adding auth would complicate the QR download flow for guests.

---

## 12. Deployment Constraints

### Rule: The frontend must be built before starting the backend in production
`run.sh` always runs `npm run build` before `uvicorn`. The backend serves `frontend/dist/`. If `dist/` doesn't exist, the root route returns a JSON message instead of the SPA.

> **What breaks:** Starting uvicorn before building the frontend means the booth screen shows `{"message": "FastAPI Photo Booth backend is running! Frontend is not built yet."}`.

---

### Rule: uvicorn must bind to `0.0.0.0`, not `127.0.0.1`
`--host 0.0.0.0` allows other devices on the local network (guests' phones) to reach `/download/{filename}` and the QR code endpoint.

> **What breaks:** `--host 127.0.0.1` means guests scanning QR codes can't download their photos — the URL resolves to the Pi's LAN IP but the server is only listening on loopback.

---

### Rule: Chromium must run in `--kiosk` mode with specific flags
The flags in `run.sh` disable: first-run dialogs, crash restore prompts, translation bars, pinch-to-zoom, and background networking. All are required for a clean kiosk experience.

> **What breaks:** Removing `--disable-pinch` allows guests to zoom out and see the OS desktop behind the booth UI. Removing `--noerrdialogs` allows browser crash dialogs to appear over the photo booth interface.

---

### Rule: gphoto2 requires udev rules for non-root USB access
On Linux, `/etc/udev/rules.d/` must have rules granting the booth user read/write access to the Canon M50 USB device. Without them, `camera.init()` raises a `CameraError` with "Could not claim the USB device".

---

## 13. Testing Rules

### Rule: Never use `git add .` or `git commit -a`
Only stage explicitly named files. The repo contains personal scratchpad files, WIP scripts, and environment configs that must not be committed.

---

### Rule: Do not write core application code before presenting an investigation report when debugging
When investigating a bug or checking logs, write and run standalone helper scripts to collect evidence first. Propose fixes only after presenting findings.

> **Why:** Rushing to fix things can introduce regressions. The safest approach is: investigate → report → align on fix → implement.

---

### Rule: Tests use the `TestClient` from `conftest.py`, not a real uvicorn server
All integration tests use FastAPI's synchronous `TestClient` with mocked camera and printer services. No test starts a real uvicorn process or connects to real hardware.

---

## 14. Frontend Design Rules

### Rule: Screen state is read from `appState.screen` — never set locally
No screen component calls `setState` to advance to the next screen. All transitions happen by sending events to the backend, which then pushes the new screen via SSE.

---

### Rule: CSS uses design tokens from `design-tokens.css`, not hardcoded values
All colours, spacing, radii, and font sizes must reference `var(--token-name)`. Hardcoded values like `color: #ff0000` or `padding: 12px` are not allowed in component stylesheets.

> **Why:** Consistent design system. Changing the event colour palette (e.g. from rose-gold to navy-gold for a different wedding) requires updating only `design-tokens.css`.

---

### Rule: Audio is handled via Web Audio API in `sounds.js`, not `<audio>` elements
Sound effects are generated programmatically (oscillator nodes) rather than loaded from audio files.

> **Why:** No audio asset files to manage or bundle. Works offline without any network requests. Sounds are generated synchronously on demand without preload delays.

---

### Rule: The inactivity timer is managed in `App.jsx`, not in individual screens
Only `App.jsx` sets and resets `inactivityTimer`. Screen components must not create their own independent timeout-to-home logic.

> **Why:** A single inactivity timer in the root component is the only way to guarantee it resets correctly on any user interaction, regardless of which screen is active.

---

*Generated: 2026-06-29*

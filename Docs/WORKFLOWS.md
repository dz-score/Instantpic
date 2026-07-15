# WORKFLOWS — User Flows & System Workflows

Step-by-step description of every meaningful flow in the photo booth, from guest interaction to hardware side-effects.

---

## Table of Contents

### User Flows
1. [Full Session — Single Photo](#1-full-session--single-photo)
2. [Full Session — Collage (3 photos)](#2-full-session--collage-3-photos)
3. [Retake Flow](#3-retake-flow)
4. [Multi-Retake + Pick Favourite](#4-multi-retake--pick-favourite)
5. [Frame Picker Flow](#5-frame-picker-flow)
6. [Guest Phone Download (QR)](#6-guest-phone-download-qr)
7. [Inactivity Timeout](#7-inactivity-timeout)
8. [Language Switch](#8-language-switch)

### Operator Flows
9. [Opening the Admin Panel](#9-opening-the-admin-panel)
10. [Changing Event Settings](#10-changing-event-settings)
11. [Changing the Overlay Frame](#11-changing-the-overlay-frame)
12. [Viewing Diagnostics & Logs](#12-viewing-diagnostics--logs)
13. [Emergency Actions](#13-emergency-actions)
14. [Changing the Admin PIN](#14-changing-the-admin-pin)

### System Workflows
15. [Boot & Startup Sequence](#15-boot--startup-sequence)
16. [Camera Preview Streaming](#16-camera-preview-streaming)
17. [Camera Capture Pipeline](#17-camera-capture-pipeline)
18. [Photo Processing Job](#18-photo-processing-job)
19. [Frame Re-processing Job](#19-frame-re-processing-job)
20. [Print Job](#20-print-job)
21. [SSE State Broadcast](#21-sse-state-broadcast)
22. [Circular Storage Cleanup](#22-circular-storage-cleanup)
23. [Graceful Shutdown](#23-graceful-shutdown)
24. [Frontend Reconnection](#24-frontend-reconnection)

---

## User Flows

---

### 1. Full Session — Single Photo

The simplest end-to-end guest journey.

```
[ATTRACT screen]
  Guest taps anywhere on screen
    → App.jsx: handleStart() → POST /api/events {type: "START_SESSION"}
    → Backend FSM: ATTRACT → CHOOSE_STYLE
    → SSE state_update → frontend renders ChooseStyleScreen

[CHOOSE_STYLE screen]
  Guest taps "Single Photo"
    → POST /api/events {type: "SELECT_LAYOUT", payload: {mode: "single"}}
    → FSM: CHOOSE_STYLE → COUNTDOWN (layoutMode="single")
    → SSE state_update → frontend renders CountdownScreen

[COUNTDOWN screen]
  Camera live preview starts (MJPEG stream at /api/camera/preview)
  Countdown ring animates (e.g. 5 → 4 → 3 → 2 → 1)
    → camera.standbyPreview() → POST /api/camera/standby  (pauses preview worker)
    → POST /api/events {type: "FIRE_SHOT"}  → FSM (one-shot-in-flight guard)
      calls camera_svc.enqueue_capture(on_complete=shot_completed, on_failure=shot_failed)
    → backend worker (_execute_capture_job): fires shutter, downloads, saves to photos/
    → SSE: camera_job {job_id, status: "fired"}       (flash + shutter sound)
    → SSE: camera_job {job_id, status: "downloading"}
    → SSE: camera_job {job_id, status: "completed", filename}
        (on failure, CameraService retries once internally after
         CAPTURE_RETRY_DELAY_S before giving up — the frontend never
         orchestrates the retry itself, per Rule 14; a shot that fails
         both attempts shows a "Try Again / Home" overlay instead)
    → camera.resumePreview()  → POST /api/camera/resume
  camera worker → FSM callback (run_coroutine_threadsafe): shot_completed(filename)
    → (the camera_job SSE events above are presentation-only — the browser
       does not report the shot back)
    → FSM: capturedImages=[filename], len(capturedImages) >= totalShots (1)
    → FSM composes banner text/overlay_id itself, state → REVEAL (isProcessing=true)
    → SSE state_update → frontend renders RevealScreen (spinner)

[REVEAL screen — processing]
  job_queue processes photo in thread pool:
    decode image → compose 1800×1200 canvas → save JPEG
    → state_machine.job_photo_processed(filename)
    → FSM: isProcessing=false, finalPhoto=filename
    → SSE state_update
  RevealScreen: spinner disappears, photo appears

  Guest taps "Print"
    → POST /api/events {type: "PRINT_FROM_REVEAL", overlays: [...]}
    → FSM: no overlays with id≠"none" → PRINTING (printStatus="printing"),
           enqueues PRINT_PHOTO job
    → SSE state_update → frontend renders PrintingScreen

[PRINTING screen]
  FSM owns the print — PrintingScreen only projects printStatus:
    → job_queue runs print_svc.print() → lp -d <printer_name> <file>
    → job_print_done/job_print_failed → printStatus "printed"/"failed"
    → SSE state_update → PrintingScreen shows success or error
  QR code displays: /api/qrcode?text=http://<LAN_IP>/download/<filename>
  Guest scans QR with phone → photo downloads to their camera roll

  Guest taps "Finish"
    → POST /api/events {type: "FINISH"}
    → FSM: PRINTING → ATTRACT (full state reset)
```

**Total steps:** ~8 taps for a guest.

---

### 2. Full Session — Collage (3 photos)

Same as Flow 1 but the FSM requires `totalShots=3` (`SHOTS_PER_LAYOUT["collage"]`),
so CountdownScreen's countdown/capture cycle repeats — driven entirely by
backend-confirmed `capturedCount`, not any frontend-owned counter.

```
[CHOOSE_STYLE] Guest taps "3-Photo Collage"
  → POST /api/events {type: "SELECT_LAYOUT", payload: {mode: "collage"}}
  → FSM: COUNTDOWN (layoutMode="collage", totalShots=3)

[COUNTDOWN screen — 3 shots]
  Shot 1:
    Countdown → standby → FIRE_SHOT → capture (camera_job: fired/downloading/completed) → resume
    shot_completed(f1) callback → FSM: capturedImages=[f1], 1 < totalShots
      → SSE state_update (capturedImages, totalShots)
      → CountdownScreen: ProgressDots shows ● ○ ○, "BETWEEN" interstitial for
        shot_interval_ms (backend config, default 3000ms), then fires next shot

  Shot 2:
    Countdown → standby → FIRE_SHOT → capture → resume
    shot_completed(f2) callback → FSM: capturedImages=[f1,f2], 2 < totalShots
      → ProgressDots shows ● ● ○, same BETWEEN pacing

  Shot 3:
    Countdown → standby → FIRE_SHOT → capture → resume
    shot_completed(f3) callback → FSM: capturedImages=[f1,f2,f3], 3 >= totalShots
      → ProgressDots shows ● ● ●
      → FSM composes banner text/overlay_id itself, state → REVEAL

  A shot that fails both of CameraService's attempts (trigger + one retry)
  never advances capturedImages; CountdownScreen shows a "Try Again / Home"
  overlay instead of silently hanging or skipping the shot.

[job_queue — collage layout]
  decode 3 images
  crop each to 540×720 (portrait 3:4)
  place side-by-side on 1800×1200 canvas with 45px gaps
  apply overlay → render text → save JPEG

[REVEAL → PRINTING]  (same as Flow 1)
```

---

### 3. Retake Flow

Guest is unhappy with the photo on the Reveal screen.

```
[REVEAL screen]
  Guest taps "Retake"
    → POST /api/events {type: "RETAKE"}
    → FSM:
        retakeCount++
        capturedImages = []
        finalPhoto = null
        screen → COUNTDOWN
    → SSE → CountdownScreen reloads

  Guest poses again → full capture cycle repeats
  New photo processed → REVEAL shows new result

  If retakeCount >= max_photos_per_session (config):
    RevealScreen hides the Retake button
    Guest can only proceed to Print
```

---

### 4. Multi-Retake + Pick Favourite

When the guest retook at least once, `allSessionPhotos` has > 1 entry.

```
[REVEAL screen — after 2nd (or later) retake is processed]
  Guest taps "Print"
    → POST /api/events {type: "PRINT_FROM_REVEAL", overlays: [...]}
    → FSM: allSessionPhotos.length > 1 → screen → PICK_FAVORITE
    → SSE → frontend renders PickFavoriteScreen

[PICK_FAVORITE screen]
  Thumbnails of all session photos displayed
  Guest taps their favourite

    → POST /api/events {
        type: "FAVORITE_SELECT",
        filename: "photo_abc.jpg",
        overlays: [...]
      }
    → FSM:
        finalPhoto = "photo_abc.jpg"
        capturedImages = rawImages from that session entry
        → _proceed_to_print_flow(overlays)

  Depending on overlays:
    No overlays → screen → PRINTING
    Overlays available → screen → FRAME_PICKER
```

---

### 5. Frame Picker Flow

Only shown when `config.overlays` contains at least one non-"none" entry.

```
[FRAME_PICKER screen]
  Shows the current finalPhoto with overlay previews side-by-side
  Guest taps an overlay frame (e.g. "Blush Floral")

    → POST /api/events {
        type: "FRAME_SELECT",
        overlay_id: "blush_floral",
        text: "Michael & Sarah · June 14, 2030"
      }
    → FSM: isProcessing=true → job_queue.enqueue(PROCESS_FRAME)
    → SSE → FramePickerScreen shows spinner

  job_queue re-processes with new overlay:
    decode original capturedImages
    composite canvas with blush_floral overlay
    save new JPEG (new filename)
    → state_machine.job_frame_processed(new_filename)
    → FSM: finalPhoto = new_filename, screen → PRINTING
    → SSE → PrintingScreen

  Guest taps "Skip" instead:
    → POST /api/events {type: "FRAME_SKIP"}
    → FSM: screen → PRINTING (no re-processing, uses existing finalPhoto)
```

---

### 6. Guest Phone Download (QR)

```
[PRINTING screen]
  PrintingScreen calls api.getDownloadUrl(filename):
    boothBaseUrl = http://<LAN_IP>:8000  (from /api/network-info)
    downloadUrl  = http://<LAN_IP>:8000/download/photo_abc.jpg

  PrintingScreen calls api.getQrUrl(downloadUrl):
    qrSrc = /api/qrcode?text=http://...

  Browser fetches /api/qrcode?text=...
    → backend generates QR PNG via qrcode library
    → returns StreamingResponse image/png
    → displayed on screen as <img>

  Guest opens phone camera, scans QR
    → phone browser hits http://<LAN_IP>:8000/download/photo_abc.jpg
    → backend: FileResponse with Content-Disposition: attachment
    → photo saved to phone camera roll

  Guest at home can also visit /download/photo_abc.jpg
    → DownloadScreen renders (SPA catches /download/* path)
    → shows photo with a download button
```

---

### 7. Inactivity Timeout

Fires when a guest walks away mid-session.

```
Any screen (except ATTRACT, DOWNLOAD, LOADING):
  App.jsx starts inactivityTimer on screen entry
  Timer duration = config.session_timeout seconds (default 120)

  On every pointerdown event anywhere on the window:
    timer is reset

  If timer expires with no interaction:
    logger.info('session_timeout')
    POST /api/events {type: "TIMEOUT"}
      → FSM: any screen → ATTRACT (full BoothState reset)
      → SSE state_update → AttractScreen renders

  Camera auto-standby (independent):
    camera preview watchdog fires if no preview request in 10s
    camera enters low-power standby automatically
    resumes when next /api/camera/preview request arrives
```

---

### 8. Language Switch

```
[ATTRACT screen]
  Language picker shows EN | FR buttons (local state in App.jsx)
  Guest taps "FR"
    → setLanguage("fr") — React local state only, no backend call
    → language prop propagates to all rendered screens
    → every t(key, "fr") call returns French strings from i18n.js
    → language persists for the entire session (reset on FINISH)
```

---

## Operator Flows

---

### 9. Opening the Admin Panel

```
Operator taps the "L'Étoile" watermark button 5 times within 2 seconds
  → adminTapCount reaches 5
  → setShowAdmin(true) → AdminPinGate renders over the current screen

Operator enters the 6-digit PIN
  → AdminPinGate compares against config.admin_pin (loaded from backend)
  → Correct PIN → AdminPanel renders (full-page overlay)
  → Wrong PIN   → error shake animation, counter resets

Operator taps "Close" or presses outside
  → setShowAdmin(false) → returns to normal booth screen
```

---

### 10. Changing Event Settings

```
[Admin Panel — Event tab]
  Fields: Couple Names, Event Date, Welcome Message,
          Thank-You Message, Show Names on Photo toggle

  Operator edits fields and taps "Save"
    → api.saveConfig({ couple_names: "...", event_date: "..." })
    → POST /api/config {body: updates}
    → backend: settings_svc.update() → merge → validate → rebind in memory → atomic write to config.json
    → response: full updated AppSettings
    → frontend: setConfig(data) — live update, no restart needed

  Effect on photos:
    Next time a shot sequence completes (shot_completed reaches totalShots),
    jobs.compose_banner_text(settings) reads the fresh
    couple_names + event_date as the banner text composited onto the canvas
```

---

### 11. Changing the Overlay Frame

```
[Admin Panel — Booth tab]
  Overlay selector dropdown: No Frame / Chic Blush Floral / Elegant Gold Frame

  Operator selects new overlay and saves
    → PATCH config.selected_overlay = "blush_floral"
    → POST /api/config
    → config.json updated

  Effect on next session:
    state_machine reads settings.selected_overlay itself when the shot
      sequence completes (CountdownScreen never passes overlay_id — Rule 14)
    FramePickerScreen uses config.overlays to show frame options
    photo_processor.py looks up overlay filename from config.overlays[]
      and composites it onto the canvas
```

---

### 12. Viewing Diagnostics & Logs

```
[Admin Panel — System tab]

  Diagnostics panel:
    Tapping "Refresh" → api.getDiagnostics()
      → GET /api/diagnostics
      → backend: check_printer() + check_storage()
      → returns {
          printer: {connected, ready, status_text, printer_name},
          storage: {total_gb, used_gb, free_gb, photo_count, max_photos}
        }
      → displayed in the System tab UI

  Log viewer:
    Tapping "Load Logs" → api.getRecentLogs(50, "both")
      → GET /api/logs/recent?count=50&source=both
      → backend: tail last 50 lines from the current run's backend_<ts>.log + frontend_<ts>.log
      → sort by timestamp descending
      → returns JSON array of log entries
      → displayed as a scrollable list, colour-coded by level
```

---

### 13. Emergency Actions

```
[Admin Panel — System tab]

  Operator taps an emergency button:

  "Restart Booth"
    → POST /api/emergency {action: "restart_booth"}
    → backend (Linux): systemctl restart chromium-kiosk + photobooth
    → backend (Windows): returns mock success

  "Restart Camera"
    → POST /api/emergency {action: "restart_camera"}
    → backend: returns signal-only success
    → the camera service auto-reconnects via its init backoff loop

  "Restart Printer"
    → POST /api/emergency {action: "restart_printer"}
    → backend: systemctl restart cups

  "Clear Print Queue"
    → POST /api/emergency {action: "clear_queue"}
    → backend: cancel -a (cancels all CUPS jobs)
```

---

### 14. Changing the Admin PIN

```
[Admin Panel — System tab]

  Operator fills "Current PIN" and "New PIN" fields, taps "Change PIN"
    → api.changePin(currentPin, newPin)
    → POST /api/change-pin {current_pin: "123456", new_pin: "999888"}

  Backend:
    settings_svc.get() → compare req.current_pin vs settings.admin_pin
    Mismatch → 403 Forbidden → frontend shows error
    Match    → settings_svc.update({admin_pin: "999888"})
               → config.json updated
               → log.info "config_pin_changed"
    → backend broadcasts config_update over SSE; the frontend's copy self-heals
```

---

## System Workflows

---

### 15. Boot & Startup Sequence

```
run.sh
  1. source backend/.venv/bin/activate
  2. cd frontend && npm run build
       → Vite bundles React SPA → frontend/dist/
  3. uvicorn backend.main:app --host 0.0.0.0 --port 8000 &

uvicorn loads main.py:
  4. FastAPI app created, CORS middleware added, routers registered
       (importing main.py constructs nothing — no camera, no settings)

  lifespan() startup — the composition root (BACKEND_RULES Rule 19):
  5. ensure_directories() → creates photos/ and overlays/ if absent
  6. log.info "system_boot"
  7. Services built in dependency order, each handed its collaborators:
       sse → settings (load() reads config.json once) → printer
           → job_queue → camera (create_camera factory) → state_machine
     All parked on app.state; routes reach them via backend/deps.py
  8. sse.bind_loop() → camera threads can now marshal SSE dispatches
  9. job_queue.start() → asyncio.Queue created, _worker task launched
 10. camera_svc.init() → gphoto2 detects camera, connects
       On success: _worker_thread starts, _monitor_thread starts
       On failure: exponential backoff, retries silently
 11. SIGINT/SIGTERM handlers registered

  4. chromium-browser --kiosk http://localhost:8000
 12. Chromium opens → GET / → FastAPI serves frontend/dist/index.html
 13. React mounts → useSse connects to /api/sse
       → backend seeds the new client with config_update over the stream
 14. useApi.fetchState() → GET /api/state → appState = {screen: "ATTRACT"}
 15. AttractScreen renders — booth is live
```

---

### 16. Camera Preview Streaming

```
CountdownScreen mounts
  → <img src="/api/camera/preview"> set in browser

Browser issues GET /api/camera/preview
  → main.py: StreamingResponse(camera_svc.preview_generator(), media_type="multipart/x-mixed-replace")

camera_svc.preview_generator():
  Loop:
    camera_svc._last_preview_request = time.monotonic()  (resets watchdog)
    wait on _frame_condition (Condition)                  (blocks until new frame)
    yield MJPEG boundary + frame bytes

[Background: _worker_thread]
  Loop:
    _preview_allowed.wait()                              (blocks if in standby)
    frame = camera.capture_preview()                     (gphoto2 call)
    _latest_frame = frame                                (overwrites stale frame)
    _frame_condition.notify_all()                        (wakes all consumers)
    check _cmd_queue for commands

[Background: _diagnostic_monitor]
  Every ~5s:
    if _last_preview_request > idle_timeout (10s):
      _cmd_queue.put("STANDBY")                         (auto-standby)
    else:
      sse_svc.dispatch_event("camera_status", {...})

If browser tab closes or navigates away:
  StreamingResponse generator exits → preview_generator returns
  No more preview requests → watchdog fires → camera enters standby
```

---

### 17. Camera Capture Pipeline

```
CountdownScreen: countdown hits 0
  1. camera.standbyPreview()
       → POST /api/camera/standby
       → camera_svc.standby()
       → _cmd_queue.put("STANDBY")
       → worker thread: _preview_allowed.clear() → preview loop pauses

  2. POST /api/events {type: "FIRE_SHOT"}
       → state_machine: _shot_in_flight guard, then
         camera_svc.enqueue_capture(on_complete=shot_completed,
                                    on_failure=shot_failed)
       → captures the running loop for the callbacks, generates job_id (uuid)
       → _cmd_queue.put(("CAPTURE", job_id, callbacks))

  [_worker_thread]
  3. dequeues ("CAPTURE", job_id) → _execute_capture_job(job_id)
  4. _attempt_capture(job_id):
       emits camera_job {job_id, status: "fired"}       (flash + shutter sound)
       camera.capture(...)                              (gphoto2 shutter)
       emits camera_job {job_id, status: "downloading"}
       camera.file_get(...) → save to backend/photos/capture_<job_id>.jpg
       returns None on success, or an error string on failure
  5. On error: sleep(CAPTURE_RETRY_DELAY_S=1.5s), reconnect if needed,
       retry _attempt_capture() exactly once (re-emits "fired"/"downloading")
  6. sse_svc.dispatch_event("camera_job", {
         job_id, status: "completed", filename: "capture_<job_id>.jpg"
       })
       — or, if both attempts failed: {job_id, status: "failed", error}

  [Terminal outcome — backend-owned]
  7. The worker thread delivers the outcome straight to the FSM via the
     callbacks supplied at enqueue_capture (run_coroutine_threadsafe):
     completed → state_machine.shot_completed(filename) appends the shot
     failed    → state_machine.shot_failed(error) releases the in-flight
                 guard; the UI shows its "Try Again / Home" overlay from the
                 presentational camera_job 'failed' SSE event (a retry
                 re-fires FIRE_SHOT — no automatic retry: CameraService
                 already retried once)
  8. camera.resumePreview()
       → POST /api/camera/resume
       → camera_svc.resume_preview()
       → _cmd_queue.put("RESUME")
       → worker thread: _preview_allowed.set() → preview loop resumes

  FSM (not CountdownScreen) decides whether more shots are needed —
  see Workflow 18.
```

---

### 18. Photo Processing Job

Triggered by the `shot_completed` camera callback that brings `capturedImages` up to `totalShots`.

```
state_machine.shot_completed(filename):   # camera worker → FSM callback
  1. state.capturedImages.append(filename)
  2. if len(capturedImages) < totalShots:
       broadcast state_update only — CountdownScreen paces the next shot
       via shot_interval_ms and fires again (see Workflow 17)
  3. else (sequence complete):
       state.screen = "REVEAL"
       state.isProcessing = true
       job_queue.enqueue({
         type: "PROCESS_PHOTO",
         images: capturedImages,                 # filenames, FSM-owned list
         layout: state.layoutMode,                # "single" | "collage"
         text: compose_banner_text(settings),     # composed in backend/jobs.py
         overlay_id: settings.selected_overlay or "none",
       })
  4. sse_svc broadcasts state_update → RevealScreen shows spinner

job_queue._worker() receives PROCESS_PHOTO:
  5. loop.run_in_executor(None, process_photo_layout, ...)
       [thread pool — does not block asyncio event loop]

  photo_processor.process_photo_layout():
  6.  Decode each image (base64 → PIL or filename → open from disk)
  7.  Create 1800×1200 canvas (cream #fdfbf7)
  8.  Layout:
        single  → fit 1440×960, paste at (180, 80)
        collage → fit 3× 540×720, paste at evenly spaced x positions
  9.  Load overlay PNG (if overlay_id ≠ "none")
        resize to 1800×1200 → composite with RGBA alpha mask
  10. Draw text with Playfair Display 52pt, centred in bottom margin
  11. canvas.save(photos/photo_<uuid10>.jpg, quality=95)
  12. return "photo_<uuid10>.jpg"

  13. asyncio.create_task(_run_cleanup())  (triggers storage cleanup)

  state_machine.job_photo_processed(filename, images):
  14. state.isProcessing = false
  15. state.finalPhoto = filename
  16. state.allSessionPhotos.append({filename, rawImages})
  17. sse_svc broadcasts state_update → RevealScreen hides spinner, shows photo
```

---

### 19. Frame Re-processing Job

Triggered by `FRAME_SELECT` event (guest chose a new overlay after reveal).

```
state_machine.handle_event("FRAME_SELECT", {overlay_id, text}):
  1. state.isProcessing = true
  2. job_queue.enqueue({
       type: "PROCESS_FRAME",
       images: state.capturedImages,   # original raw images
       layout: state.layoutMode,
       text: text,
       overlay_id: overlay_id          # new overlay chosen in FramePicker
     })
  3. SSE → FramePickerScreen shows spinner

job_queue._worker() receives PROCESS_FRAME:
  4. run_in_executor → process_photo_layout(...)
     Same pipeline as PROCESS_PHOTO but with the new overlay_id
     Produces a NEW filename (photo_<different_uuid>.jpg)

  state_machine.job_frame_processed(new_filename):
  5. state.isProcessing = false
  6. state.finalPhoto = new_filename
  7. _enter_printing(): state.screen = "PRINTING", printStatus = "printing",
     enqueues PRINT_PHOTO job
  8. SSE → PrintingScreen renders with new photo and QR code
```

---

### 20. Print Job

Printing is backend-owned workflow. The FSM starts it on entering PRINTING and
reports the outcome via `printStatus`; the UI never triggers the print or
guesses the result.

```
FSM enters PRINTING (_enter_printing):
  1. state.screen = "PRINTING", printStatus = "printing"
  2. job_queue.enqueue({type: "PRINT_PHOTO", filename: "photo_abc.jpg"})
       → SSE state_update → PrintingScreen shows "printing"

  job_queue worker (PRINT_PHOTO):
  3. filepath = PHOTOS_DIR / filename
  4. print_svc.print(filepath)  (run in thread pool — it blocks)

  PrintService.print():
  5. settings_svc.get() → printer_name  (holds the service, so a printer
       swapped in the admin panel takes effect on the next job, no restart)
  6. Select driver:
       printer_name == "mock" → MockPrinterDriver (returns immediate success)
       else → CupsPrinterDriver
  7. Validates file exists (returns failure PrintResult if not)

  CupsPrinterDriver.print_file():
  8. subprocess.run(["lp", "-d", printer_name, *options_flags, filepath])
  9. Captures stdout for job_id (e.g. "request id is Canon-123")
  10. Returns PrintResult {success: true, job_id: "Canon-123", duration_ms: ...}

  On failure:
  11. Retry once with backoff, then returns PrintResult {success: false, error}
      → job_print_failed() → printStatus = "failed"
      → SSE state_update → PrintingScreen shows error + QR fallback

  On success:
  12. job_print_done() → printStatus = "printed"
      → SSE state_update → PrintingScreen shows success + QR code
```

---

### 21. SSE State Broadcast

Every FSM transition follows this pattern.

```
state_machine.handle_event(event_type, payload):

  [Inside asyncio.Lock]
  1. Validate event against VALID_TRANSITIONS[current_screen]
  2. Mutate self._state fields

  [Outside lock — to avoid blocking]
  3. await self.broadcast_state()
       → state_dict = self._state.model_dump()
       → sse_svc.dispatch_event("state_update", state_dict)

  sse_service.dispatch_event():
  4. For each SseClient in _clients:
       payload = {id: uuid, event: "state_update", data: json.dumps(state_dict)}
       client.queue.put_nowait(payload)
       (dropped silently if queue is full — stale connection)

  event_iterator(client) [async generator]:
  5. asyncio.wait_for(client.queue.get(), timeout=1.0)
  6. yield payload → EventSourceResponse sends to browser

  useSse.js:
  7. eventSource.addEventListener("state_update", handler)
  8. setBackendState(JSON.parse(e.data))

  App.jsx:
  9. useEffect: setAppState(sse.backendState)
 10. currentScreen = appState.screen
 11. React re-renders the correct screen component
```

---

### 22. Circular Storage Cleanup

Runs automatically after every processed photo.

```
job_queue._worker() — after every successful job:
  asyncio.create_task(_run_cleanup())

_run_cleanup():
  await loop.run_in_executor(None, enforce_circular_storage)

enforce_circular_storage():
  1. Glob all *.jpg in backend/photos/
  2. Sort by mtime ascending (oldest first)

  Photo count check:
  3. len(files) > settings.max_photos?
       Delete oldest files until count <= max_photos
       Log each deletion

  Disk space check:
  4. shutil.disk_usage(PHOTOS_DIR) → free_gb
  5. free_gb < settings.disk_min_free_gb?
       Delete oldest files one-by-one, recompute free space after each
       Stop when free >= threshold or no files remain
       Log each deletion
```

---

### 23. Graceful Shutdown

```
Operator kills process (Ctrl+C / systemctl stop):
  SIGINT/SIGTERM handler (registered in lifespan):
  1. log.info "signal_shutdown"
  2. sse_svc.request_shutdown()
       → _shutdown = True
       → all event_iterator loops detect it and exit
       → all SSE clients disconnected cleanly

  3. camera_svc.shutdown()
       → _shutdown_event.set()
       → _worker_thread and _monitor_thread exit their loops
       → camera.exit() (releases gphoto2 connection)

  4. print_svc.shutdown()
       → any pending print threads are joined

  Lifespan yield returns:
  5. await job_queue.stop()
       → _shutdown = True
       → _worker_task.cancel()
       → await all pending _background_tasks

  6. uvicorn exits cleanly
```

---

### 24. Frontend Reconnection

```
useSse.js opens EventSource to /api/sse

If the backend goes offline (server restart, network blip):
  eventSource.onerror fires
  1. setIsOnline(false)
  2. eventSource.close()
  3. setTimeout(connect, 3000)  — retry after 3 seconds

useApi.js — isOnline is false:
  4. App.jsx renders offline overlay ("Reconnecting...")
  5. API calls still attempted (will fail and log errors)

Backend comes back online:
  6. connect() creates new EventSource
  7. eventSource.onopen → setIsOnline(true)
  8. Offline overlay hides

State recovery:
  9. useSse: first "state_update" SSE event re-syncs backendState
 10. useApi.fetchState() was called on mount; if backend restarted
     and FSM reset to ATTRACT, next SSE event will carry that state
```

---

*Generated: 2026-06-29*

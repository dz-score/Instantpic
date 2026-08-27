# Photo Booth API Protocol

## Overview

This document describes the command and event boundary between the React kiosk UI and the FastAPI backend. It defines what commands the UI can request, what data flows in each direction, and which session states allow each action.

The API is built on two communication patterns:

- **Commands**: Synchronous requests from UI → backend via HTTP POST to `/api/events`
- **Events**: Asynchronous updates pushed from backend → UI via Server-Sent Events (SSE) on `/api/sse`

The backend maintains a state machine that owns all workflow rules, transition logic, and session state. The UI is a reactive view layer that projects this state and collects user intent.

---

## State Machine Overview

The booth operates through a series of screen states. The backend's finite state machine validates all transitions and enforces the rules that govern which commands are allowed at each point.

### Screen States

| State | Meaning | What Happens Here |
|-------|---------|-------------------|
| `ATTRACT` | Idle loop | Kiosk displays attract loop waiting for someone to start |
| `CHOOSE_STYLE` | Layout selection | Guest chooses single photo or collage (3-photo) layout |
| `COUNTDOWN` | Capture sequence | Camera counts down, takes requested photos |
| `REVEAL` | Processing & preview | Photos are being processed; guest sees result and can retake or proceed |
| `PICK_FAVORITE` | Multi-photo selection | If multiple processed photos exist, guest picks their favorite |
| `FRAME_PICKER` | Overlay selection | Guest selects a frame/overlay for the final photo |
| `PRINTING` | Print workflow | Photo is being printed; booth displays status |

### State Transition Diagram

```
ATTRACT
  ↓ START_SESSION
CHOOSE_STYLE
  ↓ SELECT_LAYOUT
COUNTDOWN
  ↓ FIRE_SHOT (per shot; completion returns via camera→FSM callback)
REVEAL
  ├─ RETAKE → COUNTDOWN (cycle back)
  ├─ PRINT_FROM_REVEAL → PICK_FAVORITE (if multiple photos) OR FRAME_PICKER/PRINTING
  └─ (or TIMEOUT → ATTRACT)
PICK_FAVORITE
  ├─ FAVORITE_SELECT → FRAME_PICKER/PRINTING
  └─ (or TIMEOUT → ATTRACT)
FRAME_PICKER
  ├─ FRAME_SELECT → PRINTING (after processing)
  ├─ FRAME_SKIP → PRINTING (with default frame)
  └─ (or TIMEOUT → ATTRACT)
PRINTING
  ├─ REPRINT → PRINTING (retry, only while printStatus is "failed")
  ├─ ANOTHER → CHOOSE_STYLE (run again)
  ├─ FINISH → ATTRACT (return to idle)
  └─ (or TIMEOUT → ATTRACT)

TIMEOUT can be triggered from ANY state → ATTRACT
FINISH can be triggered from ANY state → ATTRACT
```

---

## Commands: UI → Backend

All commands are sent as HTTP POST to `/api/events` with JSON body:

```json
{
  "type": "<COMMAND_TYPE>",
  "payload": { /* command-specific data */ }
}
```

Response format (success):
```json
{
  "status": "ok"
}
```

Error responses include standard HTTP error codes and an error message.

### START_SESSION
**Valid from:** `ATTRACT` only  
**Payload:** `{}` (no data required)

Begins a new session. Transitions the booth to `CHOOSE_STYLE` where the guest can select a layout.

```json
{
  "type": "START_SESSION",
  "payload": {}
}
```

---

### SELECT_LAYOUT
**Valid from:** `CHOOSE_STYLE` only  
**Payload:**
- `mode` (string): `"single"` or `"collage"`

Confirms the guest's layout choice. The backend calculates `totalShots` based on the layout (1 for single, 3 for collage) and transitions to `COUNTDOWN`.

```json
{
  "type": "SELECT_LAYOUT",
  "payload": {
    "mode": "collage"
  }
}
```

---

### FIRE_SHOT
**Valid from:** `COUNTDOWN` only  
**Payload:** `{}` (no data required)

Fires the shutter for one shot. The FSM (guarding one shot in flight) asks the camera service to capture; the camera worker delivers the terminal outcome straight back to the FSM via callbacks — `shot_completed(filename)` appends the shot, `shot_failed(error)` releases the guard. The browser never reports the shot: the `camera_job` SSE events (`fired`/`downloading`/`completed`/`failed`) are presentation-only (flash, thumbnail, retry overlay).

The backend accumulates captured images until `totalShots` is reached, then automatically transitions to `REVEAL` and enqueues photo processing.

**Important:** The backend owns the decision of when the capture sequence is complete. The UI must not assume the sequence is done until receiving a state update showing `screen: "REVEAL"`. A `FIRE_SHOT` while a capture is already in flight is ignored.

```json
{
  "type": "FIRE_SHOT",
  "payload": {}
}
```

---

### RETAKE
**Valid from:** `REVEAL` only  
**Payload:** `{}` (no data required)

Guest rejects the captured photos and wants to try again. Increments `retakeCount`, clears captured images, and returns to `COUNTDOWN`.

```json
{
  "type": "RETAKE",
  "payload": {}
}
```

---

### PRINT_FROM_REVEAL
**Valid from:** `REVEAL` only  
**Payload:** `{}` (no data required)

Guest accepts the photos and wants to proceed toward printing. The backend decides the next screen based on configuration:
- If multiple processed photos exist in the session → `PICK_FAVORITE`
- Else if frame options are available → `FRAME_PICKER`
- Else → `PRINTING` (directly)

```json
{
  "type": "PRINT_FROM_REVEAL",
  "payload": {}
}
```

---

### FAVORITE_SELECT
**Valid from:** `PICK_FAVORITE` only  
**Payload:**
- `filename` (string): Filename of the chosen photo

Guest selects their favorite photo from multiple processed options. The backend records this as `finalPhoto` and proceeds to frame selection or printing.

```json
{
  "type": "FAVORITE_SELECT",
  "payload": {
    "filename": "collage_v2.jpg"
  }
}
```

---

### FRAME_SELECT
**Valid from:** `FRAME_PICKER` only  
**Payload:**
- `overlay_id` (string): ID of the selected overlay/frame (e.g., `"frame_gold"`)

Guest selects a frame overlay. The backend enqueues a photo processing job to apply the frame and transitions to `PRINTING` once processing completes.

```json
{
  "type": "FRAME_SELECT",
  "payload": {
    "overlay_id": "frame_gold"
  }
}
```

---

### FRAME_SKIP
**Valid from:** `FRAME_PICKER` only  
**Payload:** `{}` (no data required)

Guest skips frame selection and proceeds directly to printing with the default frame.

```json
{
  "type": "FRAME_SKIP",
  "payload": {}
}
```

---

### REPRINT
**Valid from:** `PRINTING`, and **only while `printStatus` is `"failed"`**  
**Payload:** `{}` (no data required)

Sends the same `finalPhoto` to the printer again after a print did not come out.
The booth returns to `printStatus: "printing"` and reports the second outcome the
same way it reported the first.

The `printStatus` condition is the point of this event, not an implementation
detail. A print that succeeded and a print that jammed look the same to a guest
standing at the booth, but retrying the first spends a second sheet of media on
a copy nobody agreed to — the booth prints once per session. Retrying the second
is the guest getting the photo they were already promised.

That condition also makes the event idempotent: the first `REPRINT` moves
`printStatus` to `"printing"`, so a double tap or a retried POST is refused
rather than queueing a duplicate behind the first. Rejections are logged
(`reprint_rejected`) and, like every invalid event, change nothing.

```json
{
  "type": "REPRINT",
  "payload": {}
}
```

---

### ANOTHER
**Valid from:** `PRINTING` only  
**Payload:** `{}` (no data required)

Guest has completed a session and wants to take another photo booth session. Resets to `CHOOSE_STYLE`.

```json
{
  "type": "ANOTHER",
  "payload": {}
}
```

---

### FINISH
**Valid from:** Any state  
**Payload:** `{}` (no data required)

Guest or system ends the session and returns to `ATTRACT`. Can be triggered at any time (e.g., if a guest walks away).

```json
{
  "type": "FINISH",
  "payload": {}
}
```

---

### TIMEOUT
**Valid from:** Any state  
**Payload:** `{}` (no data required)

System timeout (e.g., after 5 minutes of inactivity). Returns booth to `ATTRACT`.

```json
{
  "type": "TIMEOUT",
  "payload": {}
}
```

---

## Events: Backend → UI

The backend sends real-time events to connected UI clients via Server-Sent Events (SSE). The UI maintains an open connection to `/api/sse`.

Event format:
```json
{
  "id": "<UUID>",
  "event": "<EVENT_TYPE>",
  "data": "<JSON_STRINGIFIED_PAYLOAD>"
}
```

### state_update
**Pushed:** When the booth state changes (screen transition, shot accumulated, status update)

The complete current state of the booth:

```json
{
  "screen": "COUNTDOWN",
  "layoutMode": "collage",
  "totalShots": 3,
  "capturedImages": ["shot_001_captured.jpg", "shot_002_captured.jpg"],
  "finalPhoto": null,
  "retakeCount": 0,
  "allSessionPhotos": [],
  "isProcessing": false,
  "printStatus": "idle"
}
```

**Field Descriptions:**
- `screen` (string): Current screen state
- `layoutMode` (string): `"single"` or `"collage"`
- `totalShots` (integer): How many photos this layout requires
- `capturedImages` (array): Filenames of raw captured images
- `finalPhoto` (string or null): Filename of the processed/finalized photo
- `retakeCount` (integer): Number of times the guest has retaken photos
- `allSessionPhotos` (array): All processed photos from this session with their raw images
- `isProcessing` (boolean): True if a background job (photo processing, frame application) is running
- `printStatus` (string): `"idle"` | `"printing"` | `"printed"` | `"failed"`.
  `"printed"` means the print physically finished, not that CUPS accepted the
  job — the backend waits the job out before reporting either terminal value.

---

### config_update
**Pushed:** When configuration changes (admin updates settings) or on SSE connection (seed the client)

Contains the complete current application settings:

```json
{
  "printer_name": "Brother HL-L2350DW",
  "max_photos": 100,
  "disk_min_free_gb": 2.0,
  "couple_names": "Alex & Jamie",
  "event_date": "July 4, 2026",
  "default_text": "Happy Birthday",
  "selected_overlay": "frame_gold",
  "welcome_message": "Welcome to our photo booth!",
  "thank_you_message": "Thanks for the memories!",
  "countdown_duration": 3,
  "flash_enabled": true,
  "max_photos_per_session": 2,
  "session_timeout": 300,
  "show_names_on_photo": true,
  "wifi_network_name": "GuestNetwork",
  "camera_backend": "gphoto2",
  "overlays": [
    {
      "id": "none",
      "label": "No Frame",
      "image_url": "/overlays/none.png"
    },
    {
      "id": "frame_gold",
      "label": "Gold Frame",
      "image_url": "/overlays/frame_gold.png"
    }
  ],
  "admin_pin": "123456"
}
```

---

## Photo Capture & Processing Workflow

### Raw Capture (Backend-Owned)

The UI sends `FIRE_SHOT`; the camera service captures, writes the file to disk, and delivers the filename straight to the FSM via a completion callback. Filenames never pass through the browser, and the UI does **not** upload image data.

### Photo Processing (Async Job)

Once a complete capture sequence is finished (all `totalShots` captured), the backend automatically:

1. Enqueues a `PROCESS_PHOTO` job with:
   - List of raw image filenames
   - Layout mode (single or collage)
   - Banner text (composed by FSM from couple_names, event_date, etc.)
   - Selected overlay ID
   
2. Sets `isProcessing: true`

3. Processes the photo (compose, overlay, text) in a background worker

4. On completion, emits `state_update` with:
   - `isProcessing: false`
   - `finalPhoto: <processed_filename>`
   - `allSessionPhotos` array updated with the new processed photo

### Frame Selection Processing

If the guest reaches `FRAME_PICKER` and selects a frame:

1. Backend enqueues a `PROCESS_FRAME` job with the same raw images and the new overlay_id

2. Sets `isProcessing: true`

3. Processes and replaces the previous `finalPhoto`

4. On completion, transitions to `PRINTING`

---

## Configuration Management

### Get Current Config

**Endpoint:** `GET /api/config`  
**Response:** Full `AppSettings` object (see config_update event above)

Used to bootstrap the UI with current settings on page load or to refresh settings without waiting for SSE.

### Update Config

**Endpoint:** `POST /api/config`  
**Payload:** Partial update (any subset of fields)

```json
{
  "couple_names": "Alex & Jamie",
  "countdown_duration": 5,
  "welcome_message": "New welcome text!"
}
```

**Response:** Full updated `AppSettings`  
**Side Effect:** All connected clients receive a `config_update` SSE event

---

## Camera Control Endpoints

These endpoints are called by the UI to directly control the camera (preview, capture, settings).

### GET /api/camera/preview
**Returns:** Streaming MJPEG video feed from the camera

Used to display a live preview in the UI while waiting for capture.

### POST /api/camera/capture
**Returns:** `{ "status": "enqueued", "job_id": "<job_id>" }`

Enqueues a camera capture job. Responds immediately; the actual image capture happens asynchronously. The camera service writes the image to disk and reports completion via the job queue. The UI should receive a `state_update` when the file is ready.

### GET /api/camera/status
**Returns:**
```json
{
  "connected": true,
  "is_capturing": false,
  "error": null
}
```

Check if the camera is ready, currently capturing, or has an error.

### POST /api/camera/standby
Puts the camera into standby mode (lower power, faster to resume).

### POST /api/camera/resume
Brings the camera out of standby and resumes preview.

### GET /api/camera/config
**Returns:** Current camera settings (ISO, aperture, shutter speed, etc.)

USB round-trips occur under a lock to avoid interfering with the preview stream.

### POST /api/camera/config
**Payload:**
```json
{
  "settings": {
    "iso": "400",
    "aperture": "f/2.8"
  }
}
```

Updates camera hardware settings.

---

## Printer Status

### GET /api/printer/status
**Returns:**
```json
{
  "connected": true,
  "ready": true,
  "error": null,
  "media_available": true
}
```

Check printer connectivity and readiness. Queried during `PRINTING` state to display status.

---

## Photo Download (for Guests)

### GET /download/{filename}
Downloads a photo file. Used by guests scanning QR codes to retrieve their photos on their phones.

### GET /api/qrcode?text=<url>
Generates a QR code PNG dynamically for a given URL (e.g., a guest download link).

### GET /api/network-info
**Returns:**
```json
{
  "ip": "192.168.1.42",
  "port": 8000,
  "base_url": "http://192.168.1.42:8000"
}
```

Used by the UI to construct download URLs and QR codes.

---

## Logging & Diagnostics

### POST /api/logs
**Payload:**
```json
{
  "lines": [
    "{\"ts\":\"2026-07-08T10:30:00Z\",\"level\":\"info\",\"msg\":\"...\"}"
  ]
}
```

Frontend sends logs in JSONL format for centralized logging.

### GET /api/logs/recent?count=50&source=both
**Returns:** Array of recent log entries (backend, frontend, or both)

### GET /api/diagnostics
**Returns:** Full diagnostic snapshot (disk usage, camera status, printer status, system info, recent errors)

### POST /api/emergency
**Payload:**
```json
{
  "action": "restart_booth"  // or "restart_camera", "restart_printer", "clear_queue"
}
```

Emergency controls for operator. Restart or force-reset subsystems.

---

## Health & Session Management

### GET /api/health
**Returns:** `{ "status": "ok" }`

Heartbeat endpoint for connection monitoring.

### GET /api/state
**Returns:** Current `BoothState` (equivalent to latest `state_update` event)

One-shot fetch of the current state without waiting for SSE.

### POST /api/change-pin
**Payload:**
```json
{
  "current_pin": "123456",
  "new_pin": "654321"
}
```

Admin PIN change. Requires the correct current PIN.

---

## Example Session Flow

### Happy Path: Single Photo

```
1. UI polls GET /api/state → screen: "ATTRACT"

2. Guest taps "Start" → UI sends:
   POST /api/events
   { "type": "START_SESSION", "payload": {} }

3. Backend processes → emits state_update: screen: "CHOOSE_STYLE"

4. Guest selects "Single Photo" → UI sends:
   POST /api/events
   { "type": "SELECT_LAYOUT", "payload": { "mode": "single" } }

5. Backend processes → emits state_update: screen: "COUNTDOWN", totalShots: 1

6. UI starts countdown video from GET /api/camera/preview
   At T-0, UI sends: POST /api/events
   { "type": "FIRE_SHOT", "payload": {} }

7. Camera captures; its completion callback hands the filename to the FSM,
   which sees totalShots reached → 
   - Enqueues PROCESS_PHOTO job
   - Emits state_update: screen: "REVEAL", isProcessing: true

8. Background worker processes photo → 
   Backend emits state_update: isProcessing: false, finalPhoto: "single_0001.jpg"

9. Guest sees preview, taps "Print" → UI sends:
   POST /api/events
   { "type": "PRINT_FROM_REVEAL", "payload": {} }

10. Backend checks config → no frame options or already selected →
    Enqueues PRINT_PHOTO job → emits state_update: screen: "PRINTING", printStatus: "printing"

11. Printer completes → Backend emits state_update: printStatus: "printed"

12. Guest taps "Done" or timeout → UI sends:
    POST /api/events
    { "type": "FINISH", "payload": {} }

13. Backend resets → emits state_update: screen: "ATTRACT"
```

### Retake Scenario: Collage

```
1. Guest selects "Collage" (3 photos)

2. Three times, backend accumulates shots and broadcasts state_update

3. After 3rd shot: screen: "REVEAL", isProcessing: true

4. Guest sees result, dislikes it → taps "Retake" → UI sends:
   POST /api/events
   { "type": "RETAKE", "payload": {} }

5. Backend → retakeCount++, screen: "COUNTDOWN", capturedImages: []

6. Guest retakes sequence again... (loop back to step 2)
```

### Frame Selection Scenario

```
1. Guest accepts photos in REVEAL

2. Backend has multiple frame options in config → 
   emits state_update: screen: "FRAME_PICKER"

3. Guest selects frame → UI sends:
   POST /api/events
   { "type": "FRAME_SELECT", "payload": { "overlay_id": "frame_gold" } }

4. Backend → isProcessing: true, enqueues PROCESS_FRAME job

5. Frame processing completes → isProcessing: false, screen: "PRINTING"
```

---

## Error Handling & Edge Cases

### Invalid Transition
If the UI sends a command that's not valid for the current state, the backend:
- Logs a warning
- Ignores the command (no state change)
- Returns `{ "status": "ok" }` (does not error)

**Why:** The UI and backend can be out of sync if SSE updates are delayed. Rather than error, the backend silently ignores invalid commands and waits for the UI to catch up via the next state_update.

### Session Timeout
After `session_timeout` seconds (configurable, default 300), the backend should automatically emit:
```json
{
  "type": "TIMEOUT",
  "payload": {}
}
```

This can be implemented as a server-side timer or by the UI sending a TIMEOUT command. The backend transitions to ATTRACT regardless.

### Loss of Printer
If the printer disconnects while `printStatus: "printing"`, the backend:
- Detects the error in the print worker
- Calls `job_print_failed(error)`
- Emits state_update: `printStatus: "failed"`

The UI can then offer retry/skip/troubleshoot options. The booth does **not** automatically retry printing — the guest (or operator) must explicitly choose next steps.

### Photo Processing Failure
If a photo processing job fails:
- Backend calls `job_failed(error)`
- Emits state_update: `isProcessing: false`
- Stays in current screen (REVEAL, FRAME_PICKER, etc.)

The guest can retry by retaking or selecting a different frame.

---

## Rules of Engagement

1. **State is backend-owned.** The FSM is the single source of truth. The UI projects state but never updates it directly.

2. **Commands are validated.** Each command is valid only from certain states. Invalid commands are ignored.

3. **Processing is asynchronous.** Photo processing, printing, and camera capture happen in background workers. The UI waits for state_update notifications, not timeouts.

4. **Config is global.** Changes to config are broadcast to all connected clients via SSE.

5. **Filenames never pass through the UI.** Captured filenames travel camera → FSM via completion callbacks; the browser only ever *displays* them (from state and camera_job events).

6. **Print status is backend-reported.** The UI never guesses whether a print succeeded. It waits for the backend's printStatus update.

7. **Business logic lives in the backend.** Banner text composition, shot-count logic, overlay availability checks, and state transitions are all FSM rules.

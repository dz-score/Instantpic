# CAMERA_SEQUENCES — Startup & Countdown-to-Reveal Flows

Two sequence diagrams for onboarding: what the camera stack does when the
booth boots, and what happens from the guest tapping **single mode** until
their photo appears on the REVEAL screen. Diagrams are
[Mermaid](https://mermaid.js.org/) — GitHub and most IDEs render them
inline. Log event names (`like_this`) match what you'll grep in
`logs/backend_<ts>.log`.

Background on *why* the flows look like this (wedged sessions, stall
cycles, flush rules) lives in [CAMERA_NOTES.md](CAMERA_NOTES.md).
Component-level architecture is in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. App Startup

Covers both boot variants: after a **clean stop** (`stop.sh` ran
`camera.exit()`) the first session is healthy; after a **hard kill / power
cut** the first session is wedged and self-heals in ~12s (see
CAMERA_NOTES §2). Either way the camera ends up resting in standby until a
guest reaches a screen that needs live view.

```mermaid
sequenceDiagram
    autonumber
    participant R as run.sh
    participant L as main.py (lifespan)
    participant C as CameraService
    participant M as Canon M50 (PTP/USB)
    participant W as Worker thread
    participant K as Chromium kiosk

    R->>L: start uvicorn
    R->>K: launch kiosk (2s later)
    L->>C: init()   [blocking, before serving]
    C->>M: gp.Camera().init()  (~1.4s)
    Note over C,M: camera_init → camera_ready
    C->>M: set capturetarget='Internal RAM', autofocusdrive=0
    C->>M: read widgets (output, liveviewsize, …)
    Note over C: camera_widget_info ×5 (DEBUG)
    C->>M: warmup capture_preview()

    alt previous stop was clean (stop.sh)
        M-->>C: JPEG frame (~10ms)
        Note over C: camera_warmup ("Viewfinder pre-warmed")
        C->>W: start worker
    else previous stop was a hard kill → session wedged
        M-->>C: ~3s stall, then [-1] error
        Note over C: camera_warmup_fail (WARN)<br/>_warmup_failed = True
        C->>W: start worker
        loop 2 preview attempts (fail-fast threshold)
            W->>M: flush events + capture_preview()
            M-->>W: ~3.1s stall, [-1]
            Note over W: worker_preview_err #1, #2
        end
        Note over W: camera_preview_fail → connected=False<br/>(watchdog standby is DEFERRED while _warmup_failed)
        W->>C: init()  [the heal]
        C->>M: exit() old handle  ← this resets the camera
        C->>M: fresh gp.Camera().init() + config + warmup
        M-->>C: JPEG frame
        Note over C: camera_warmup OK, _warmup_failed=False<br/>~12s after boot, guest hasn't tapped yet
    end

    W->>M: preview polling (~15fps, nobody watching)
    Note over W: 10s with no preview request
    W->>W: idle watchdog → standby()
    Note over W: camera_watchdog → camera_standby<br/>camera rests until a screen needs live view
    K->>L: load ATTRACT screen, open SSE
```

**Key takeaways for a new dev:**

- `init()` runs in the FastAPI lifespan **before** the server accepts
  requests — the kiosk may retry for a couple of seconds on a slow boot.
- A failing warmup preview is **expected** after a power cut. Don't "fix"
  it: the fail-fast + re-init heal is deliberate, and shortcuts (in-place
  session rebuilds) are proven to poison the camera (CAMERA_NOTES §1).
- The worker thread owns **all** camera USB I/O from here on. Nothing else
  talks to the M50 directly.
- Idle rest is the steady state: on the ATTRACT screen the camera is in
  standby, not streaming.

---

## 2. Countdown → Capture → Reveal (single mode)

Starts where the guest taps single mode on CHOOSE_STYLE. The countdown
screen is the **only** screen that mounts the MJPEG live view.

```mermaid
sequenceDiagram
    autonumber
    actor G as Guest
    participant F as CountdownScreen (frontend)
    participant A as FastAPI (API + FSM)
    participant P as preview_generator (async)
    participant W as Worker thread
    participant M as Canon M50
    participant S as SSE (events to frontend)

    G->>F: taps "single mode"
    F->>A: POST event SELECT_LAYOUT
    A-->>F: FSM → COUNTDOWN, CountdownScreen mounts

    rect rgb(230, 240, 255)
    Note over F,M: — live view phase —
    F->>A: POST /api/camera/resume  (on mount)
    F->>P: mounts MJPEG img tag → GET /api/camera/preview
    P->>W: resume_preview() — sets _preview_allowed
    Note over P: refreshes _last_preview_request every 0.5s poll<br/>(a viewer counts even if frames stop)
    loop ~15fps while countdown ring plays (3…2…1)
        W->>M: flush events, capture_preview()
        M-->>W: JPEG frame → shared buffer
        P-->>F: MJPEG frame (guest sees themselves)
    end
    end

    rect rgb(255, 245, 225)
    Note over F,M: — pose gap: live view stops BEFORE the shutter —
    F->>F: count reaches 0 → phase "POSE!"
    F->>A: POST /api/camera/standby
    A->>W: standby() — clears _preview_allowed
    Note over W: worker stops polling, USB bus drains (~250ms+)
    end

    rect rgb(230, 255, 230)
    Note over F,M: — capture phase (all on the worker thread) —
    F->>A: POST /api/camera/capture
    A->>W: enqueue_capture() → set _capture_in_progress + standby, THEN queue CAPTURE
    Note over W: gate: no new preview grab, and resume_preview() is a no-op,<br/>until the shot completes (nothing can re-arm polling)
    A-->>F: job_id
    S-->>F: camera_job: pending → started
    Note over W: PREVIEW_RELEASE_SETTLE_S (~15ms) idle — let live view release
    W->>M: flush leftover events (~15ms)
    S-->>F: camera_job: fired
    Note over F: flash + shutter sound<br/>(deferred until the ring video finished —<br/>slightly LEADS the real exposure)
    W->>M: capture(GP_CAPTURE_IMAGE)  — blocks ~1.5s
    M-->>W: file path (image in camera RAM)
    W->>M: flush again (drains FILE_ADDED etc.)
    S-->>F: camera_job: downloading
    W->>M: file_get() from RAM (~5ms)
    W->>W: save photos/capture_<job>.jpg
    S-->>F: camera_job: completed (filename)
    end

    F->>A: POST event SHOT_CAPTURED
    A-->>F: FSM → REVEAL (single mode: 1/1 shots done)
    Note over A: job queue runs PROCESS_PHOTO (overlay/layout)
    Note over W: worker stays in standby through REVEAL —<br/>camera rests while guest views the photo
    Note over P: abandoned MJPEG stream self-closes<br/>after 30s without frames
    G->>F: (RETAKE loops back to the live view phase —<br/>no re-init needed between shots)
```

**Key takeaways for a new dev:**

- **The FSM never touches the camera.** The frontend calls camera endpoints
  and reports `SHOT_CAPTURED`; the backend owns retries and job state — the
  frontend only ever sees one terminal `completed`/`failed` per shot.
- **Live view and capture never overlap.** From `enqueue_capture()` the
  capture is authoritative — `_capture_in_progress` blocks new preview grabs
  and makes `resume_preview()` a no-op, so nothing can re-arm polling before
  the shutter (CAMERA_NOTES §6). Combined with a **≤5s countdown** — so the
  shutter lands in the fresh ~6s live-view window (§3) — captures no longer
  ride into the periodic stall.
- **Failure is handled below the UI**: a failed trigger/download is retried
  once inside `_execute_capture_job()` (new shot, ~1.5s later). The guest
  sees the error screen only if both attempts fail.
- **Nothing resumes preview automatically** after a capture. Live view
  returns when a screen that needs it mounts (retake / next session), which
  is what lets the camera rest during REVEAL.

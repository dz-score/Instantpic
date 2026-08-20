# Gravity Booth

A self-contained wedding photo booth. A FastAPI backend drives a Canon M50 over gphoto2, composites the shots into a printable layout, sends them to a dye-sub printer via CUPS, and serves a React kiosk UI — all from a single uvicorn process on a Raspberry Pi (or any Linux host).

## How it works

The backend owns a finite state machine; the frontend is a pure projection of it. The browser sends events (`START_SESSION`, `SELECT_LAYOUT`, `FIRE_SHOT`, …) over REST and receives every state change back over SSE. It runs no state machine, no retry logic, and no pacing of its own.

```
Guest taps screen → POST /api/events → FSM transition → SSE state_update → screen renders
```

A session runs: **ATTRACT → CHOOSE_STYLE → COUNTDOWN → REVEAL → (PICK_FAVORITE) → (FRAME_PICKER) → PRINTING → ATTRACT**.

Blocking work (Pillow compositing, shelling out to `lp`) goes to an async job queue backed by a thread pool. The camera runs its own worker thread with a decoupled frame buffer, so live MJPEG preview never blocks the event loop and slow HTTP consumers never stall the shutter.

## Layout

| Path | What it is |
|---|---|
| `backend/` | FastAPI app. `main.py` is the composition root — it constructs every service and wires the FSM. |
| `backend/routers/` | HTTP routes (booth, camera, config, logs, photos, sse, system). |
| `backend/tests/` | pytest suite covering the FSM, camera worker, job queue, settings, printing, storage. |
| `frontend/` | React + Vite SPA. Eight screens under `src/screens/`, three hooks (`useSse`, `useCamera`, `useApi`). |
| `Docs/` | Architecture reference, API protocol, backend/frontend rules, camera field notes, deployment guide. |
| `config.json` | Operator settings (couple names, overlay, printer, timeouts). Read and written at runtime. |
| `led-node/` | ESP-IDF firmware for a companion LED node. Work in progress, not yet tracked in git. |

## Running it

**Development (Windows or Linux):**

```bash
# Backend — set "camera_backend": "mock" in config.json when no camera is attached
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000

# Frontend, separate terminal — Vite dev server on :5173, proxies /api and /photos to :8000
cd frontend && npm install && npm run dev
```

**Production (Raspberry Pi):** `./run.sh` builds the frontend, starts uvicorn on `0.0.0.0:8000`, and opens Chromium in kiosk mode. `./stop.sh` tears it down. See [Docs/DEPLOYMENT_GUIDE.md](Docs/DEPLOYMENT_GUIDE.md).

**Tests:** `pytest` from the project root.

## Hardware notes

- **gphoto2 must be built from source against the system libgphoto2.** The `--no-binary gphoto2` line in `backend/requirements.txt` is load-bearing: the bundled 2.5.34 wheel stalls M50 live view (~3s dead grab every 6s) and blocks the shutter mid-countdown. Requires `libgphoto2-dev` and `pkg-config`.
- With no camera or printer attached, set `camera_backend: "mock"` and `printer_name: "mock"` — both have drop-in mock drivers.
- [Docs/CAMERA_NOTES.md](Docs/CAMERA_NOTES.md) records how this specific M50 body behaves. Read it before touching `camera_service.py`.

## Admin

Five rapid taps on the "L'Etoile" watermark opens a PIN-gated panel for settings, diagnostics, recent logs, and emergency actions (restart booth/camera/printer, clear the print queue). The PIN lives in `config.json` in plaintext — this is a LAN-only kiosk appliance, not an internet-facing service.

## Further reading

[Docs/ARCHITECTURE.md](Docs/ARCHITECTURE.md) is the full reference: module-by-module breakdown, FSM diagram, a complete session walkthrough, and the dependency map. [Docs/BACKEND_RULES.md](Docs/BACKEND_RULES.md) and [Docs/FRONTEND_RULES.md](Docs/FRONTEND_RULES.md) hold the invariants — notably that workflow decisions belong to the backend, never the browser.

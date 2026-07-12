---
name: verify
description: How to launch and drive the photo booth backend for end-to-end verification on this Windows dev machine (mock camera, SSE observation, FSM flow).
---

# Verifying the booth backend (Windows dev box)

## Launch

```bash
# Backup config first — the server reads/writes the real config.json at repo root
cp config.json config.json.verify-backup
# Edit config.json: set "camera_backend": "mock" (+ any timeouts you want short,
# e.g. "capture_stall_timeout": 6). Restore the backup when done.

./backend/venv/Scripts/python.exe -m uvicorn backend.main:app --port 8123 --log-level warning
# run in background; ~3s to ready; health check: GET /api/health
```

Use a non-default port (8123) — the user's real booth/dev server may hold 8000.

## Drive

- FSM flow: `POST /api/events` with `{"type": ..., "payload": {...}}` — exactly what the frontend sends. Sequence: START_SESSION → SELECT_LAYOUT {mode: single|collage} → SHOT_CAPTURED {filename} (×totalShots) → PRINT_FROM_REVEAL → FRAME_SELECT {overlay_id} or FRAME_SKIP → FINISH.
- State: `GET /api/state`.
- SSE observation: `curl -sN http://127.0.0.1:8123/api/sse > sse.log &` — first event is always a seeded `config_update`; then grep `event:`/`data:` lines. This is the ground truth for broadcast order.
- SHOT_CAPTURED needs a real JPEG in `backend/photos/` (photo_processor opens it): create one with PIL via `backend.storage.PHOTOS_DIR`. Processed `photo_*.jpg` outputs appear there too — delete the ones you created.
- Mock printer (`printer_name: "mock"`) completes prints instantly; `printStatus` goes printing → printed via the job queue.

## Gotchas

- **Tests never run the lifespan** — the `client` fixture uses `TestClient(app)` without a context manager. Startup-time bugs only show when the real server boots. There is one lifespan test (`test_lifespan_startup_and_shutdown`); keep it passing.
- **MockCameraService has no `enqueue_capture`/`standby`/`resume_preview`** — `POST /api/camera/capture` 500s in mock mode. Capture flow must be driven by posting SHOT_CAPTURED directly; the camera_job SSE path and the off-thread camera_metrics dispatch are only observable with real gphoto2 hardware.
- Kill the server via the PID on the port (`netstat -ano | grep :8123`, `taskkill //F //PID <pid>`); the SSE curl dies with it.
- The user's editor touches file mtimes without changing content — a "file modified since read" error usually just needs a re-read.

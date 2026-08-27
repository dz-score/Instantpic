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

- FSM flow: `POST /api/events` with `{"type": ..., "payload": {...}}` — exactly what the frontend sends. Sequence: START_SESSION → SELECT_LAYOUT {mode: single|collage} → FIRE_SHOT (×totalShots; the mock camera completes each in ~1s via backend callback — wait ~2s between shots) → PRINT_FROM_REVEAL → FRAME_SELECT {overlay_id} or FRAME_SKIP → FINISH.
- State: `GET /api/state`.
- SSE observation: `curl -sN http://127.0.0.1:8123/api/sse > sse.log &` — first event is always a seeded `config_update`; then grep `event:`/`data:` lines. This is the ground truth for broadcast order.
- FIRE_SHOT makes the mock camera write its own decodable JPEG to `backend/photos/` (`capture_<id>_mock.jpg`), which then flows through processing. Clean up both the mock captures and the processed `photo_*.jpg` outputs afterwards.
- Mock printer (`printer_name: "mock"`) completes prints instantly; `printStatus` goes printing → printed via the job queue.

## Gotchas

- **Tests do run the lifespan** — the `client` fixture uses `TestClient(app)` as a context manager, so the real composition root builds every service. `test_lifespan_startup_and_shutdown` covers the boot/teardown path explicitly; keep it passing.
- **MockCameraService has full capture parity** (`enqueue_capture` with FSM callbacks, `standby`, `resume_preview`) — the whole FIRE_SHOT flow, including the cross-thread camera→FSM callback, is drivable in mock mode. Only `camera_metrics` (monitor thread) remains real-hardware-only.
- Kill the server via the PID on the port (`netstat -ano | grep :8123`, `taskkill //F //PID <pid>`); the SSE curl dies with it.
- The user's editor touches file mtimes without changing content — a "file modified since read" error usually just needs a re-read.

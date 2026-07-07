#!/bin/bash
# Clean booth stop — the counterpart to run.sh.
#
# SIGTERM (not SIGKILL) matters here: backend/main.py traps it and calls
# camera.exit(), which closes the M50's PTP session cleanly. A hard kill
# leaves stale live-view state on the camera and the next launch pays a
# ~12s wedged-session heal before previews work.

# Close the kiosk first so its MJPEG/SSE connections drop.
pkill -f chromium 2>/dev/null

# Ask the backend to shut down cleanly and wait for it.
if ! pgrep -f "uvicorn backend.main:app" >/dev/null; then
    echo "Backend not running."
    exit 0
fi

pkill -TERM -f "uvicorn backend.main:app"
for _ in $(seq 1 20); do
    if ! pgrep -f "uvicorn backend.main:app" >/dev/null; then
        echo "Booth stopped cleanly (camera session closed)."
        exit 0
    fi
    sleep 0.5
done

echo "Backend still running after 10s — forcing." >&2
pkill -KILL -f "uvicorn backend.main:app"
exit 1

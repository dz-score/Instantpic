#!/bin/bash
cd /home/instantpic/Projects/instantpic-antigravity

source backend/.venv/bin/activate

cd frontend && npm run build
cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
sleep 1
chromium-browser \
  --kiosk \
  --start-maximized \
  --no-first-run \
  --no-default-browser-check \
  --disable-session-crashed-bubble \
  --disable-infobars \
  --disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication \
  --overscroll-history-navigation=0 \
  --autoplay-policy=no-user-gesture-required \
  --disable-pinch \
   --noerrdialogs \
   --disable-background-networking \
   --disable-cache \
    http://localhost:8000 &


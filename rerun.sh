#!/bin/bash
cd frontend && npm run build
cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
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
    http://localhost:8000 &


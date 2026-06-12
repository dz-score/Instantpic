#!/bin/bash
cd frontend && npm run build
cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 8000
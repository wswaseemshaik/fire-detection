#!/usr/bin/env python3
"""Start the backend only (serves the built UI from frontend/dist if present). Prefer `npm run dev`."""
import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level="info")

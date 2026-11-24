"""FastAPI server: REST + MJPEG + WebSocket alerts + static dashboard."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .detector import Detector
from .events import EventBus
from .pipeline import SourceManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fire")

STATIC = Path(__file__).parent / "static"

detector: Detector
bus: EventBus
manager: SourceManager
_ws_clients: set[WebSocket] = set()
_loop: Optional[asyncio.AbstractEventLoop] = None


def _broadcast_from_thread(event: dict) -> None:
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast(event), _loop)


async def _broadcast(event: dict) -> None:
    dead = []
    msg = json.dumps({"type": "alert", "event": event})
    for ws in list(_ws_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global detector, bus, manager, _loop
    _loop = asyncio.get_running_loop()
    detector = Detector(settings.weights, settings.device, settings.conf, settings.iou, settings.imgsz)
    bus = EventBus(settings.snapshot_dir, settings.webhook_url, jpeg_quality=settings.jpeg_quality)
    bus.subscribe(_broadcast_from_thread)
    manager = SourceManager(detector, bus, settings)
    manager.load_persisted()
    for src in settings.initial_sources:
        manager.add(src)
    log.info("ready with %d source(s)", len(manager.workers))
    try:
        yield
    finally:
        manager.stop_all()


app = FastAPI(title="Fire & Smoke Detection", lifespan=lifespan)
app.mount("/snapshots", StaticFiles(directory=settings.snapshot_dir), name="snapshots")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class SourceIn(BaseModel):
    source: str = Field(..., description="rtsp://, rtmp://, http(s)://, local file path, or webcam index")
    name: str = ""
    record: bool = False


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC / "index.html").read_text()


@app.get("/api/health")
async def health():
    return {"ok": True, "device": settings.device, "weights": settings.weights, "classes": detector.names}


@app.get("/api/sources")
async def list_sources():
    return manager.list()


@app.post("/api/sources", status_code=201)
async def add_source(body: SourceIn):
    src = body.source.strip()
    if not src:
        raise HTTPException(400, "source is required")
    w = await asyncio.to_thread(manager.add, src, body.name.strip(), body.record)
    return w.status()


@app.delete("/api/sources/{source_id}")
async def remove_source(source_id: str):
    if not await asyncio.to_thread(manager.remove, source_id):
        raise HTTPException(404, "no such source")
    return {"removed": source_id}


@app.get("/api/sources/{source_id}")
async def source_status(source_id: str):
    w = manager.get(source_id)
    if not w:
        raise HTTPException(404, "no such source")
    return w.status()


@app.get("/api/sources/{source_id}/snapshot.jpg")
async def snapshot(source_id: str):
    w = manager.get(source_id)
    if not w:
        raise HTTPException(404, "no such source")
    _, data = await asyncio.to_thread(w.jpeg, -1, 2.0)
    if not data:
        raise HTTPException(503, "no frame yet")
    return Response(data, media_type="image/jpeg")


@app.get("/api/sources/{source_id}/stream")
async def mjpeg(source_id: str):
    w = manager.get(source_id)
    if not w:
        raise HTTPException(404, "no such source")

    async def gen():
        seq = -1
        while manager.get(source_id) is w:
            seq, data = await asyncio.to_thread(w.jpeg, seq, 2.0)
            if not data:
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n"

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/events")
async def events(limit: int = 100):
    return bus.recent(min(max(limit, 1), 500))


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "hello", "sources": manager.list()}))
        while True:
            # keep the socket alive; periodically push source status for the dashboard
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=2.0)
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "status", "sources": manager.list()}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _ws_clients.discard(ws)


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

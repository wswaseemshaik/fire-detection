"""FastAPI server: REST + MJPEG + WebSocket + static frontend."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import files as filesmod
from .config import ROOT, settings
from .detector import Detector
from .events import EventBus
from .jobs import JobManager
from .pipeline import SourceManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fire")

FRONTEND_DIST = ROOT / "frontend" / "dist"

detector: Detector
bus: EventBus
manager: SourceManager
jobs: JobManager
_ws_clients: set[WebSocket] = set()
_loop: Optional[asyncio.AbstractEventLoop] = None


def _broadcast_from_thread(event: dict) -> None:
    if _loop is not None:
        asyncio.run_coroutine_threadsafe(_broadcast({"type": "alert", "event": event}), _loop)


async def _broadcast(payload: dict) -> None:
    msg = json.dumps(payload)
    for ws in list(_ws_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            _ws_clients.discard(ws)


def _enabled_labels() -> set[str]:
    return {l for l, on in (("fire", settings.detect_fire), ("smoke", settings.detect_smoke)) if on}


def _on_settings_change(changed: dict) -> None:
    detector.configure(settings.conf, settings.iou, settings.imgsz, _enabled_labels())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global detector, bus, manager, jobs, _loop
    _loop = asyncio.get_running_loop()
    detector = Detector(settings.weights, settings.device, settings.conf, settings.iou, settings.imgsz)
    detector.configure(labels=_enabled_labels())
    settings.on_change(_on_settings_change)
    bus = EventBus(settings)
    bus.subscribe(_broadcast_from_thread)
    manager = SourceManager(detector, bus, settings)
    jobs = JobManager(detector, settings)
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
app.mount("/media/recordings", StaticFiles(directory=settings.recording_dir), name="recordings")
app.mount("/media/clips", StaticFiles(directory=settings.clip_dir), name="clips")
app.mount("/media/jobs", StaticFiles(directory=settings.job_dir), name="jobmedia")


# models ---------------------------------------------------------------------------
class SourceIn(BaseModel):
    source: str
    name: str = ""
    record: bool = False
    paused: bool = False


class FolderIn(BaseModel):
    path: str
    record: bool = False
    recursive: bool = False


class SourcePatch(BaseModel):
    name: Optional[str] = None
    record: Optional[bool] = None
    paused: Optional[bool] = None
    restart: bool = False


class JobIn(BaseModel):
    path: str = ""
    files: list[str] = []
    recursive: bool = False
    sample_fps: float = 2.0
    annotate: bool = False


class TestNotification(BaseModel):
    channel: str


# system ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"ok": True, "device": settings.device, "weights": settings.weights, "classes": detector.names,
            "inference_ms": round(detector.inference_ms, 1), "ffmpeg": shutil.which("ffmpeg") is not None}


@app.get("/api/stats")
async def stats():
    srcs = manager.list()
    return {**bus.stats(), "sources": len(srcs), "sources_connected": sum(1 for s in srcs if s["connected"]),
            "sources_alarm": sum(1 for s in srcs if s["alarm"]), "inference_ms": round(detector.inference_ms, 1),
            "jobs_running": sum(1 for j in jobs.jobs.values() if j.status == "running")}


@app.get("/api/settings")
async def get_settings():
    return {"settings": settings.to_dict(), "channels": bus.notifier.channels()}


@app.put("/api/settings")
async def put_settings(body: dict):
    # masked secrets coming back unchanged must not overwrite the real value
    for k in ("smtp_password", "telegram_bot_token"):
        if isinstance(body.get(k), str) and body[k].startswith("••••"):
            body.pop(k)
    changed = settings.update(body)
    return {"changed": changed, "settings": settings.to_dict(), "channels": bus.notifier.channels()}


@app.post("/api/settings/test-notification")
async def test_notification(body: TestNotification):
    if body.channel not in ("webhook", "telegram", "email"):
        raise HTTPException(400, "unknown channel")
    ok, msg = await asyncio.to_thread(bus.notifier.test, body.channel)
    return {"ok": ok, "message": msg}


# sources ---------------------------------------------------------------------------
@app.get("/api/sources")
async def list_sources():
    return manager.list()


@app.post("/api/sources", status_code=201)
async def add_source(body: SourceIn):
    src = body.source.strip()
    if not src:
        raise HTTPException(400, "source is required")
    w = await asyncio.to_thread(manager.add, src, body.name.strip(), body.record, body.paused)
    return w.status()


@app.post("/api/sources/folder", status_code=201)
async def add_folder(body: FolderIn):
    try:
        added = await asyncio.to_thread(manager.add_folder, body.path.strip(), body.record, body.recursive)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return {"added": [w.status() for w in added]}


@app.patch("/api/sources/{source_id}")
async def patch_source(source_id: str, body: SourcePatch):
    w = await asyncio.to_thread(manager.update, source_id, **body.model_dump())
    if not w:
        raise HTTPException(404, "no such source")
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
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode()
                   + b"\r\n\r\n" + data + b"\r\n")

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# files ---------------------------------------------------------------------------
@app.get("/api/files/browse")
async def browse(path: str = ""):
    try:
        return await asyncio.to_thread(filesmod.browse, path)
    except FileNotFoundError:
        raise HTTPException(404, "path not found")


@app.post("/api/files/upload", status_code=201)
async def upload(file: UploadFile = File(...)):
    name = filesmod.safe_name(file.filename or "upload.mp4")
    if Path(name).suffix.lower() not in filesmod.VIDEO_EXTS:
        raise HTTPException(400, "unsupported file type")
    dest = settings.upload_dir / name
    with dest.open("wb") as out:
        while chunk := await file.read(1 << 20):
            out.write(chunk)
    return {"path": str(dest), "name": name, "size": dest.stat().st_size}


@app.get("/api/uploads")
async def uploads():
    return [{"name": f.name, "path": str(f), "size": f.stat().st_size, "mtime": f.stat().st_mtime}
            for f in sorted(settings.upload_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True) if f.is_file()]


# events ---------------------------------------------------------------------------
@app.get("/api/events")
async def events(limit: int = 100, source_id: str = "", label: str = "", since: str = ""):
    return bus.recent(min(max(limit, 1), 2000), source_id, label, since)


@app.delete("/api/events/{event_id}")
async def delete_event(event_id: str):
    if not bus.delete(event_id):
        raise HTTPException(404, "no such event")
    return {"deleted": event_id}


@app.delete("/api/events")
async def clear_events():
    return {"deleted": bus.clear()}


# media ---------------------------------------------------------------------------
@app.get("/api/media")
async def media():
    return await asyncio.to_thread(filesmod.list_media, settings)


@app.delete("/api/media/{kind}/{name}")
async def delete_media(kind: str, name: str):
    d = {"recordings": settings.recording_dir, "clips": settings.clip_dir, "jobs": settings.job_dir}.get(kind)
    if d is None or "/" in name or name.startswith("."):
        raise HTTPException(400, "bad path")
    p = d / name
    if not p.exists():
        raise HTTPException(404, "not found")
    p.unlink()
    return {"deleted": name}


# batch jobs -------------------------------------------------------------------------
@app.get("/api/jobs")
async def list_jobs():
    return jobs.list()


@app.post("/api/jobs", status_code=201)
async def create_job(body: JobIn):
    try:
        job = await asyncio.to_thread(jobs.create, body.path.strip(), body.files, body.recursive, body.sample_fps, body.annotate)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(400, str(e))
    return job.to_dict()


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return job.to_dict(full=True)


@app.get("/api/jobs/{job_id}/report.csv")
async def job_csv(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return PlainTextResponse(job.csv(), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=job_{job_id}.csv"})


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    job.cancel()
    return job.to_dict()


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    if not jobs.delete(job_id):
        raise HTTPException(404, "no such job")
    return {"deleted": job_id}


# websocket --------------------------------------------------------------------------
@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "status", "sources": manager.list()}))
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "status", "sources": manager.list(),
                                               "jobs": [j.to_dict() for j in jobs.jobs.values() if j.status == "running"]}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _ws_clients.discard(ws)


# frontend ---------------------------------------------------------------------------
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        f = FRONTEND_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    async def no_frontend():
        return PlainTextResponse("Frontend not built. Run `npm run dev` (dev) or `npm run build` (prod).")

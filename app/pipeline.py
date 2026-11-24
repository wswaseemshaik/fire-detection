"""One worker thread per video source: read -> detect -> annotate -> alert -> publish."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .config import VIDEO_EXTS, Settings
from .detector import Detection, Detector
from .events import AlertState, EventBus
from .stream import StreamReader
from .video_writer import VideoWriter

log = logging.getLogger(__name__)


class SourceWorker:
    def __init__(self, source: str, name: str, detector: Detector, bus: EventBus, settings: Settings,
                 source_id: Optional[str] = None, record: bool = False, paused: bool = False, group: str = ""):
        self.id = source_id or uuid.uuid4().hex[:8]
        self.name = name or Path(source).name or source
        self.source = source
        self.group = group
        self.detector = detector
        self.bus = bus
        self.s = settings
        self.record = record
        self.paused = paused

        self.reader = StreamReader(source, settings)
        self.alert = AlertState(settings)

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._jpeg: Optional[bytes] = None
        self._jpeg_seq = 0
        self._jpeg_ready = threading.Condition(self._lock)
        self._writer: Optional[VideoWriter] = None
        self._clip: Optional[VideoWriter] = None
        self._clip_until = 0.0
        self._ring: deque[np.ndarray] = deque()

        self.last_detections: list[Detection] = []
        self.frames_processed = 0
        self.alerts = 0
        self.fps = 0.0
        self._fps_window: deque[float] = deque(maxlen=30)
        self.started_at = time.time()
        self.last_frame_at = 0.0

    # lifecycle ----------------------------------------------------------------
    def start(self) -> "SourceWorker":
        self.reader.start()
        self._thread = threading.Thread(target=self._run, name=f"worker:{self.id}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.reader.stop()
        if self._thread:
            self._thread.join(timeout=10)
        self._close_writers()

    def restart_stream(self) -> None:
        self.reader.stop()
        self.reader = StreamReader(self.source, self.s).start()
        self.alert.reset()

    def _close_writers(self) -> None:
        for attr in ("_writer", "_clip"):
            w = getattr(self, attr)
            if w:
                w.close()
                setattr(self, attr, None)

    # status -------------------------------------------------------------------
    def status(self) -> dict:
        r = self.reader
        return {
            "id": self.id, "name": self.name, "source": self.source, "group": self.group,
            "live": r.is_live, "connected": r.connected, "ended": r.ended, "error": r.last_error,
            "resolution": f"{r.width}x{r.height}" if r.width else None,
            "source_fps": round(r.fps, 1), "fps": round(self.fps, 1), "frames": self.frames_processed,
            "progress": r.progress, "paused": self.paused, "recording": self.record,
            "alarm": self.alert.active, "alerts": self.alerts,
            "detections": [d.to_dict() for d in self.last_detections],
            "uptime": round(time.time() - self.started_at),
        }

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "source": self.source, "record": self.record,
                "paused": self.paused, "group": self.group}

    # frame access ---------------------------------------------------------------
    def jpeg(self, wait_after: int = -1, timeout: float = 2.0) -> tuple[int, Optional[bytes]]:
        with self._jpeg_ready:
            if self._jpeg_seq == wait_after:
                self._jpeg_ready.wait(timeout)
            return self._jpeg_seq, self._jpeg

    # main loop ------------------------------------------------------------------
    def _run(self) -> None:
        last_id = -1
        n = 0
        dets: list[Detection] = []
        last_tick = time.monotonic()
        while not self._stop.is_set():
            fid, frame = self.reader.latest(last_id)
            if frame is None:
                if self.reader.ended:
                    self._publish_placeholder("End of video")
                elif not self.reader.connected:
                    self._publish_placeholder(f"Connecting... {self.reader.last_error}".strip())
                else:
                    time.sleep(0.005)
                    continue
                self._close_writers()
                time.sleep(0.5)
                continue
            last_id = fid
            n += 1

            if self.paused:
                dets = []
                self.last_detections = []
                self.alert.reset()
            elif n % self.s.detect_every == 0:
                try:
                    dets = self.detector.detect(frame)
                except Exception as e:
                    log.exception("detection failed on %s: %s", self.name, e)
                    dets = []
                self.last_detections = dets
                if self.alert.update(bool(dets)):
                    self._on_alert(frame, dets)

            now = time.monotonic()
            self._fps_window.append(now - last_tick)
            last_tick = now
            self.fps = len(self._fps_window) / max(sum(self._fps_window), 1e-6)
            self.frames_processed += 1
            self.last_frame_at = time.time()

            header = f"{self.name}  |  {self.fps:.1f} fps"
            if self.paused:
                header += "  |  PAUSED"
            elif self.alert.active:
                header += "  |  ALARM: " + ", ".join(sorted({d.label for d in dets})).upper()
            annotated = self.detector.annotate(frame, dets, header)
            if self.alert.active:
                cv2.rectangle(annotated, (0, 0), (annotated.shape[1] - 1, annotated.shape[0] - 1), (0, 0, 255), 6)

            self._handle_recording(annotated)
            self._handle_clip(annotated)
            self._publish(annotated)

    # alerts / clips / recording ----------------------------------------------------
    def _on_alert(self, frame: np.ndarray, dets: list[Detection]) -> None:
        self.alerts += 1
        clip_name = None
        if self.s.save_clips:
            clip_name = f"{time.strftime('%Y%m%dT%H%M%S')}_{self.id}_alert.mp4"
            self._start_clip(clip_name)
        self.bus.emit(self.id, self.name, self.detector.annotate(frame, dets), dets, clip_name)

    def _effective_fps(self) -> float:
        src = self.reader.fps
        proc = self.fps
        if 0 < proc < 120:
            return max(proc, 1.0) if self.reader.is_live else min(max(proc, 1.0), src if src > 0 else proc)
        return src if 0 < src < 120 else 15.0

    def _start_clip(self, name: str) -> None:
        if self._clip:  # extend an in-progress clip instead of starting a new one
            self._clip_until = time.time() + self.s.clip_post_seconds
            return
        if not self._ring:
            return
        f0 = self._ring[0]
        self._clip = VideoWriter(self.s.clip_dir / name, self._effective_fps(), (f0.shape[1], f0.shape[0]))
        for f in self._ring:
            self._clip.write(f)
        self._clip_until = time.time() + self.s.clip_post_seconds

    def _handle_clip(self, annotated: np.ndarray) -> None:
        if self.s.save_clips:
            self._ring.append(annotated)
            max_len = max(1, int(self.s.clip_pre_seconds * max(self.fps, 1.0)))
            while len(self._ring) > max_len:
                self._ring.popleft()
        elif self._ring:
            self._ring.clear()
        if self._clip:
            self._clip.write(annotated)
            if time.time() >= self._clip_until:
                self._clip.close()
                self._clip = None

    def _handle_recording(self, annotated: np.ndarray) -> None:
        if self.record and self._writer is None:
            path = self.s.recording_dir / f"{time.strftime('%Y%m%dT%H%M%S')}_{self.id}.mp4"
            self._writer = VideoWriter(path, self._effective_fps(), (annotated.shape[1], annotated.shape[0]))
            log.info("recording %s -> %s", self.name, path.name)
        elif not self.record and self._writer is not None:
            self._writer.close()
            self._writer = None
        if self._writer:
            self._writer.write(annotated)

    # publishing --------------------------------------------------------------------
    def _publish(self, frame: np.ndarray) -> None:
        mw = self.s.stream_max_width
        if mw and frame.shape[1] > mw:
            frame = cv2.resize(frame, (mw, int(frame.shape[0] * mw / frame.shape[1])))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.s.jpeg_quality])
        if not ok:
            return
        with self._jpeg_ready:
            self._jpeg = buf.tobytes()
            self._jpeg_seq += 1
            self._jpeg_ready.notify_all()

    def _publish_placeholder(self, text: str) -> None:
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(img, self.name[:40], (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(img, text[:70], (16, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
        self._publish(img)


class SourceManager:
    def __init__(self, detector: Detector, bus: EventBus, settings: Settings):
        self.detector = detector
        self.bus = bus
        self.s = settings
        self.workers: dict[str, SourceWorker] = {}
        self._lock = threading.Lock()

    def add(self, source: str, name: str = "", record: bool = False, paused: bool = False,
            source_id: Optional[str] = None, group: str = "", persist: bool = True) -> SourceWorker:
        with self._lock:
            for w in self.workers.values():
                if w.source == source:
                    return w
            w = SourceWorker(source, name, self.detector, self.bus, self.s, source_id, record, paused, group).start()
            self.workers[w.id] = w
            if persist:
                self._persist()
            return w

    def add_folder(self, folder: str, record: bool = False, recursive: bool = False) -> list[SourceWorker]:
        p = Path(folder).expanduser()
        if not p.is_dir():
            raise FileNotFoundError(f"not a directory: {folder}")
        it = p.rglob("*") if recursive else p.iterdir()
        files = sorted(f for f in it if f.is_file() and f.suffix.lower() in VIDEO_EXTS)
        added = [self.add(str(f), f.name, record, group=p.name, persist=False) for f in files]
        self._persist()
        return added

    def update(self, source_id: str, **fields) -> Optional[SourceWorker]:
        w = self.workers.get(source_id)
        if not w:
            return None
        if "name" in fields and fields["name"] is not None:
            w.name = fields["name"].strip() or w.name
        if "record" in fields and fields["record"] is not None:
            w.record = bool(fields["record"])
        if "paused" in fields and fields["paused"] is not None:
            w.paused = bool(fields["paused"])
        if fields.get("restart"):
            w.restart_stream()
        self._persist()
        return w

    def remove(self, source_id: str) -> bool:
        with self._lock:
            w = self.workers.pop(source_id, None)
            if not w:
                return False
            self._persist()
        w.stop()
        return True

    def get(self, source_id: str) -> Optional[SourceWorker]:
        return self.workers.get(source_id)

    def list(self) -> list[dict]:
        return [w.status() for w in self.workers.values()]

    def stop_all(self) -> None:
        for w in list(self.workers.values()):
            w.stop()

    def _persist(self) -> None:
        self.s.sources_file.write_text(json.dumps([w.to_json() for w in self.workers.values()], indent=2))

    def load_persisted(self) -> None:
        f = self.s.sources_file
        if not f.exists():
            return
        try:
            for item in json.loads(f.read_text()):
                self.add(item["source"], item.get("name", ""), item.get("record", False), item.get("paused", False),
                         item.get("id"), item.get("group", ""), persist=False)
        except Exception as e:
            log.warning("could not load %s: %s", f, e)

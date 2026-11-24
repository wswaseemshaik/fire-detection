"""One worker thread per video source: read -> detect -> annotate -> alert -> publish."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .config import Settings
from .detector import Detection, Detector
from .events import AlertState, EventBus
from .stream import StreamReader

log = logging.getLogger(__name__)


class SourceWorker:
    def __init__(
        self,
        source: str,
        name: str,
        detector: Detector,
        bus: EventBus,
        settings: Settings,
        source_id: Optional[str] = None,
        record: bool = False,
    ):
        self.id = source_id or uuid.uuid4().hex[:8]
        self.name = name or source
        self.source = source
        self.detector = detector
        self.bus = bus
        self.settings = settings
        self.record = record

        self.reader = StreamReader(source, settings.reconnect_delay, settings.loop_files)
        self.alert = AlertState(settings.min_consecutive, settings.alert_cooldown)

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._jpeg: Optional[bytes] = None
        self._jpeg_seq = 0
        self._jpeg_ready = threading.Condition(self._lock)
        self._writer: Optional[cv2.VideoWriter] = None

        self.last_detections: list[Detection] = []
        self.frames_processed = 0
        self.fps = 0.0
        self._fps_window: deque[float] = deque(maxlen=30)
        self.started_at = time.time()
        self.last_frame_at = 0.0

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> "SourceWorker":
        self.reader.start()
        self._thread = threading.Thread(target=self._run, name=f"worker:{self.id}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.reader.stop()
        if self._thread:
            self._thread.join(timeout=5)
        if self._writer:
            self._writer.release()
            self._writer = None

    # -- status ----------------------------------------------------------------
    def status(self) -> dict:
        r = self.reader
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "live": r.is_live,
            "connected": r.connected,
            "ended": r.ended,
            "error": r.last_error,
            "resolution": f"{r.width}x{r.height}" if r.width else None,
            "source_fps": round(r.fps, 1),
            "fps": round(self.fps, 1),
            "frames": self.frames_processed,
            "alarm": self.alert.active,
            "detections": [d.to_dict() for d in self.last_detections],
            "recording": self.record,
            "uptime": round(time.time() - self.started_at),
        }

    # -- frame access ---------------------------------------------------------
    def jpeg(self, wait_after: int = -1, timeout: float = 2.0) -> tuple[int, Optional[bytes]]:
        """Block until a frame newer than wait_after is available (or timeout)."""
        with self._jpeg_ready:
            if self._jpeg_seq == wait_after:
                self._jpeg_ready.wait(timeout)
            return self._jpeg_seq, self._jpeg

    # -- main loop --------------------------------------------------------------
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
                    time.sleep(0.5)
                elif not self.reader.connected:
                    self._publish_placeholder(f"Connecting... {self.reader.last_error}".strip())
                    time.sleep(0.5)
                else:
                    time.sleep(0.005)
                continue
            last_id = fid
            n += 1

            if n % self.settings.detect_every == 0:
                try:
                    dets = self.detector.detect(frame)
                except Exception as e:
                    log.exception("detection failed on %s: %s", self.name, e)
                    dets = []
                self.last_detections = dets
                if self.alert.update(bool(dets)):
                    self.bus.emit(self.id, self.name, self.detector.annotate(frame, dets), dets)

            now = time.monotonic()
            self._fps_window.append(now - last_tick)
            last_tick = now
            self.fps = len(self._fps_window) / max(sum(self._fps_window), 1e-6)
            self.frames_processed += 1
            self.last_frame_at = time.time()

            header = f"{self.name}  |  {self.fps:.1f} fps"
            if self.alert.active:
                header += "  |  ALARM: " + ", ".join(sorted({d.label for d in dets})).upper()
            annotated = self.detector.annotate(frame, dets, header)
            if self.alert.active:
                cv2.rectangle(annotated, (0, 0), (annotated.shape[1] - 1, annotated.shape[0] - 1), (0, 0, 255), 6)
            if self.record:
                self._write(annotated)
            self._publish(annotated)

    def _publish(self, frame: np.ndarray) -> None:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.settings.jpeg_quality])
        if not ok:
            return
        with self._jpeg_ready:
            self._jpeg = buf.tobytes()
            self._jpeg_seq += 1
            self._jpeg_ready.notify_all()

    def _publish_placeholder(self, text: str) -> None:
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(img, self.name, (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(img, text[:70], (16, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
        self._publish(img)

    def _write(self, frame: np.ndarray) -> None:
        if self._writer is None:
            fps = self.reader.fps if 0 < self.reader.fps < 120 else 15.0
            path = Path(self.settings.recording_dir) / f"{time.strftime('%Y%m%dT%H%M%S')}_{self.id}.mp4"
            self._writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (frame.shape[1], frame.shape[0]))
            log.info("recording %s -> %s", self.name, path)
        self._writer.write(frame)


class SourceManager:
    def __init__(self, detector: Detector, bus: EventBus, settings: Settings):
        self.detector = detector
        self.bus = bus
        self.settings = settings
        self.workers: dict[str, SourceWorker] = {}
        self._lock = threading.Lock()

    def add(self, source: str, name: str = "", record: bool = False, source_id: Optional[str] = None) -> SourceWorker:
        with self._lock:
            for w in self.workers.values():
                if w.source == source:
                    return w
            w = SourceWorker(source, name, self.detector, self.bus, self.settings, source_id, record).start()
            self.workers[w.id] = w
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

    # persistence so sources survive restarts
    def _persist(self) -> None:
        import json

        data = [{"id": w.id, "name": w.name, "source": w.source, "record": w.record} for w in self.workers.values()]
        self.settings.sources_file.write_text(json.dumps(data, indent=2))

    def load_persisted(self) -> None:
        import json

        f = self.settings.sources_file
        if not f.exists():
            return
        try:
            for item in json.loads(f.read_text()):
                self.add(item["source"], item.get("name", ""), item.get("record", False), item.get("id"))
        except Exception as e:
            log.warning("could not load %s: %s", f, e)

"""Alert state machine, persistent event log and fan-out to subscribers/notifiers."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Optional

import cv2
import numpy as np

from .config import Settings
from .detector import Detection
from .notify import Notifier

log = logging.getLogger(__name__)


class AlertState:
    """Per-source debounce: require N consecutive positive passes, then rate-limit alerts."""

    def __init__(self, settings: Settings):
        self.s = settings
        self.consecutive = 0
        self.last_alert_ts = 0.0
        self.active = False

    def update(self, positive: bool) -> bool:
        if not positive:
            self.consecutive = 0
            self.active = False
            return False
        self.consecutive += 1
        if self.consecutive < self.s.min_consecutive:
            return False
        self.active = True
        now = time.time()
        if now - self.last_alert_ts >= self.s.alert_cooldown:
            self.last_alert_ts = now
            return True
        return False

    def reset(self) -> None:
        self.consecutive = 0
        self.active = False


class EventBus:
    def __init__(self, settings: Settings, max_events: int = 2000):
        self.s = settings
        self.notifier = Notifier(settings)
        self.events: deque[dict] = deque(maxlen=max_events)
        self._subs: list[Callable[[dict], None]] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        f = self.s.events_file
        if not f.exists():
            return
        try:
            for line in f.read_text().splitlines()[-self.events.maxlen:]:
                if line.strip():
                    self.events.append(json.loads(line))
        except Exception as e:
            log.warning("could not load %s: %s", f, e)

    def _flush(self) -> None:
        self.s.events_file.write_text("".join(json.dumps(e) + "\n" for e in self.events))

    def subscribe(self, fn: Callable[[dict], None]) -> None:
        self._subs.append(fn)

    def emit(self, source_id: str, source_name: str, frame: np.ndarray, dets: list[Detection],
             clip_name: Optional[str] = None) -> dict:
        ts = datetime.now(timezone.utc)
        labels = sorted({d.label for d in dets})
        fname = f"{ts.strftime('%Y%m%dT%H%M%S')}_{source_id}_{'-'.join(labels)}.jpg"
        path = self.s.snapshot_dir / fname
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        event = {
            "id": f"{int(ts.timestamp() * 1000)}-{source_id}",
            "timestamp": ts.isoformat(),
            "source_id": source_id,
            "source_name": source_name,
            "labels": labels,
            "max_confidence": round(max(d.confidence for d in dets), 3),
            "detections": [d.to_dict() for d in dets],
            "snapshot": f"/snapshots/{fname}",
            "clip": f"/media/clips/{clip_name}" if clip_name else None,
        }
        with self._lock:
            self.events.append(event)
            with self.s.events_file.open("a") as f:
                f.write(json.dumps(event) + "\n")
        log.warning("ALERT %s on %s (conf=%.2f)", labels, source_name, event["max_confidence"])
        for fn in list(self._subs):
            try:
                fn(event)
            except Exception as e:
                log.error("subscriber failed: %s", e)
        self.notifier.send(event, path)
        return event

    def recent(self, limit: int = 100, source_id: str = "", label: str = "", since: str = "") -> list[dict]:
        with self._lock:
            evs = list(self.events)
        if source_id:
            evs = [e for e in evs if e["source_id"] == source_id]
        if label:
            evs = [e for e in evs if label in e["labels"]]
        if since:
            evs = [e for e in evs if e["timestamp"] >= since]
        return evs[-limit:][::-1]

    def delete(self, event_id: str) -> bool:
        with self._lock:
            ev = next((e for e in self.events if e["id"] == event_id), None)
            if not ev:
                return False
            self.events.remove(ev)
            self._flush()
        self._unlink(ev)
        return True

    def clear(self) -> int:
        with self._lock:
            evs = list(self.events)
            self.events.clear()
            self._flush()
        for ev in evs:
            self._unlink(ev)
        return len(evs)

    def _unlink(self, ev: dict) -> None:
        for key, d in (("snapshot", self.s.snapshot_dir), ("clip", self.s.clip_dir)):
            if ev.get(key):
                p = d / ev[key].rsplit("/", 1)[-1]
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    def stats(self) -> dict:
        now = time.time()
        with self._lock:
            evs = list(self.events)
        def within(sec):
            cutoff = datetime.fromtimestamp(now - sec, tz=timezone.utc).isoformat()
            return [e for e in evs if e["timestamp"] >= cutoff]
        day = within(86400)
        hourly = [0] * 24
        for e in day:
            age_h = int((now - datetime.fromisoformat(e["timestamp"]).timestamp()) // 3600)
            if 0 <= age_h < 24:
                hourly[23 - age_h] += 1
        by_source: dict[str, int] = {}
        by_label: dict[str, int] = {}
        for e in day:
            by_source[e["source_name"]] = by_source.get(e["source_name"], 0) + 1
            for l in e["labels"]:
                by_label[l] = by_label.get(l, 0) + 1
        return {"total": len(evs), "last_hour": len(within(3600)), "last_24h": len(day),
                "hourly": hourly, "by_source": by_source, "by_label": by_label}

"""Alert state machine, event log, snapshot saving, webhook and in-process subscribers."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .detector import Detection

log = logging.getLogger(__name__)


class AlertState:
    """Per-source debounce: require N consecutive positive passes, then rate-limit alerts."""

    def __init__(self, min_consecutive: int, cooldown: float):
        self.min_consecutive = min_consecutive
        self.cooldown = cooldown
        self.consecutive = 0
        self.last_alert_ts = 0.0
        self.active = False  # currently in an alarm condition

    def update(self, positive: bool) -> bool:
        """Return True when a new alert should be emitted."""
        if positive:
            self.consecutive += 1
        else:
            self.consecutive = 0
            self.active = False
            return False
        if self.consecutive < self.min_consecutive:
            return False
        self.active = True
        now = time.time()
        if now - self.last_alert_ts >= self.cooldown:
            self.last_alert_ts = now
            return True
        return False


class EventBus:
    def __init__(self, snapshot_dir: Path, webhook_url: str = "", max_events: int = 500, jpeg_quality: int = 85):
        self.snapshot_dir = snapshot_dir
        self.webhook_url = webhook_url
        self.jpeg_quality = jpeg_quality
        self.events: deque[dict] = deque(maxlen=max_events)
        self.log_file = snapshot_dir.parent / "events.jsonl"
        self._subs: list[Callable[[dict], None]] = []
        self._lock = threading.Lock()
        self._load_existing()

    def _load_existing(self) -> None:
        if not self.log_file.exists():
            return
        try:
            lines = self.log_file.read_text().splitlines()[-self.events.maxlen:]
            for line in lines:
                if line.strip():
                    self.events.append(json.loads(line))
        except Exception as e:  # corrupt log shouldn't stop the app
            log.warning("could not load %s: %s", self.log_file, e)

    def subscribe(self, fn: Callable[[dict], None]) -> None:
        self._subs.append(fn)

    def emit(self, source_id: str, source_name: str, frame: np.ndarray, dets: list[Detection]) -> dict:
        ts = datetime.now(timezone.utc)
        labels = sorted({d.label for d in dets})
        fname = f"{ts.strftime('%Y%m%dT%H%M%S')}_{source_id}_{'-'.join(labels)}.jpg"
        path = self.snapshot_dir / fname
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        event = {
            "id": f"{int(ts.timestamp() * 1000)}-{source_id}",
            "timestamp": ts.isoformat(),
            "source_id": source_id,
            "source_name": source_name,
            "labels": labels,
            "max_confidence": round(max(d.confidence for d in dets), 3),
            "detections": [d.to_dict() for d in dets],
            "snapshot": f"/snapshots/{fname}",
        }
        with self._lock:
            self.events.append(event)
            with self.log_file.open("a") as f:
                f.write(json.dumps(event) + "\n")
        log.warning("ALERT %s on %s (%s, conf=%.2f)", labels, source_name, source_id, event["max_confidence"])
        for fn in list(self._subs):
            try:
                fn(event)
            except Exception as e:
                log.error("subscriber failed: %s", e)
        if self.webhook_url:
            threading.Thread(target=self._post_webhook, args=(event,), daemon=True).start()
        return event

    def _post_webhook(self, event: dict) -> None:
        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(event).encode(),
                headers={"Content-Type": "application/json", "User-Agent": "fire-detection/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                log.info("webhook -> %s", resp.status)
        except Exception as e:
            log.error("webhook failed: %s", e)

    def recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self.events)[-limit:][::-1]

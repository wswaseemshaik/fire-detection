"""YOLO fire/smoke detector wrapper."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, asdict

import cv2
import numpy as np
from ultralytics import YOLO

log = logging.getLogger(__name__)

COLORS = {"fire": (0, 69, 255), "smoke": (200, 200, 200)}  # BGR


@dataclass
class Detection:
    label: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidence"] = round(self.confidence, 3)
        return d


class Detector:
    """Thread-safe wrapper; a single instance is shared by every stream worker."""

    def __init__(self, weights: str, device: str = "cpu", conf: float = 0.35, iou: float = 0.5, imgsz: int = 640):
        self.model = YOLO(weights)
        self.weights = weights
        self.device = device
        self.conf = conf
        self.iou = iou
        self.imgsz = imgsz
        self.enabled_labels: set[str] | None = None  # None = all
        self.names = {int(k): str(v).lower() for k, v in self.model.names.items()}
        self._lock = threading.Lock()
        self.inference_ms = 0.0
        log.info("loaded %s on %s, classes=%s", weights, device, self.names)
        self.detect(np.zeros((imgsz, imgsz, 3), dtype=np.uint8))  # warm-up

    def configure(self, conf: float | None = None, iou: float | None = None, imgsz: int | None = None,
                  labels: set[str] | None = None) -> None:
        if conf is not None: self.conf = conf
        if iou is not None: self.iou = iou
        if imgsz is not None: self.imgsz = imgsz
        self.enabled_labels = labels

    def detect(self, frame: np.ndarray) -> list[Detection]:
        import time
        t0 = time.perf_counter()
        with self._lock:
            results = self.model.predict(frame, conf=self.conf, iou=self.iou, imgsz=self.imgsz,
                                         device=self.device, verbose=False)
        self.inference_ms = (time.perf_counter() - t0) * 1000
        out: list[Detection] = []
        r = results[0]
        if r.boxes is None or len(r.boxes) == 0:
            return out
        xyxy = r.boxes.xyxy.cpu().numpy()
        confs = r.boxes.conf.cpu().numpy()
        clss = r.boxes.cls.cpu().numpy().astype(int)
        for (x1, y1, x2, y2), c, k in zip(xyxy, confs, clss):
            label = self.names.get(k, str(k))
            if self.enabled_labels is not None and label not in self.enabled_labels:
                continue
            out.append(Detection(label, float(c), int(x1), int(y1), int(x2), int(y2)))
        return out

    @staticmethod
    def annotate(frame: np.ndarray, dets: list[Detection], header: str = "") -> np.ndarray:
        img = frame.copy()
        for d in dets:
            color = COLORS.get(d.label, (0, 255, 0))
            cv2.rectangle(img, (d.x1, d.y1), (d.x2, d.y2), color, 2)
            text = f"{d.label} {d.confidence:.2f}"
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(img, (d.x1, max(0, d.y1 - th - 8)), (d.x1 + tw + 6, d.y1), color, -1)
            cv2.putText(img, text, (d.x1 + 3, d.y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
        if header:
            cv2.rectangle(img, (0, 0), (img.shape[1], 28), (0, 0, 0), -1)
            cv2.putText(img, header, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        return img

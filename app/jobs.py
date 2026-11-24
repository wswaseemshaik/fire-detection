"""Batch jobs: run the detector over every video in a folder (or a list of files) as fast as
possible and produce a per-file report of where fire/smoke appears."""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import cv2

from .config import VIDEO_EXTS, Settings
from .detector import Detector
from .video_writer import VideoWriter

log = logging.getLogger(__name__)


class BatchJob:
    def __init__(self, files: list[Path], detector: Detector, settings: Settings, sample_fps: float = 2.0,
                 annotate: bool = False, label: str = ""):
        self.id = uuid.uuid4().hex[:8]
        self.files = files
        self.detector = detector
        self.s = settings
        self.sample_fps = max(0.1, sample_fps)
        self.annotate = annotate
        self.label = label or (files[0].parent.name if files else "job")
        self.status = "queued"
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.current_file = ""
        self.file_index = 0
        self.file_progress = 0.0
        self.results: list[dict] = []
        self.error = ""
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "BatchJob":
        self._thread = threading.Thread(target=self._run, name=f"job:{self.id}", daemon=True)
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancel.set()

    def to_dict(self, full: bool = False) -> dict:
        d = {"id": self.id, "label": self.label, "status": self.status, "files": len(self.files),
             "file_index": self.file_index, "current_file": self.current_file, "file_progress": round(self.file_progress, 3),
             "progress": round(min(1.0, (self.file_index + self.file_progress) / max(len(self.files), 1)), 3),
             "created": self.created, "started": self.started, "finished": self.finished, "error": self.error,
             "sample_fps": self.sample_fps, "annotate": self.annotate,
             "positives": sum(1 for r in self.results if r["detections"] > 0)}
        if full:
            d["results"] = self.results
        return d

    def _run(self) -> None:
        self.status = "running"
        self.started = time.time()
        try:
            for i, f in enumerate(self.files):
                if self._cancel.is_set():
                    self.status = "cancelled"
                    break
                self.file_index = i
                self.current_file = f.name
                self.file_progress = 0.0
                try:
                    self.results.append(self._process(f))
                except Exception as e:
                    log.exception("job %s failed on %s", self.id, f)
                    self.results.append({"file": str(f), "name": f.name, "error": str(e), "detections": 0, "segments": []})
            else:
                self.file_index = len(self.files)
                self.file_progress = 0.0
                self.current_file = ""
                self.status = "done"
        except Exception as e:
            self.status = "failed"
            self.error = str(e)
        self.finished = time.time()
        self._save()

    def _process(self, path: Path) -> dict:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise RuntimeError("could not open video")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        stride = max(1, int(round(fps / self.sample_fps)))
        writer = None
        out_name = None
        segments: list[dict] = []
        current: Optional[dict] = None
        n_det = 0
        max_conf = 0.0
        labels: set[str] = set()
        idx = 0
        last_dets = []
        while True:
            if self._cancel.is_set():
                break
            ok, frame = cap.read()
            if not ok:
                break
            t = idx / fps
            if idx % stride == 0:
                last_dets = self.detector.detect(frame)
                if last_dets:
                    n_det += 1
                    for d in last_dets:
                        labels.add(d.label)
                        max_conf = max(max_conf, d.confidence)
                    if current and t - current["end"] <= 2.0:
                        current["end"] = t
                        current["max_confidence"] = max(current["max_confidence"], max(d.confidence for d in last_dets))
                        current["labels"] = sorted(set(current["labels"]) | {d.label for d in last_dets})
                    else:
                        current = {"start": round(t, 2), "end": t, "labels": sorted({d.label for d in last_dets}),
                                   "max_confidence": max(d.confidence for d in last_dets)}
                        segments.append(current)
                if total:
                    self.file_progress = idx / total
            if self.annotate:
                if writer is None:
                    out_name = f"{self.id}_{path.stem}.mp4"
                    writer = VideoWriter(self.s.job_dir / out_name, fps, (frame.shape[1], frame.shape[0]))
                writer.write(self.detector.annotate(frame, last_dets, f"{path.name}  t={t:.1f}s"))
            idx += 1
        cap.release()
        if writer:
            writer.close()
        for sgm in segments:
            sgm["end"] = round(sgm["end"], 2)
            sgm["max_confidence"] = round(sgm["max_confidence"], 3)
        self.file_progress = 1.0
        return {"file": str(path), "name": path.name, "duration": round(idx / fps, 2), "frames": idx,
                "sampled": (idx + stride - 1) // stride, "detections": n_det, "labels": sorted(labels),
                "max_confidence": round(max_conf, 3), "segments": segments,
                "annotated": f"/media/jobs/{out_name}" if out_name else None}

    def _save(self) -> None:
        (self.s.job_dir / f"{self.id}.json").write_text(json.dumps(self.to_dict(full=True), indent=2))

    def csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["file", "duration_s", "positive_samples", "labels", "max_confidence", "segment_start_s", "segment_end_s", "segment_labels", "segment_conf"])
        for r in self.results:
            if not r.get("segments"):
                w.writerow([r["file"], r.get("duration", ""), r["detections"], "+".join(r.get("labels", [])), r.get("max_confidence", ""), "", "", "", ""])
            for sgm in r.get("segments", []):
                w.writerow([r["file"], r.get("duration", ""), r["detections"], "+".join(r.get("labels", [])), r.get("max_confidence", ""),
                            sgm["start"], sgm["end"], "+".join(sgm["labels"]), sgm["max_confidence"]])
        return buf.getvalue()


class JobManager:
    def __init__(self, detector: Detector, settings: Settings):
        self.detector = detector
        self.s = settings
        self.jobs: dict[str, BatchJob] = {}
        self._load()

    def _load(self) -> None:
        for f in sorted(self.s.job_dir.glob("*.json")):
            try:
                d = json.loads(f.read_text())
                job = BatchJob([], self.detector, self.s, d.get("sample_fps", 2), d.get("annotate", False), d.get("label"))
                job.id = d["id"]; job.status = d["status"]; job.results = d.get("results", [])
                job.created = d.get("created", f.stat().st_mtime); job.started = d.get("started"); job.finished = d.get("finished")
                job.files = [Path(r["file"]) for r in job.results]; job.file_index = len(job.files); job.error = d.get("error", "")
                self.jobs[job.id] = job
            except Exception as e:
                log.warning("bad job file %s: %s", f, e)

    def create(self, path: str = "", files: Optional[list[str]] = None, recursive: bool = False,
               sample_fps: float = 2.0, annotate: bool = False) -> BatchJob:
        paths: list[Path] = []
        if path:
            p = Path(path).expanduser()
            if p.is_dir():
                it = p.rglob("*") if recursive else p.iterdir()
                paths += sorted(f for f in it if f.is_file() and f.suffix.lower() in VIDEO_EXTS)
            elif p.is_file():
                paths.append(p)
            else:
                raise FileNotFoundError(path)
        for f in files or []:
            q = Path(f).expanduser()
            if q.is_file():
                paths.append(q)
        if not paths:
            raise ValueError("no video files found")
        job = BatchJob(paths, self.detector, self.s, sample_fps, annotate).start()
        self.jobs[job.id] = job
        return job

    def list(self) -> list[dict]:
        return sorted((j.to_dict() for j in self.jobs.values()), key=lambda d: d["created"], reverse=True)

    def get(self, job_id: str) -> Optional[BatchJob]:
        return self.jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        job = self.jobs.pop(job_id, None)
        if not job:
            return False
        job.cancel()
        (self.s.job_dir / f"{job_id}.json").unlink(missing_ok=True)
        for f in self.s.job_dir.glob(f"{job_id}_*.mp4"):
            f.unlink(missing_ok=True)
        return True

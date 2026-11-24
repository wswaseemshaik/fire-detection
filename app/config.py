"""Runtime configuration.

Defaults come from environment variables; anything changed from the UI is persisted to
data/settings.json and applied live (no restart). See `.env.example`.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

# Must be set before OpenCV is imported anywhere: force TCP for RTSP so frames don't
# arrive corrupted/out of order over UDP, and cap the time we wait on a dead stream.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;5000000|rw_timeout;5000000|max_delay;500000|buffer_size;1048576",
)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FIRE_DATA_DIR", str(ROOT / "data")))

VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".m4v", ".ts", ".flv", ".wmv", ".mpg", ".mpeg"}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# key -> (default, type)
SCHEMA: dict[str, tuple[Any, type]] = {
    # detection
    "weights": (_env("FIRE_WEIGHTS", str(ROOT / "models" / "fire-smoke-yolov8n.pt")), str),
    "device": (_env("FIRE_DEVICE", "cpu"), str),
    "conf": (float(_env("FIRE_CONF", "0.35")), float),
    "iou": (float(_env("FIRE_IOU", "0.5")), float),
    "imgsz": (int(_env("FIRE_IMGSZ", "640")), int),
    "detect_every": (int(_env("FIRE_DETECT_EVERY", "1")), int),
    "detect_fire": (True, bool),
    "detect_smoke": (True, bool),
    # alerting
    "min_consecutive": (int(_env("FIRE_MIN_CONSECUTIVE", "3")), int),
    "alert_cooldown": (float(_env("FIRE_ALERT_COOLDOWN", "30")), float),
    "save_clips": (True, bool),
    "clip_pre_seconds": (5.0, float),
    "clip_post_seconds": (10.0, float),
    # notifications
    "webhook_url": (_env("FIRE_WEBHOOK_URL", ""), str),
    "telegram_bot_token": (_env("FIRE_TELEGRAM_BOT_TOKEN", ""), str),
    "telegram_chat_id": (_env("FIRE_TELEGRAM_CHAT_ID", ""), str),
    "smtp_host": (_env("FIRE_SMTP_HOST", ""), str),
    "smtp_port": (int(_env("FIRE_SMTP_PORT", "587")), int),
    "smtp_user": (_env("FIRE_SMTP_USER", ""), str),
    "smtp_password": (_env("FIRE_SMTP_PASSWORD", ""), str),
    "smtp_from": (_env("FIRE_SMTP_FROM", ""), str),
    "smtp_to": (_env("FIRE_SMTP_TO", ""), str),
    "smtp_tls": (True, bool),
    # streams
    "loop_files": (_env("FIRE_LOOP_FILES", "true").lower() in {"1", "true", "yes"}, bool),
    "reconnect_delay": (float(_env("FIRE_RECONNECT_DELAY", "3")), float),
    "jpeg_quality": (int(_env("FIRE_JPEG_QUALITY", "80")), int),
    "stream_max_width": (int(_env("FIRE_STREAM_MAX_WIDTH", "1280")), int),
}

# keys that may be changed at runtime via the API (everything except weights/device which need a reload)
RUNTIME_KEYS = [k for k in SCHEMA if k not in {"weights", "device"}]
SECRET_KEYS = {"smtp_password", "telegram_bot_token"}


class Settings:
    host: str = _env("FIRE_HOST", "0.0.0.0")
    port: int = int(_env("FIRE_PORT", "8000"))
    data_dir: Path = DATA_DIR
    initial_sources: list[str] = [s.strip() for s in _env("FIRE_SOURCES", "").split(",") if s.strip()]

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: list = []
        for k, (default, _) in SCHEMA.items():
            setattr(self, k, default)
        for d in (self.snapshot_dir, self.recording_dir, self.clip_dir, self.upload_dir, self.job_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._load()

    # paths -----------------------------------------------------------------
    @property
    def snapshot_dir(self) -> Path: return self.data_dir / "snapshots"
    @property
    def recording_dir(self) -> Path: return self.data_dir / "recordings"
    @property
    def clip_dir(self) -> Path: return self.data_dir / "clips"
    @property
    def upload_dir(self) -> Path: return self.data_dir / "uploads"
    @property
    def job_dir(self) -> Path: return self.data_dir / "jobs"
    @property
    def sources_file(self) -> Path: return self.data_dir / "sources.json"
    @property
    def settings_file(self) -> Path: return self.data_dir / "settings.json"
    @property
    def events_file(self) -> Path: return self.data_dir / "events.jsonl"

    # persistence --------------------------------------------------------------
    def _load(self) -> None:
        if self.settings_file.exists():
            try:
                self._apply(json.loads(self.settings_file.read_text()))
            except Exception:
                pass

    def _apply(self, values: dict) -> dict:
        changed = {}
        for k, v in values.items():
            if k not in SCHEMA:
                continue
            typ = SCHEMA[k][1]
            try:
                if typ is bool and isinstance(v, str):
                    v = v.lower() in {"1", "true", "yes", "on"}
                v = typ(v)
            except (TypeError, ValueError):
                continue
            if k in {"detect_every", "min_consecutive"}:
                v = max(1, v)
            if getattr(self, k) != v:
                setattr(self, k, v)
                changed[k] = v
        return changed

    def update(self, values: dict) -> dict:
        with self._lock:
            changed = self._apply({k: v for k, v in values.items() if k in RUNTIME_KEYS})
            self.settings_file.write_text(json.dumps({k: getattr(self, k) for k in RUNTIME_KEYS}, indent=2))
        for fn in self._listeners:
            fn(changed)
        return changed

    def on_change(self, fn) -> None:
        self._listeners.append(fn)

    def to_dict(self, mask_secrets: bool = True) -> dict:
        d = {k: getattr(self, k) for k in SCHEMA}
        if mask_secrets:
            for k in SECRET_KEYS:
                if d[k]:
                    d[k] = "••••••" + d[k][-4:]
        d["host"], d["port"] = self.host, self.port
        return d


settings = Settings()

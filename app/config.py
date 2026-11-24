"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Must be set before OpenCV is imported anywhere: force TCP for RTSP so frames don't
# arrive corrupted/out of order over UDP, and cap the time we wait on a dead stream.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;5000000|rw_timeout;5000000|max_delay;500000|buffer_size;1048576",
)

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    weights: str = _env("FIRE_WEIGHTS", str(ROOT / "models" / "fire-smoke-yolov8n.pt"))
    device: str = _env("FIRE_DEVICE", "cpu")
    conf: float = float(_env("FIRE_CONF", "0.35"))
    iou: float = float(_env("FIRE_IOU", "0.5"))
    imgsz: int = int(_env("FIRE_IMGSZ", "640"))
    detect_every: int = max(1, int(_env("FIRE_DETECT_EVERY", "1")))
    min_consecutive: int = max(1, int(_env("FIRE_MIN_CONSECUTIVE", "3")))
    alert_cooldown: float = float(_env("FIRE_ALERT_COOLDOWN", "30"))
    webhook_url: str = _env("FIRE_WEBHOOK_URL", "").strip()
    loop_files: bool = _bool("FIRE_LOOP_FILES", True)
    host: str = _env("FIRE_HOST", "0.0.0.0")
    port: int = int(_env("FIRE_PORT", "8000"))
    reconnect_delay: float = float(_env("FIRE_RECONNECT_DELAY", "3"))
    jpeg_quality: int = int(_env("FIRE_JPEG_QUALITY", "80"))
    data_dir: Path = field(default_factory=lambda: Path(_env("FIRE_DATA_DIR", str(ROOT / "data"))))
    initial_sources: list[str] = field(
        default_factory=lambda: [s.strip() for s in _env("FIRE_SOURCES", "").split(",") if s.strip()]
    )

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def recording_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def sources_file(self) -> Path:
        return ROOT / "sources.json"


settings = Settings()
for _d in (settings.snapshot_dir, settings.recording_dir):
    _d.mkdir(parents=True, exist_ok=True)

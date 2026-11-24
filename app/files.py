"""Server-side file browser + uploads so sources can be picked from the UI."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from .config import VIDEO_EXTS, Settings


def browse(path: str = "") -> dict:
    p = Path(path).expanduser() if path else Path.home()
    if not p.exists():
        raise FileNotFoundError(path)
    if p.is_file():
        p = p.parent
    p = p.resolve()
    dirs, files = [], []
    try:
        for entry in sorted(p.iterdir(), key=lambda e: e.name.lower()):
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    dirs.append({"name": entry.name, "path": str(entry)})
                elif entry.suffix.lower() in VIDEO_EXTS:
                    st = entry.stat()
                    files.append({"name": entry.name, "path": str(entry), "size": st.st_size, "mtime": st.st_mtime})
            except OSError:
                continue
    except PermissionError:
        pass
    return {
        "path": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": dirs, "files": files,
        "video_count": len(files),
        "shortcuts": [{"name": n, "path": str(q)} for n, q in (("Home", Path.home()), ("Root", Path("/")))
                      if q.exists()],
    }


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(name)).strip("._") or "upload"
    return f"{time.strftime('%Y%m%dT%H%M%S')}_{name}"


def list_media(settings: Settings) -> list[dict]:
    out = []
    for kind, d in (("recording", settings.recording_dir), ("clip", settings.clip_dir), ("job", settings.job_dir)):
        for f in d.glob("*.mp4"):
            st = f.stat()
            out.append({"name": f.name, "kind": kind, "size": st.st_size, "mtime": st.st_mtime,
                        "url": f"/media/{kind}s/{f.name}", "source_id": f.stem.split("_")[1] if "_" in f.stem else None})
    out.sort(key=lambda m: m["mtime"], reverse=True)
    return out

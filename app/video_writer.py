"""Browser-playable (H.264) MP4 writer via an ffmpeg pipe, with an OpenCV fallback."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)
FFMPEG = shutil.which("ffmpeg")


class VideoWriter:
    def __init__(self, path: Path, fps: float, size: tuple[int, int]):
        self.path = Path(path)
        self.size = size
        self.frames = 0
        self._proc = None
        self._cv = None
        w, h = size
        if FFMPEG:
            cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps:.3f}", "-i", "-",
                   "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                   "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-movflags", "+faststart", str(self.path)]
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        else:
            self._cv = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)

    def write(self, frame: np.ndarray) -> None:
        if (frame.shape[1], frame.shape[0]) != self.size:
            frame = cv2.resize(frame, self.size)
        try:
            if self._proc:
                self._proc.stdin.write(frame.tobytes())
            else:
                self._cv.write(frame)
            self.frames += 1
        except (BrokenPipeError, OSError) as e:
            log.error("writer %s failed: %s", self.path.name, e)
            self.close()

    def close(self) -> None:
        if self._proc:
            proc, self._proc = self._proc, None
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
                try:
                    rc = proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = proc.wait()
                err = proc.stderr.read() if proc.stderr else b""
                if rc and err:
                    log.error("ffmpeg (%s): %s", self.path.name, err.decode(errors="ignore")[-400:])
            except Exception as e:
                log.error("closing writer %s: %s", self.path.name, e)
        if self._cv:
            self._cv.release()
            self._cv = None

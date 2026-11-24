"""Threaded video source reader with automatic reconnection.

Accepts anything OpenCV/FFmpeg can open: rtsp://, rtmp://, http(s):// (HLS, MJPEG, MP4),
local file paths, or an integer webcam index. Always keeps only the *latest* frame so
the consumer stays real-time even when inference is slower than the source frame rate.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

STREAM_SCHEMES = ("rtsp://", "rtsps://", "rtmp://", "rtmps://", "http://", "https://", "udp://", "tcp://", "srt://")


def is_live_source(source: str) -> bool:
    s = source.strip().lower()
    return s.isdigit() or s.startswith(STREAM_SCHEMES)


def _open(source: str) -> cv2.VideoCapture:
    if source.strip().isdigit():
        return cv2.VideoCapture(int(source))
    return cv2.VideoCapture(source, cv2.CAP_FFMPEG)


class StreamReader:
    def __init__(self, source: str, reconnect_delay: float = 3.0, loop_files: bool = True):
        self.source = source
        self.reconnect_delay = reconnect_delay
        self.loop_files = loop_files
        self.is_live = is_live_source(source)

        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._frame_id = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.connected = False
        self.last_error: str = ""
        self.fps: float = 0.0  # source fps as reported by the container, if any
        self.width = 0
        self.height = 0
        self.ended = False  # non-looping file reached EOF

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> "StreamReader":
        self._thread = threading.Thread(target=self._run, name=f"reader:{self.source}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    # -- consumer API --------------------------------------------------------
    def latest(self, after_id: int = -1):
        """Return (frame_id, frame) if a frame newer than after_id exists, else (after_id, None)."""
        with self._lock:
            if self._frame is None or self._frame_id == after_id:
                return after_id, None
            return self._frame_id, self._frame

    # -- reader thread -------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            cap = _open(self.source)
            if not cap.isOpened():
                self.connected = False
                self.last_error = "failed to open source"
                log.warning("[%s] %s, retrying in %.0fs", self.source, self.last_error, self.reconnect_delay)
                cap.release()
                self._stop.wait(self.reconnect_delay)
                continue

            self.fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self.connected = True
            self.last_error = ""
            log.info("[%s] opened %dx%d @ %.1f fps (live=%s)", self.source, self.width, self.height, self.fps, self.is_live)

            # Local files are paced to their native frame rate so they behave like a live feed.
            frame_interval = (1.0 / self.fps) if (not self.is_live and 0 < self.fps < 240) else 0.0
            next_due = time.monotonic()
            failures = 0

            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    if not self.is_live:
                        if self.loop_files:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            ok, frame = cap.read()
                            if ok and frame is not None:
                                self._publish(frame)
                            continue
                        self.ended = True
                        self.connected = False
                        log.info("[%s] end of file", self.source)
                        cap.release()
                        return
                    failures += 1
                    if failures > 30:
                        self.last_error = "stream stalled"
                        break
                    time.sleep(0.05)
                    continue
                failures = 0
                self._publish(frame)

                if frame_interval:
                    next_due += frame_interval
                    delay = next_due - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    elif delay < -1.0:  # fell far behind; resync instead of racing
                        next_due = time.monotonic()

            cap.release()
            self.connected = False
            if not self._stop.is_set():
                log.warning("[%s] disconnected (%s), reconnecting in %.0fs", self.source, self.last_error, self.reconnect_delay)
                self._stop.wait(self.reconnect_delay)

    def _publish(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame
            self._frame_id += 1
